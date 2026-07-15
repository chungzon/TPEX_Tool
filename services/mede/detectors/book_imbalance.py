"""BookImbalanceShiftDetector — 五檔深度由平衡轉明顯偏多/偏空（含變化速度）。

需 BidAsk；Tick-only 時回傳 idle。僅在有新五檔時重算，兩筆間維持上次判定。
"""

from __future__ import annotations

from services.mede.detectors.base import Detector, DetectorResult, clamp


class BookImbalanceShiftDetector(Detector):
    name = "book_imbalance_shift"
    requires_bidask = True

    def __init__(self, cfg):
        super().__init__(cfg)
        self._prev_l5 = None
        self._last_ba = -1
        self._last = DetectorResult.idle(self.name, self.pv)

    def update(self, snap) -> DetectorResult:
        if not snap.bidask_available:
            return self._idle(reason="tick-only")
        if snap.bidask_updates == self._last_ba:
            return self._last
        self._last_ba = snap.bidask_updates

        l5, l1 = snap.l5_imbalance, snap.l1_imbalance
        vel = 0.0 if self._prev_l5 is None else (l5 - self._prev_l5)
        self._prev_l5 = l5
        thr = self.cfg.book_imbalance_threshold
        wb, wa = snap.weighted_bid_depth, snap.weighted_ask_depth
        depth_ratio = (wb - wa) / max(wb + wa, 1e-9)
        direction = 1 if l5 >= thr else (-1 if l5 <= -thr else 0)
        triggered = direction != 0
        score = (clamp(abs(l5) / thr * 55 + abs(vel) * 200)
                 if triggered else clamp(abs(l5) / thr * 40))
        conf = clamp(abs(l5) * 0.5 + abs(depth_ratio) * 0.3
                     + min(abs(vel) * 20, 1.0) * 0.2, 0.0, 1.0)
        reasons = ([f"五檔{'偏多' if direction > 0 else '偏空'}"
                    f"（L5={l5:+.2f}, 加權深度={depth_ratio:+.2f}, 變速={vel:+.3f}）"]
                   if triggered else [])
        self._last = DetectorResult(
            self.name, direction, score, conf, triggered, reasons,
            {"l1_imbalance": round(l1, 3), "l5_imbalance": round(l5, 3),
             "velocity": round(vel, 4), "depth_ratio": round(depth_ratio, 3)},
            self.pv)
        return self._last
