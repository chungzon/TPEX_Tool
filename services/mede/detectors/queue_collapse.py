"""QueueCollapseDetector — 最佳買/賣掛量快速消失（區分 成交/撤單/移價）。

買一崩塌 → 下方支撐消失（偏空，-1）；賣一崩塌 → 上方賣壓消失（偏多，+1）。
崩塌比 = (消耗+撤單)/前量；raw_metrics 保留 consumed/cancelled 以區分成因。需 BidAsk。
"""

from __future__ import annotations

from services.mede.detectors.base import Detector, DetectorResult, clamp


class QueueCollapseDetector(Detector):
    name = "queue_collapse"
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

        thr = self.cfg.queue_collapse_ratio
        bid_c = snap.bid_collapse_ratio
        ask_c = snap.ask_collapse_ratio
        # 移價本身即最強崩塌訊號
        bid_collapse = bid_c >= thr or snap.bid_moved == -1
        ask_collapse = ask_c >= thr or snap.ask_moved == 1
        direction = 0
        ratio = 0.0
        if ask_collapse and not bid_collapse:
            direction, ratio = 1, ask_c
        elif bid_collapse and not ask_collapse:
            direction, ratio = -1, bid_c
        elif bid_collapse and ask_collapse:
            direction = 1 if ask_c >= bid_c else -1
            ratio = max(ask_c, bid_c)
        triggered = direction != 0
        if direction > 0:
            consumed, cancelled = snap.ask_consumed, snap.ask_cancelled
        else:
            consumed, cancelled = snap.bid_consumed, snap.bid_cancelled
        cause = "成交吃單" if consumed >= cancelled else "撤單/移價"
        score = clamp(min(ratio / thr, 2.0) * 55) if triggered else 0.0
        conf = clamp(min(ratio, 1.0) * 0.7 + (0.3 if consumed >= cancelled else 0.1),
                     0.0, 1.0) if triggered else 0.0
        reasons = ([f"{'賣一' if direction > 0 else '買一'}崩塌"
                    f"（比{ratio:.0%}，{cause}）"] if triggered else [])
        self._last = DetectorResult(
            self.name, direction, score, conf, triggered, reasons,
            {"bid_collapse": round(bid_c, 2), "ask_collapse": round(ask_c, 2),
             "consumed": round(consumed, 0), "cancelled": round(cancelled, 0),
             "cause": cause}, self.pv)
        return self._last
