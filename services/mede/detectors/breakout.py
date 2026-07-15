"""BreakoutDetector — 價格突破近 N 筆高低（+ 主動流確認 + VWAP 過濾）。

candidate：現價越過前高/前低；confirmed：候選 + 主動流同向。is_triggered=confirmed。
"""

from __future__ import annotations

from services.mede.detectors.base import Detector, DetectorResult, clamp


class BreakoutDetector(Detector):
    name = "breakout"

    def update(self, snap) -> DetectorResult:
        price, hi, lo = snap.last_price, snap.recent_high, snap.recent_low
        if price <= 0 or hi <= 0:
            return self._idle()
        w = self._win(snap)
        imb = w.get("trade_imbalance", 0.0)
        direction = 1 if price > hi else (-1 if price < lo else 0)
        if direction == 0:
            return self._idle(recent_high=hi, recent_low=lo)
        confirmed = (imb > 0.2) if direction > 0 else (imb < -0.2)
        above_vwap = (price >= snap.vwap) if direction > 0 else (price <= snap.vwap)
        score = clamp(45 + (30 if confirmed else 0) + (15 if above_vwap else 0)
                      + min(abs(imb), 1.0) * 10)
        conf = clamp((0.5 if confirmed else 0.25) + (0.25 if above_vwap else 0.0)
                     + min(abs(imb), 1.0) * 0.25, 0.0, 1.0)
        ref = hi if direction > 0 else lo
        reasons = [f"{'向上' if direction > 0 else '向下'}突破近{self.cfg.breakout_lookback_ticks}筆"
                   f"{'高' if direction > 0 else '低'}({ref:g})"
                   f"{' + 主動流確認' if confirmed else '（候選）'}"]
        return DetectorResult(self.name, direction, score, conf, bool(confirmed),
                              reasons, {"recent_high": hi, "recent_low": lo,
                                        "candidate": True, "confirmed": confirmed,
                                        "vwap": snap.vwap, "imbalance": round(imb, 3)},
                              self.pv)
