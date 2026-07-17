"""LowerHighDetector（6.4 Lower High）— 反彈高點逐次降低。

用 FeatureEngine 的因果式 swing：本次已確認 swing high 低於前一個 swing high 達門檻，
且現價已在該 swing high 之下（正在轉弱）。方向偏空(−1)。
Swing 由確認延遲產生 → 不使用未來大量資料。
"""

from __future__ import annotations

from services.mede.detectors.base import Detector, DetectorResult, clamp


class LowerHighDetector(Detector):
    name = "lower_high"

    def update(self, snap) -> DetectorResult:
        sh = snap.swing_high
        psh = snap.prev_swing_high
        ts = snap.tick_size
        if sh <= 0 or psh <= 0 or ts <= 0:
            return self._idle(swing_high=sh, prev_swing_high=psh)
        drop = (psh - sh) / ts
        triggered = bool(drop >= self.cfg.lower_high_min_ticks
                         and snap.last_price <= sh)
        if not triggered:
            return self._idle(swing_high=sh, prev_swing_high=psh,
                              lower_high_ticks=round(drop, 1))
        w = self._win(snap)
        imb = w.get("trade_imbalance", 0.0)
        score = clamp(35 + min(drop, 8.0) * 6 + (15 if imb < 0 else 0))
        conf = clamp(0.35 + min(drop / 6.0, 1.0) * 0.4 + (0.15 if imb < 0 else 0.0),
                     0.0, 1.0)
        reasons = [f"Lower High：反彈高 {sh:g} < 前高 {psh:g}（低 {drop:.0f} 跳）"]
        return DetectorResult(self.name, -1, score, conf, True, reasons,
                              {"swing_high": sh, "prev_swing_high": psh,
                               "lower_high_ticks": round(drop, 1)}, self.pv)
