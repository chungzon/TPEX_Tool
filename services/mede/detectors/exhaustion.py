"""ExhaustionDetector — 動能由強轉弱（成交速度/OFI 自峰值衰退，價格仍在極值）。

先偵測一段強動能(成交速度 z 高)並記其方向與價格極值；之後若成交速度與 OFI
明顯低於峰值、且價速轉弱、價格仍守在極值附近 → 判定衰竭，方向與原動能**相反**。
"""

from __future__ import annotations

from services.mede.detectors.base import Detector, DetectorResult, RollingZ, clamp


class ExhaustionDetector(Detector):
    name = "exhaustion"

    def __init__(self, cfg):
        super().__init__(cfg)
        self._z = RollingZ(cfg.baseline_window)
        self._burst = None       # {"dir","peak_rate","peak_ofi","extreme","t"}
        self._prev_vel = None

    def update(self, snap) -> DetectorResult:
        w = self._win(snap)
        rate = w.get("buy_cnt", 0) + w.get("sell_cnt", 0)
        ofi = abs(w.get("ofi_sum", 0.0))
        imb = w.get("trade_imbalance", 0.0)
        vel = snap.price_velocity
        price, t = snap.last_price, snap.t_ns
        z = self._z.z(rate)
        self._z.push(rate)
        warm = self._z.ready(self.cfg.minimum_warmup_ticks)
        vel_decl = self._prev_vel is not None and abs(vel) < abs(self._prev_vel)
        self._prev_vel = vel

        # 偵測/更新強動能
        if warm and z >= self.cfg.trade_burst_zscore and abs(imb) > 0.2:
            d = 1 if imb > 0 else -1
            if self._burst is None or self._burst["dir"] != d:
                self._burst = {"dir": d, "peak_rate": rate, "peak_ofi": ofi,
                               "extreme": price, "t": t}
            else:
                b = self._burst
                b["peak_rate"] = max(b["peak_rate"], rate)
                b["peak_ofi"] = max(b["peak_ofi"], ofi)
                b["extreme"] = max(b["extreme"], price) if d > 0 else min(b["extreme"], price)
                b["t"] = t
            return self._idle(building=True)

        if self._burst is None:
            return self._idle()
        b = self._burst
        if (t - b["t"]) > self.cfg.exhaustion_timeout_ms * 1_000_000:
            self._burst = None
            return self._idle(reason="timeout")
        b["extreme"] = (max(b["extreme"], price) if b["dir"] > 0
                        else min(b["extreme"], price))

        fr = self.cfg.exhaustion_fade_ratio
        rate_fade = b["peak_rate"] > 0 and rate < b["peak_rate"] * fr
        ofi_fade = b["peak_ofi"] <= 0 or ofi < b["peak_ofi"] * fr
        near_extreme = self._win_price_near(snap, b)   # 價格仍守在極值附近

        triggered = bool(rate_fade and ofi_fade and vel_decl and near_extreme)
        if triggered:
            direction = -b["dir"]
            score = clamp(45 + (1 - rate / max(b["peak_rate"], 1)) * 30
                          + (25 if ofi_fade else 0))
            conf = clamp(0.4 + (0.3 if rate_fade else 0.0)
                         + (0.3 if ofi_fade else 0.0), 0.0, 1.0)
            peak_rate, extreme = b["peak_rate"], b["extreme"]
            self._burst = None
            reasons = [f"{'上漲' if direction < 0 else '下跌'}動能衰竭："
                       f"成交速度自峰值 {peak_rate:.0f} 回落、OFI 收斂、價守極值"]
            return DetectorResult(self.name, direction, score, conf, True, reasons,
                                  {"peak_rate": peak_rate, "cur_rate": rate,
                                   "extreme": round(extreme, 2)}, self.pv)
        return self._idle(watching=True)

    @staticmethod
    def _win_price_near(snap, b) -> bool:
        # 價格仍在動能極值附近（±3 跳內），代表尚未回落即動能先竭
        if snap.price_range_ticks == 0:
            return True
        return abs(snap.last_price - b["extreme"]) / max(
            snap.price_range_ticks, 1e-9) <= 3.0
