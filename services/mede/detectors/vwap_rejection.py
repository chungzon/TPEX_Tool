"""VwapRejectionDetector（6.3 反彈不過 VWAP）— VWAP 下方反彈測試後遭壓回。

條件：股價已在 VWAP 下方 → 反彈逼近/短暫站上 VWAP → 無法站穩、於短時間內跌回 →
反彈量能弱、主動買衰退。方向偏空(−1)。狀態：追蹤「是否已在下方」「反彈是否測試過 VWAP」。
"""

from __future__ import annotations

from services.mede.detectors.base import Detector, DetectorResult, clamp


class VwapRejectionDetector(Detector):
    name = "vwap_rejection"

    def __init__(self, cfg):
        super().__init__(cfg)
        self._below = False
        self._tested = False
        self._rebound_high = 0.0

    def update(self, snap) -> DetectorResult:
        price = snap.last_price
        ts = snap.tick_size
        vwap = snap.vwap
        if price <= 0 or ts <= 0 or vwap <= 0:
            return self._idle()
        near = (vwap - price) / ts               # 距 VWAP 幾跳（正=在下方）
        if price < vwap:
            self._below = True
        # 反彈測試：曾在下方且逼近或站上 VWAP
        if self._below and (near <= self.cfg.vwap_reject_near_ticks):
            self._tested = True
            self._rebound_high = max(self._rebound_high, price)
        w = self._win(snap)
        imb = w.get("trade_imbalance", 0.0)
        # 壓回：測試過 + 現在重回 VWAP 下方 + 賣壓回升 + 轉弱
        rejected = (self._tested and price < vwap - self.cfg.vwap_break_min_ticks * ts
                    and imb <= 0.0 and snap.price_velocity <= 0.0)
        if not rejected:
            return self._idle(near_ticks=round(near, 1), tested=self._tested)
        depth = (vwap - price) / ts
        rej_from = (self._rebound_high - price) / ts
        # 觸發後重置測試狀態，等待下一次反彈
        self._tested = False
        self._rebound_high = 0.0
        score = clamp(30 + min(rej_from, 5.0) * 8 + min(abs(imb), 1.0) * 25)
        conf = clamp(0.35 + min(abs(imb), 1.0) * 0.35 + min(rej_from / 4.0, 1.0) * 0.3,
                     0.0, 1.0)
        reasons = [f"反彈不過 VWAP({vwap:g})，測到後壓回 {rej_from:.0f} 跳、"
                   f"現距 VWAP 下方 {depth:.0f} 跳"]
        return DetectorResult(self.name, -1, score, conf, True, reasons,
                              {"reject_from_ticks": round(rej_from, 1),
                               "depth_below_vwap": round(depth, 1),
                               "imbalance": round(imb, 3)}, self.pv)
