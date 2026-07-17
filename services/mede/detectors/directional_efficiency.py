"""DirectionalEfficiencyDetector（6.6 下跌有效、反彈無效）— 量價效率不對稱。

比較「下跌段」與「反彈段」每單位成交量造成的價格移動：若下跌用較少量即推動較多、
反彈需較大量卻推不高，則偏空(−1)。以價速正負分段，各自維護 EMA 效率，避免逐筆雜訊。
效率＝|price_velocity| / 該方向窗內成交量（price 每張的推動力）。
"""

from __future__ import annotations

from services.mede.detectors.base import Detector, DetectorResult, clamp


class DirectionalEfficiencyDetector(Detector):
    name = "directional_efficiency"

    def __init__(self, cfg):
        super().__init__(cfg)
        self._down_eff = 0.0
        self._up_eff = 0.0
        self._down_vol = 0.0
        self._up_vol = 0.0

    def update(self, snap) -> DetectorResult:
        ts = snap.tick_size
        if ts <= 0:
            return self._idle()
        w = self._win(snap)
        buy_vol = w.get("buy_vol", 0.0)
        sell_vol = w.get("sell_vol", 0.0)
        vel_ticks = snap.price_velocity / ts if ts > 0 else 0.0   # 跳/秒
        a = 0.3   # EMA 平滑
        if vel_ticks < 0 and sell_vol > 0:
            eff = abs(vel_ticks) / sell_vol
            self._down_eff = (1 - a) * self._down_eff + a * eff
            self._down_vol = (1 - a) * self._down_vol + a * sell_vol
        elif vel_ticks > 0 and buy_vol > 0:
            eff = vel_ticks / buy_vol
            self._up_eff = (1 - a) * self._up_eff + a * eff
            self._up_vol = (1 - a) * self._up_vol + a * buy_vol
        if self._up_eff <= 1e-12 or self._down_eff <= 1e-12:
            return self._idle(down_eff=round(self._down_eff, 5),
                              up_eff=round(self._up_eff, 5))
        ratio = self._down_eff / self._up_eff
        enough = (self._down_vol >= self.cfg.efficiency_min_volume
                  and self._up_vol >= self.cfg.efficiency_min_volume)
        triggered = bool(ratio >= self.cfg.efficiency_min_ratio and enough)
        if not triggered:
            return self._idle(ratio=round(ratio, 2))
        score = clamp(30 + min(ratio / max(self.cfg.efficiency_min_ratio, 1e-9), 3.0) * 20)
        conf = clamp(0.3 + min(ratio / (self.cfg.efficiency_min_ratio * 2), 1.0) * 0.5,
                     0.0, 1.0)
        reasons = [f"下跌有效反彈無效：下跌效率/反彈效率 = {ratio:.1f}×"
                   f"（下跌每張推動力遠大於反彈）"]
        return DetectorResult(self.name, -1, score, conf, True, reasons,
                              {"down_eff": round(self._down_eff, 5),
                               "up_eff": round(self._up_eff, 5),
                               "ratio": round(ratio, 2)}, self.pv)
