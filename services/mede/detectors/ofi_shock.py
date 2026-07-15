"""OFIShockDetector — OFI/MLOFI 突然偏向單一方向（z-score + 各檔一致性）。

需 BidAsk；只在有新五檔時更新 z 基準與判定，避免 tick 間重複值稀釋基準。
"""

from __future__ import annotations

from services.mede.detectors.base import Detector, DetectorResult, RollingZ, clamp


class OFIShockDetector(Detector):
    name = "ofi_shock"
    requires_bidask = True

    def __init__(self, cfg):
        super().__init__(cfg)
        self._z = RollingZ(cfg.baseline_window)
        self._last_ba = -1
        self._last = DetectorResult.idle(self.name, self.pv)

    def update(self, snap) -> DetectorResult:
        if not snap.bidask_available:
            return self._idle(reason="tick-only")
        if snap.bidask_updates == self._last_ba:
            return self._last
        self._last_ba = snap.bidask_updates

        ofi = snap.ofi_l1
        z = self._z.z(ofi)
        self._z.push(ofi)
        thr = self.cfg.ofi_shock_zscore
        warm = self._z.ready(self.cfg.minimum_warmup_ticks)
        ml = snap.mlofi or []
        sign = 1 if ofi > 0 else (-1 if ofi < 0 else 0)
        agree = (sum(1 for x in ml if x != 0 and (x > 0) == (ofi > 0))
                 / max(len(ml), 1)) if sign else 0.0
        direction = sign if (warm and abs(z) >= thr and agree >= 0.6) else 0
        triggered = direction != 0
        score = clamp(abs(z) / thr * 60 + agree * 40) if warm else 0.0
        conf = (clamp(min(abs(z) / (thr * 2), 1.0) * 0.6 + agree * 0.4, 0.0, 1.0)
                if warm else 0.0)
        reasons = ([f"OFI 衝擊 z={z:.1f}，MLOFI 一致 {agree:.0%}，"
                    f"方向{'多' if direction > 0 else '空'}"] if triggered else [])
        self._last = DetectorResult(
            self.name, direction, score, conf, triggered, reasons,
            {"ofi_l1": round(ofi, 1), "zscore": round(z, 2),
             "mlofi_agree": round(agree, 2),
             "integrated_mlofi": round(snap.integrated_mlofi, 1)}, self.pv)
        return self._last
