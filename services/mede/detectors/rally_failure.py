"""RallyFailureDetector（6.1 拉高失敗）— 開盤/盤中拉高後無法延續、快速回落。

空方前導結構：曾自開盤價或 VWAP 上方快速拉高 → 無法續創高 → 高點後回落，
且回落時主動買仍在、但價格反應不足（買不上去）。方向偏空(−1)。
狀態：記住盤中最高與是否曾拉高（只用當下與過去）。
"""

from __future__ import annotations

from services.mede.detectors.base import Detector, DetectorResult, clamp


class RallyFailureDetector(Detector):
    name = "rally_failure"

    def __init__(self, cfg):
        super().__init__(cfg)
        self._session_high = 0.0
        self._high_seen = False

    def update(self, snap) -> DetectorResult:
        price = snap.last_price
        ts = snap.tick_size
        if price <= 0 or ts <= 0:
            return self._idle()
        ref = snap.open_price if snap.open_price > 0 else price
        if price > self._session_high:
            self._session_high = price
        rose = (self._session_high - ref) / ts
        rallied = rose >= self.cfg.rally_min_rise_ticks and self._session_high >= snap.vwap
        if rallied:
            self._high_seen = True
        fade = (self._session_high - price) / ts
        w = self._win(snap)
        imb = w.get("trade_imbalance", 0.0)
        below_vwap = price < snap.vwap
        # 拉高失敗：曾拉高 + 自高點回落達門檻 + 已失守 VWAP + 賣壓浮現
        triggered = bool(self._high_seen and fade >= self.cfg.rally_fade_ticks
                         and below_vwap and imb <= 0.0)
        if not triggered:
            return self._idle(session_high=self._session_high, fade_ticks=round(fade, 1))
        score = clamp(fade / max(self.cfg.rally_fade_ticks, 1e-9) * 40
                      + min(abs(imb), 1.0) * 30 + (rose >= self.cfg.rally_min_rise_ticks) * 20)
        conf = clamp(0.3 + min(fade / (self.cfg.rally_fade_ticks * 2), 1.0) * 0.4
                     + min(abs(imb), 1.0) * 0.3, 0.0, 1.0)
        reasons = [f"拉高失敗：自高 {self._session_high:g} 回落 {fade:.0f} 跳、"
                   f"跌破 VWAP({snap.vwap:g})"]
        return DetectorResult(self.name, -1, score, conf, True, reasons,
                              {"session_high": self._session_high,
                               "fade_ticks": round(fade, 1),
                               "rose_ticks": round(rose, 1),
                               "imbalance": round(imb, 3)}, self.pv)
