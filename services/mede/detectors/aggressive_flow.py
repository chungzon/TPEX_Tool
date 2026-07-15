"""AggressiveFlowDetector — 主動買/賣是否形成連續性（多時間窗方向一致）。"""

from __future__ import annotations

from services.mede.detectors.base import Detector, DetectorResult, clamp


class AggressiveFlowDetector(Detector):
    name = "aggressive_flow"

    def update(self, snap) -> DetectorResult:
        tws = snap.time_windows
        if not tws:
            return self._idle()
        imbs = [w.get("trade_imbalance", 0.0) for w in tws.values()]
        n = len(imbs)
        thr = self.cfg.flow_imbalance_threshold
        primary = self._win(snap)
        pimb = primary.get("trade_imbalance", 0.0)
        cimb = primary.get("count_imbalance", 0.0)
        consec = snap.consec_buy if pimb >= 0 else snap.consec_sell
        pos = sum(1 for x in imbs if x > 0.1)
        neg = sum(1 for x in imbs if x < -0.1)

        direction = 0
        if pos == n and pimb >= thr:
            direction = 1
        elif neg == n and pimb <= -thr:
            direction = -1
        triggered = direction != 0
        consistency = max(pos, neg) / n
        score = (clamp(abs(pimb) / thr * 60 + min(consec, 10) * 3)
                 if triggered else clamp(abs(pimb) / thr * 40))
        conf = clamp(consistency * 0.6 + min(abs(pimb), 1.0) * 0.4, 0.0, 1.0)
        reasons = ([f"多視窗{'買' if direction > 0 else '賣'}方主動流一致"
                    f"（imb={pimb:+.2f}, 連續{consec}）"] if triggered else [])
        return DetectorResult(self.name, direction, score, conf, triggered, reasons,
                              {"primary_imbalance": round(pimb, 3),
                               "count_imbalance": round(cimb, 3),
                               "consistency": round(consistency, 2),
                               "consec": consec}, self.pv)
