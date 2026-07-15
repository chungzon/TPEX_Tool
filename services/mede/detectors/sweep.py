"""SweepDetector — 短時間內成交快速跨越 ≥N 個價位（掃單），且主動流同向。"""

from __future__ import annotations

from services.mede.detectors.base import Detector, DetectorResult, clamp


class SweepDetector(Detector):
    name = "sweep"

    def update(self, snap) -> DetectorResult:
        rng = snap.price_range_ticks
        vel = snap.price_velocity
        w = self._win(snap)
        imb = w.get("trade_imbalance", 0.0)
        min_ticks = self.cfg.sweep_min_ticks
        if rng < min_ticks:
            return self._idle(range_ticks=rng)
        direction = 1 if vel > 0 else (-1 if vel < 0 else 0)
        flow_ok = (imb > 0.3) if direction > 0 else (imb < -0.3)
        # 對應方向的五檔被吃：up→ask_consumed、down→bid_consumed
        consumed = snap.ask_consumed if direction > 0 else snap.bid_consumed
        triggered = bool(direction != 0 and flow_ok)
        score = clamp(rng / min_ticks * 45 + min(abs(imb), 1.0) * 45)
        conf = clamp(min(rng / min_ticks, 2.0) / 2 * 0.5 + min(abs(imb), 1.0) * 0.3
                     + (0.2 if consumed > 0 else 0.0), 0.0, 1.0)
        reasons = ([f"{'向上' if direction > 0 else '向下'}掃單 {rng:.0f} 跳"
                    f"（主動流 {imb:+.2f}）"] if triggered else [])
        return DetectorResult(self.name, direction, score, conf, triggered, reasons,
                              {"range_ticks": rng, "velocity": vel,
                               "consumed": round(consumed, 0),
                               "imbalance": round(imb, 3)}, self.pv)
