"""AbsorptionDetector — 大量主動成交卻未推動價格（對側吸收）。

賣方吸收：大量主動買、價幾乎沒漲、賣方補量 → 上方被壓（偏空，方向 -1）。
買方吸收：大量主動賣、價幾乎沒跌、買方補量 → 下方有撐（偏多，方向 +1）。
核心（量 vs 價）Tick-only 可算；補量為加分（需 BidAsk）。
"""

from __future__ import annotations

from services.mede.detectors.base import Detector, DetectorResult, clamp


class AbsorptionDetector(Detector):
    name = "absorption"

    def update(self, snap) -> DetectorResult:
        w = self._win(snap)
        buy_vol = w.get("buy_vol", 0.0)
        sell_vol = w.get("sell_vol", 0.0)
        rng = snap.price_range_ticks
        minv = self.cfg.absorption_min_volume
        maxt = self.cfg.absorption_max_price_ticks

        direction, absorbed, repl = 0, 0.0, 0.0
        if buy_vol >= minv and rng <= maxt and buy_vol > sell_vol * 1.5:
            direction, absorbed, repl = -1, buy_vol, snap.ask_replenished   # 賣方吸收（頂）
        elif sell_vol >= minv and rng <= maxt and sell_vol > buy_vol * 1.5:
            direction, absorbed, repl = 1, sell_vol, snap.bid_replenished   # 買方吸收（底）
        if direction == 0:
            return self._idle(range_ticks=rng, buy_vol=buy_vol, sell_vol=sell_vol)

        score = clamp(min(absorbed / minv, 3.0) * 25 + (25 if repl > 0 else 0.0)
                      + (1.0 - min(rng / max(maxt, 1e-9), 1.0)) * 25)
        conf = clamp(min(absorbed / (minv * 2), 1.0) * 0.5
                     + (1.0 - min(rng / max(maxt, 1e-9), 1.0)) * 0.3
                     + (0.2 if repl > 0 else 0.0), 0.0, 1.0)
        reasons = [f"{'賣方(頂)' if direction < 0 else '買方(底)'}吸收："
                   f"主動量 {absorbed:.0f} 張但價僅動 {rng:.0f} 跳"
                   f"{'，對側補量' if repl > 0 else ''}"]
        return DetectorResult(self.name, direction, score, conf, True, reasons,
                              {"absorbed_volume": round(absorbed, 0),
                               "range_ticks": rng, "replenished": round(repl, 0)},
                              self.pv)
