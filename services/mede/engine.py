"""MedeEngine — 單股編排器：FeatureEngine → Detectors → Fusion → StateMachine。

即時與重播共用同一顆(決定性)。每筆成交產生 snapshot → 跑偵測器 → 融合 →
狀態機，觸發時輸出 Event。不直接下單。
"""

from __future__ import annotations

from services.mede.feature_engine import FeatureEngine
from services.mede.detectors import build_detectors
from services.mede.fusion import FusionEngine
from services.mede.state_machine import EventStateMachine
from services.mede.enums import StateType


class MedeEngine:
    def __init__(self, code: str, cfg, tick_size_fn,
                 on_event=None, on_state=None):
        self.code = code
        self.cfg = cfg
        self.fe = FeatureEngine(
            tick_size_fn, breakout_lookback_ticks=cfg.breakout_lookback_ticks,
            velocity_window_ms=cfg.velocity_window_ms,
            swing_reversal_ticks=cfg.swing_reversal_ticks,
            swing_confirm_ticks=cfg.swing_confirm_ticks,
            vwap_slope_window_ms=cfg.vwap_slope_window_ms)
        self.detectors = build_detectors(cfg)
        self.fusion = FusionEngine(cfg)
        self.sm = EventStateMachine(code, cfg)
        self.on_event = on_event
        self.on_state = on_state
        self.events: list = []
        self.state = StateType.IDLE
        self.last_fusion = None

    def on_bidask(self, ba: dict) -> None:
        self.fe.on_bidask(ba)

    def on_tick(self, tick: dict, data_ok: bool = True):
        self.fe.on_tick(tick)
        snap = self.fe.snapshot()
        results = {d.name: d.update(snap) for d in self.detectors}
        # 冷卻由狀態機把關（含反向突破冷卻），不在 fusion 重複否決，
        # 否則冷卻期 fusion.candidate 永遠 False → 反向發動無法被狀態機偵測。
        fusion = self.fusion.evaluate(snap, results, data_ok=data_ok)
        self.last_fusion = fusion
        state, ev = self.sm.update(fusion, results, snap, data_ok=data_ok)
        self.state = state
        if ev is not None:
            self.events.append(ev)
            if self.on_event:
                self.on_event(ev)
        if self.on_state:
            self.on_state(state, fusion, snap)
        return snap, fusion, state, ev
