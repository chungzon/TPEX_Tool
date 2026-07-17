"""VwapBreakDetector（6.2 跌破 VWAP）— 價格由 VWAP 上方跌破至下方。

條件：收在 VWAP 下方達門檻、跌破時主動賣增/成交速度放大、VWAP 斜率不再向上、
跌破後無法快速站回。方向偏空(−1)。狀態：記住前一筆在 VWAP 上/下方以辨識「新鮮跌破」。
"""

from __future__ import annotations

from services.mede.detectors.base import Detector, DetectorResult, clamp


class VwapBreakDetector(Detector):
    name = "vwap_break"

    def __init__(self, cfg):
        super().__init__(cfg)
        self._was_above = True

    def update(self, snap) -> DetectorResult:
        price = snap.last_price
        ts = snap.tick_size
        vwap = snap.vwap
        if price <= 0 or ts <= 0 or vwap <= 0:
            return self._idle()
        dist = (vwap - price) / ts               # 在 VWAP 下方幾跳
        was_above = self._was_above
        self._was_above = price >= vwap
        w = self._win(snap)
        imb = w.get("trade_imbalance", 0.0)
        below = dist >= self.cfg.vwap_break_min_ticks
        sell_flow = imb <= -0.1
        slope_down = snap.vwap_slope <= 0.0
        speeding = snap.price_velocity < 0.0
        triggered = bool(below and sell_flow and slope_down)
        if not triggered:
            return self._idle(dist_below_vwap=round(dist, 1))
        fresh = was_above                        # 這筆才剛跌破
        score = clamp(min(dist / max(self.cfg.vwap_break_min_ticks, 1e-9), 4.0) * 18
                      + min(abs(imb), 1.0) * 25 + (20 if fresh else 0)
                      + (15 if speeding else 0))
        conf = clamp(0.3 + min(abs(imb), 1.0) * 0.3 + (0.2 if fresh else 0.0)
                     + (0.2 if speeding else 0.0), 0.0, 1.0)
        reasons = [f"跌破 VWAP({vwap:g})，現距 {dist:.0f} 跳"
                   f"{'（剛跌破）' if fresh else ''}，VWAP 斜率{snap.vwap_slope:+.3f}"]
        return DetectorResult(self.name, -1, score, conf, True, reasons,
                              {"dist_below_vwap": round(dist, 1),
                               "vwap_slope": snap.vwap_slope,
                               "imbalance": round(imb, 3), "fresh": fresh}, self.pv)
