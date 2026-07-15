"""VolumeBurstDetector — 成交量突然放大（z-score）+ 是否集中單一方向。"""

from __future__ import annotations

from services.mede.detectors.base import Detector, DetectorResult, RollingZ, clamp


class VolumeBurstDetector(Detector):
    name = "volume_burst"

    def __init__(self, cfg):
        super().__init__(cfg)
        self._z = RollingZ(cfg.baseline_window)
        self._ticks = 0

    def update(self, snap) -> DetectorResult:
        self._ticks += 1
        w = self._win(snap)
        vol = w.get("buy_vol", 0.0) + w.get("sell_vol", 0.0)
        z = self._z.z(vol)
        self._z.push(vol)
        thr = self.cfg.volume_burst_zscore
        warm = (self._z.ready(self.cfg.minimum_warmup_ticks)
                and self._ticks >= self.cfg.minimum_warmup_ticks)
        imb = w.get("trade_imbalance", 0.0)
        conc = abs(imb)                 # 方向集中度
        direction = 1 if imb > 0.1 else (-1 if imb < -0.1 else 0)
        triggered = bool(warm and z >= thr)
        score = clamp(z / thr * 70) if warm else 0.0
        conf = clamp((z / (thr * 2)) * 0.7 + conc * 0.3, 0.0, 1.0) if warm else 0.0
        reasons = ([f"成交量暴增 z={z:.1f}，方向集中度 {conc:.2f}"]
                   if triggered else [])
        return DetectorResult(self.name, direction, score, conf, triggered, reasons,
                              {"volume": vol, "zscore": round(z, 2),
                               "concentration": round(conc, 2)}, self.pv)
