"""EventFusionEngine — 融合各偵測器結果 → 多/空加權分、否決、觸發候選。

第一版：Weighted Score + Rule Pattern（不做 LightGBM）。
輸出可解釋：bull/bear/absorption/exhaustion 分數、觸發理由、否決理由、命中 Pattern。
最終「狀態」由 state_machine 決定，本引擎只給「是否為觸發候選 + 方向 + 分數」。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

from services.mede.patterns import match_patterns


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return lo if x < lo else hi if x > hi else x

# 偵測器 → 證據類別（觸發需多類共振）
CATEGORY_MAP = {
    "trade_burst": "burst", "volume_burst": "burst",
    "aggressive_flow": "flow",
    "ofi_shock": "ofi_book", "book_imbalance_shift": "ofi_book",
    "queue_collapse": "liquidity", "liquidity_vacuum": "liquidity",
    "breakout": "breakout", "sweep": "breakout",
    "momentum_ignition": "momentum",
    # BED 空方結構（只在 direction<0 觸發，不影響多方類別）
    "rally_failure": "rally", "vwap_break": "vwap", "vwap_rejection": "vwap",
    "lower_high": "swing", "structure_break": "swing",
    "directional_efficiency": "efficiency",
}

# 偵測器 → BED 三大評分群（結構/成交/委託簿），供空方分數細分（規劃 §八）
GROUP_MAP = {
    # 結構
    "rally_failure": "structure", "vwap_break": "structure",
    "vwap_rejection": "structure", "lower_high": "structure",
    "structure_break": "structure", "breakout": "structure",
    "failed_breakout": "structure",
    # 成交
    "trade_burst": "trade", "volume_burst": "trade", "aggressive_flow": "trade",
    "directional_efficiency": "trade", "momentum_ignition": "trade",
    # 委託簿
    "ofi_shock": "orderbook", "book_imbalance_shift": "orderbook",
    "queue_collapse": "orderbook", "liquidity_vacuum": "orderbook",
    "replenishment": "orderbook", "sweep": "orderbook", "absorption": "orderbook",
}


@dataclass
class FusionResult:
    t_ns: int
    seq: int
    code: str
    last_price: float
    direction: int                 # +1/-1/0
    candidate: bool                # 是否達觸發候選
    bull_score: float
    bear_score: float
    absorption_score: float
    exhaustion_score: float
    bull_categories: int
    bear_categories: int
    # BED 空方分數細分（規劃 §八）
    structure_score: float = 0.0
    trade_score: float = 0.0
    orderbook_score: float = 0.0
    veto_score: float = 0.0
    final_bear_score: float = 0.0      # 0~100（分數帶用）
    final_bull_score: float = 0.0
    trigger_reasons: list = field(default_factory=list)
    veto_reasons: list = field(default_factory=list)
    matched_patterns: list = field(default_factory=list)
    detector_scores: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


class FusionEngine:
    def __init__(self, cfg):
        self.cfg = cfg

    def evaluate(self, snap, results: dict, data_ok: bool = True,
                 cooldown: bool = False) -> FusionResult:
        cfg = self.cfg
        w = cfg.detector_weights or {}
        bull = bear = 0.0
        bull_cats, bear_cats = set(), set()
        groups = {"structure": 0.0, "trade": 0.0, "orderbook": 0.0}  # 空方細分
        dscores = {}
        for name, r in results.items():
            dscores[name] = {"dir": r.direction, "score": round(r.score, 1),
                             "trig": r.is_triggered}
            if not r.is_triggered:
                continue
            wt = w.get(name, 1.0)
            cat = CATEGORY_MAP.get(name)
            if r.direction > 0:
                bull += wt * r.score
                if cat:
                    bull_cats.add(cat)
            elif r.direction < 0:
                bear += wt * r.score
                if cat:
                    bear_cats.add(cat)
                g = GROUP_MAP.get(name)
                if g:
                    groups[g] += wt * r.score

        absr = results.get("absorption")
        exhr = results.get("exhaustion")
        repl = results.get("replenishment")
        fb = results.get("failed_breakout")
        absorption_score = absr.score if (absr and absr.is_triggered) else 0.0
        exhaustion_score = exhr.score if (exhr and exhr.is_triggered) else 0.0

        spread_ok = (not snap.bidask_available
                     or snap.spread_ticks <= cfg.spread_max_ticks)
        necessary = data_ok and spread_ok and not cooldown

        veto_bull, veto_bear = [], []
        if not data_ok:
            veto_bull.append("資料品質不合格"); veto_bear.append("資料品質不合格")
        if not spread_ok:
            veto_bull.append("點差異常擴大"); veto_bear.append("點差異常擴大")
        if cooldown:
            veto_bull.append("冷卻期"); veto_bear.append("冷卻期")
        if absr and absr.is_triggered and absr.score >= cfg.veto_absorption_score:
            (veto_bull if absr.direction < 0 else veto_bear).append(
                "賣方吸收" if absr.direction < 0 else "買方吸收")
        if repl and repl.is_triggered:
            (veto_bull if repl.direction < 0 else veto_bear).append(
                "賣方補單/壓盤" if repl.direction < 0 else "買方補單/支撐")
        if fb and fb.is_triggered:
            (veto_bull if fb.direction < 0 else veto_bear).append(
                "向上假突破" if fb.direction < 0 else "向下假突破")
        # BED 空方專屬否決（規劃 §八）：站回 VWAP / 已在 VWAP 下方過遠
        if snap.vwap > 0 and snap.tick_size > 0:
            if snap.last_price >= snap.vwap:
                veto_bear.append("反彈站回 VWAP")
            elif (snap.vwap - snap.last_price) / snap.tick_size >= cfg.veto_far_from_vwap_ticks:
                veto_bear.append("已遠離 VWAP（過度延伸）")
        # 註：大盤急拉 / 接近跌停 / 重要支撐過近 目前無資料源，暫未實作（見 BED Phase 1 已知限制）

        bull_ok = (necessary and bull >= cfg.bull_trigger_score
                   and len(bull_cats) >= cfg.trigger_min_detector_categories
                   and not veto_bull)
        bear_ok = (necessary and bear >= cfg.bear_trigger_score
                   and len(bear_cats) >= cfg.trigger_min_detector_categories
                   and not veto_bear)

        direction, candidate = 0, False
        if bull_ok and bull >= bear:
            direction, candidate = 1, True
        elif bear_ok:
            direction, candidate = -1, True

        # --- BED 正規化分數（0~100，對齊分數帶）---
        def _norm(raw, thr):
            return _clamp(raw / thr * 75.0) if thr > 0 else 0.0

        final_bull = _norm(bull, cfg.bull_trigger_score)
        final_bear = _norm(bear, cfg.bear_trigger_score)
        veto_score = _clamp(25.0 * len(veto_bear))
        if veto_bear:
            final_bear = min(final_bear, cfg.bed_candidate_score - 1.0)  # 否決 → 壓到觸發帶以下

        patterns = match_patterns(results, snap, cfg) if necessary else []
        reasons = []
        if candidate:
            cats = bull_cats if direction > 0 else bear_cats
            sc = bull if direction > 0 else bear
            reasons.append(f"{'多方' if direction > 0 else '空方'}共振 {len(cats)} 類"
                           f"（{'/'.join(sorted(cats))}），加權分 {sc:.0f}")
            reasons += [r.reasons[0] for n, r in results.items()
                        if r.is_triggered and r.direction == direction and r.reasons][:4]
            reasons += [f"命中型態：{p}" for p, pd in patterns if pd == direction]

        return FusionResult(
            t_ns=snap.t_ns, seq=snap.seq, code=snap.code, last_price=snap.last_price,
            direction=direction, candidate=candidate,
            bull_score=round(bull, 1), bear_score=round(bear, 1),
            absorption_score=round(absorption_score, 1),
            exhaustion_score=round(exhaustion_score, 1),
            bull_categories=len(bull_cats), bear_categories=len(bear_cats),
            structure_score=round(groups["structure"], 1),
            trade_score=round(groups["trade"], 1),
            orderbook_score=round(groups["orderbook"], 1),
            veto_score=round(veto_score, 1),
            final_bear_score=round(final_bear, 1),
            final_bull_score=round(final_bull, 1),
            trigger_reasons=reasons,
            veto_reasons=(veto_bull if direction >= 0 else veto_bear),
            matched_patterns=[p for p, _ in patterns], detector_scores=dscores)
