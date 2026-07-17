"""每股獨立事件狀態機 + 事件/轉移產生。

IDLE→WATCH→(BULL/BEAR)_TRIGGER→CONTINUATION→EXHAUSTION/FAILED→COOLDOWN→IDLE。
觸發時產生 Event（event_id 用 code-seq，決定性）；同波同向於 merge 窗內不重覆發事件；
冷卻期不發訊號；每股每日事件數上限。所有轉移記 from/to/time/reason/reference_price。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

from services.mede.enums import StateType


@dataclass
class Event:
    event_id: str
    code: str
    event_time_ns: int
    seq: int
    event_type: str
    direction: int
    score: float
    confidence: float
    trigger_price: float
    reasons: list
    matched_patterns: list
    detector_scores: dict
    consolidated_trigger_id: str
    parameter_version: str
    algorithm_version: str
    # BED 空方分數細分（規劃 §八）+ 否決理由落地
    vwap: float = 0.0
    structure_score: float = 0.0
    trade_score: float = 0.0
    orderbook_score: float = 0.0
    veto_score: float = 0.0
    final_score: float = 0.0
    veto_reasons: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Transition:
    code: str
    from_state: str
    to_state: str
    t_ns: int
    reason: str
    reference_price: float


class EventStateMachine:
    def __init__(self, code: str, cfg):
        self.code = code
        self.cfg = cfg
        self.state = StateType.IDLE
        self._enter_t = 0
        self._last_trigger_t = None
        self._last_dir = 0
        self._event_count = 0
        self.transitions: list = []

    def _set(self, new: StateType, t: int, reason: str, ref: float):
        if new != self.state:
            self.transitions.append(Transition(self.code, self.state.value,
                                               new.value, t, reason, ref))
            self.state = new
            self._enter_t = t

    def _move_dir(self) -> int:
        if self.state in (StateType.BULL_TRIGGER, StateType.BULL_CONTINUATION):
            return 1
        if self.state in (StateType.BEAR_TRIGGER, StateType.BEAR_CONTINUATION):
            return -1
        return 0

    def update(self, fusion, results: dict, snap, data_ok: bool = True):
        """回傳 (state, event|None)。"""
        t = snap.t_ns
        ref = snap.last_price
        if not data_ok:
            self._set(StateType.DATA_INVALID, t, "資料不合格", ref)
            return self.state, None
        if self.state == StateType.DATA_INVALID:
            self._set(StateType.IDLE, t, "資料恢復", ref)
        # 冷卻：正常等滿冷卻期；但「反向」強候選可突破冷卻（防同波重複，非擋真實反轉）
        if self.state == StateType.COOLDOWN:
            flip = fusion.candidate and self._last_dir != 0 \
                and fusion.direction == -self._last_dir
            if t - self._enter_t >= self.cfg.cooldown_ms * 1_000_000:
                self._set(StateType.IDLE, t, "冷卻結束", ref)
            elif flip:
                self._set(StateType.IDLE, t, "反向發動突破冷卻", ref)
            else:
                return self.state, None

        move = self._move_dir()
        # ---- 持倉狀態：出場/延續 ----
        if move != 0:
            exh = results.get("exhaustion")
            fb = results.get("failed_breakout")
            if exh and exh.is_triggered and exh.direction == -move:
                et = StateType.EXHAUSTION_UP if move > 0 else StateType.EXHAUSTION_DOWN
                ev = self._emit(snap, fusion, results, et, -move, "動能衰竭")
                self._set(et, t, "動能衰竭", ref)
                self._set(StateType.COOLDOWN, t, "衰竭→冷卻", ref)
                return self.state, ev
            if fb and fb.is_triggered and fb.direction == -move:
                ft = StateType.FAILED_BREAKOUT_UP if move > 0 else StateType.FAILED_BREAKOUT_DOWN
                ev = self._emit(snap, fusion, results, ft, -move, "假突破反向")
                self._set(ft, t, "假突破", ref)
                self._set(StateType.COOLDOWN, t, "假突破→冷卻", ref)
                return self.state, ev
            cont = fusion.bull_score if move > 0 else fusion.bear_score
            if fusion.direction == move and cont >= self.cfg.continuation_min_score:
                nc = StateType.BULL_CONTINUATION if move > 0 else StateType.BEAR_CONTINUATION
                self._set(nc, t, "延續", ref)
            else:
                self._set(StateType.COOLDOWN, t, "動能消退→冷卻", ref)
            return self.state, None

        # ---- IDLE / WATCH ----
        if fusion.candidate:
            d = fusion.direction
            merged = (self._last_trigger_t is not None and d == self._last_dir
                      and (t - self._last_trigger_t) < self.cfg.merge_window_ms * 1_000_000)
            tstate = StateType.BULL_TRIGGER if d > 0 else StateType.BEAR_TRIGGER
            ev = None if merged else self._emit(snap, fusion, results, tstate, d, "觸發")
            self._last_trigger_t = t
            self._last_dir = d
            cont = StateType.BULL_CONTINUATION if d > 0 else StateType.BEAR_CONTINUATION
            self._set(cont, t, "觸發→延續" if not merged else "同波合併", ref)
            return self.state, ev

        # ---- CANDIDATE：分數進入候選帶但未達觸發（規劃 §五）----
        band = max(fusion.final_bear_score, fusion.final_bull_score)
        if band >= self.cfg.bed_candidate_score:
            if self.state != StateType.CANDIDATE:
                self._set(StateType.CANDIDATE, t, "分數進入候選帶", ref)
            return self.state, None

        any_trig = any(r.is_triggered for r in results.values())
        if any_trig or band >= self.cfg.bed_watch_score:
            if self.state != StateType.WATCH:
                self._set(StateType.WATCH, t, "出現異常", ref)
            return self.state, None
        # 安靜
        if self.state in (StateType.WATCH, StateType.CANDIDATE):
            if t - self._enter_t >= self.cfg.merge_window_ms * 1_000_000:
                self._set(StateType.IDLE, t, "回歸平靜", ref)
        elif self.state != StateType.IDLE:
            self._set(StateType.IDLE, t, "平靜", ref)
        return self.state, None

    def _emit(self, snap, fusion, results, etype: StateType, direction: int,
              tag: str) -> Event | None:
        if self._event_count >= self.cfg.max_events_per_stock_per_day:
            return None
        self._event_count += 1
        eid = f"{self.code}-{snap.seq}"
        raw = fusion.bull_score if direction > 0 else fusion.bear_score
        thr = self.cfg.bull_trigger_score if direction > 0 else self.cfg.bear_trigger_score
        conf = min(raw / max(thr * 2, 1e-9), 1.0)
        reasons = list(fusion.trigger_reasons) or [tag]
        final = fusion.final_bull_score if direction > 0 else fusion.final_bear_score
        return Event(
            event_id=eid, code=self.code, event_time_ns=snap.t_ns, seq=snap.seq,
            event_type=etype.value, direction=direction, score=round(raw, 1),
            confidence=round(conf, 3), trigger_price=snap.last_price,
            reasons=reasons, matched_patterns=list(fusion.matched_patterns),
            detector_scores=fusion.detector_scores, consolidated_trigger_id=eid,
            parameter_version=self.cfg.parameter_version,
            algorithm_version=self.cfg.algorithm_version,
            vwap=round(snap.vwap, 4),
            structure_score=fusion.structure_score, trade_score=fusion.trade_score,
            orderbook_score=fusion.orderbook_score, veto_score=fusion.veto_score,
            final_score=final, veto_reasons=list(fusion.veto_reasons))
