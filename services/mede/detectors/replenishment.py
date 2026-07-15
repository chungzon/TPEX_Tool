"""ReplenishmentDetector — 同價位反覆補單（累積消耗 ≫ 顯示量，價未穿越）。

輸出「疑似補單／隱藏流動性」，**不宣稱真正 Iceberg**。
買方補單/隱藏買 → 支撐（+1）；賣方補單/隱藏賣 → 壓盤（-1）。需 BidAsk。
"""

from __future__ import annotations

from services.mede.detectors.base import Detector, DetectorResult, clamp


class ReplenishmentDetector(Detector):
    name = "replenishment"
    requires_bidask = True

    def __init__(self, cfg):
        super().__init__(cfg)
        self._last_ba = -1
        self._last = DetectorResult.idle(self.name, self.pv)

    def update(self, snap) -> DetectorResult:
        if not snap.bidask_available:
            return self._idle(reason="tick-only")
        if snap.bidask_updates == self._last_ba:
            return self._last
        self._last_ba = snap.bidask_updates

        thr = self.cfg.replenishment_ratio
        br, ar = snap.bid_repl_ratio, snap.ask_repl_ratio
        direction, ratio = 0, 0.0
        if br >= thr and br >= ar:
            direction, ratio = 1, br
        elif ar >= thr and ar > br:
            direction, ratio = -1, ar
        triggered = direction != 0
        score = clamp(min(ratio / thr, 3.0) * 45) if triggered else 0.0
        conf = clamp(min(ratio / (thr * 2), 1.0) * 0.8, 0.0, 1.0) if triggered else 0.0
        reasons = ([f"疑似{'買方' if direction > 0 else '賣方'}補單/隱藏流動性"
                    f"（累積消耗/顯示 {ratio:.1f}×）"] if triggered else [])
        self._last = DetectorResult(
            self.name, direction, score, conf, triggered, reasons,
            {"bid_repl_ratio": br, "ask_repl_ratio": ar}, self.pv)
        return self._last
