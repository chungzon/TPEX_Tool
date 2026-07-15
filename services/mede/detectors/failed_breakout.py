"""FailedBreakoutDetector — 突破後在時限內反向跌回（假突破）。

向上假突破：越過前高但無延續，主動買衰退/對側補量吸收，時限內跌回突破價下方 → 偏空(-1)。
向下反向處理。失敗訊號方向與原突破**相反**。有狀態：先記錄突破，再觀察。
"""

from __future__ import annotations

from services.mede.detectors.base import Detector, DetectorResult, clamp


class FailedBreakoutDetector(Detector):
    name = "failed_breakout"

    def __init__(self, cfg):
        super().__init__(cfg)
        self._active = None   # {"dir","ref","t","extreme"}

    def update(self, snap) -> DetectorResult:
        price, t = snap.last_price, snap.t_ns
        hi, lo = snap.recent_high, snap.recent_low
        w = self._win(snap)
        imb = w.get("trade_imbalance", 0.0)

        if self._active is None:
            if price > hi > 0:
                self._active = {"dir": 1, "ref": hi, "t": t, "extreme": price}
            elif 0 < price < lo:
                self._active = {"dir": -1, "ref": lo, "t": t, "extreme": price}
            return self._idle()

        a = self._active
        if (t - a["t"]) > self.cfg.failed_breakout_timeout_ms * 1_000_000:
            self._active = None
            return self._idle(reason="timeout")
        a["extreme"] = (max(a["extreme"], price) if a["dir"] > 0
                        else min(a["extreme"], price))
        failed = (price < a["ref"]) if a["dir"] > 0 else (price > a["ref"])
        opp_flow = (imb < -0.1) if a["dir"] > 0 else (imb > 0.1)
        if a["dir"] > 0:
            opp_sig = (snap.ask_replenished > 0
                       or snap.ask_repl_ratio >= self.cfg.replenishment_ratio)
        else:
            opp_sig = (snap.bid_replenished > 0
                       or snap.bid_repl_ratio >= self.cfg.replenishment_ratio)

        if failed and (opp_flow or opp_sig):
            direction = -a["dir"]
            ref, extreme = a["ref"], a["extreme"]
            self._active = None
            score = clamp(50 + (25 if opp_flow else 0) + (25 if opp_sig else 0))
            conf = clamp(0.4 + (0.3 if opp_flow else 0.0)
                         + (0.3 if opp_sig else 0.0), 0.0, 1.0)
            reasons = [f"{'向上' if direction < 0 else '向下'}假突破："
                       f"{ref:g} 未延續，反向跌回"]
            return DetectorResult(self.name, direction, score, conf, True, reasons,
                                  {"ref": ref, "extreme": round(extreme, 2),
                                   "opp_flow": opp_flow, "opp_signal": opp_sig},
                                  self.pv)
        return self._idle(watching=True, ref=a["ref"])
