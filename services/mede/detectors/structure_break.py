"""StructureBreakDetector（6.5 跌破微結構低點）— 跌破最近 swing low。

條件：已有 swing low → 現價跌破該 swing low 達門檻 → 跌破時 Trade Imbalance 偏空、
成交速度放大、賣方 OFI/MLOFI 增強、買一掛單偏弱。方向偏空(−1)。
狀態：記住已回報過的跌破價位，只在「新鮮跌破」觸發。
"""

from __future__ import annotations

from services.mede.detectors.base import Detector, DetectorResult, clamp


class StructureBreakDetector(Detector):
    name = "structure_break"

    def __init__(self, cfg):
        super().__init__(cfg)
        self._broken_level = 0.0

    def update(self, snap) -> DetectorResult:
        sl = snap.swing_low
        ts = snap.tick_size
        price = snap.last_price
        if sl <= 0 or ts <= 0 or price <= 0:
            return self._idle(swing_low=sl)
        below = (sl - price) / ts
        broke = below >= self.cfg.structure_break_min_ticks
        fresh = broke and abs(sl - self._broken_level) > 1e-9   # 新的 swing low 被跌破
        w = self._win(snap)
        imb = w.get("trade_imbalance", 0.0)
        ofi_bear = (snap.integrated_mlofi < 0) or (snap.ofi_l1 < 0)
        speeding = snap.price_velocity < 0.0
        triggered = bool(broke and imb <= -0.1 and (ofi_bear or speeding))
        if not triggered:
            return self._idle(swing_low=sl, below_ticks=round(below, 1))
        if fresh:
            self._broken_level = sl
        score = clamp(min(below / max(self.cfg.structure_break_min_ticks, 1e-9), 5.0) * 14
                      + min(abs(imb), 1.0) * 25 + (15 if ofi_bear else 0)
                      + (15 if fresh else 0))
        conf = clamp(0.3 + min(abs(imb), 1.0) * 0.3 + (0.2 if ofi_bear else 0.0)
                     + (0.2 if fresh else 0.0), 0.0, 1.0)
        reasons = [f"跌破微結構低點 {sl:g}（現低 {below:.0f} 跳）"
                   f"{'，OFI 偏空' if ofi_bear else ''}{'（新破）' if fresh else ''}"]
        return DetectorResult(self.name, -1, score, conf, True, reasons,
                              {"swing_low": sl, "below_ticks": round(below, 1),
                               "imbalance": round(imb, 3),
                               "integrated_mlofi": round(snap.integrated_mlofi, 1),
                               "fresh": fresh}, self.pv)
