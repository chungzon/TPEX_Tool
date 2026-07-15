"""TradeBurstDetector — 單位時間成交筆數突然放大（z-score 基準，非固定 3 倍）。"""

from __future__ import annotations

from services.mede.detectors.base import Detector, DetectorResult, RollingZ, clamp


class TradeBurstDetector(Detector):
    name = "trade_burst"

    def __init__(self, cfg):
        super().__init__(cfg)
        self._z = RollingZ(cfg.baseline_window)
        self._ticks = 0

    def update(self, snap) -> DetectorResult:
        self._ticks += 1
        w = self._win(snap)
        cnt = w.get("buy_cnt", 0) + w.get("sell_cnt", 0)
        z = self._z.z(cnt)          # 先對「過去」算 z，再納入基準（避免自我膨脹）
        self._z.push(cnt)
        thr = self.cfg.trade_burst_zscore
        warm = (self._z.ready(self.cfg.minimum_warmup_ticks)
                and self._ticks >= self.cfg.minimum_warmup_ticks)
        imb = w.get("trade_imbalance", 0.0)
        direction = 1 if imb > 0.1 else (-1 if imb < -0.1 else 0)
        triggered = bool(warm and z >= thr)
        score = clamp(z / thr * 70) if warm else 0.0
        conf = clamp(z / (thr * 2), 0.0, 1.0) if warm else 0.0
        reasons = [f"成交筆數暴增 z={z:.1f}（≥{thr}）"] if triggered else []
        return DetectorResult(self.name, direction, score, conf, triggered, reasons,
                              {"trade_count": cnt, "zscore": round(z, 2),
                               "warm": warm}, self.pv)
