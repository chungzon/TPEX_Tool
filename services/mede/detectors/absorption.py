"""AbsorptionDetector — 大量主動成交卻未推動價格（對側吸收）。

賣方吸收：大量主動買、價幾乎沒漲、賣方守價補量 → 上方被壓（偏空，方向 -1）。
買方吸收：大量主動賣、價幾乎沒跌、買方守價補量 → 下方有撐（偏多，方向 +1）。

吸收的定義核心是「被動側守住價位並補量把主動單吃掉」，而非單純「量大價沒動」。
故有委託簿時，必要條件：對側最佳價未朝主動方向讓步（未移價）且有補量；
否則量大價平只是「發動起漲/起跌的第一筆」，非吸收。無委託簿(tick-only)退回
量 vs 價，但需成交筆數達門檻，避免單一大單在起漲瞬間被誤判。
"""

from __future__ import annotations

from services.mede.detectors.base import Detector, DetectorResult, clamp


class AbsorptionDetector(Detector):
    name = "absorption"

    def update(self, snap) -> DetectorResult:
        w = self._win(snap)
        buy_vol = w.get("buy_vol", 0.0)
        sell_vol = w.get("sell_vol", 0.0)
        n_trades = w.get("buy_cnt", 0) + w.get("sell_cnt", 0)
        rng = snap.price_range_ticks
        minv = self.cfg.absorption_min_volume
        maxt = self.cfg.absorption_max_price_ticks
        min_trades = self.cfg.absorption_min_trades
        ba = snap.bidask_available

        direction, absorbed, repl = 0, 0.0, 0.0
        base = rng <= maxt and n_trades >= min_trades
        if base and buy_vol >= minv and buy_vol > sell_vol * 1.5:
            # 賣方吸收（頂）：賣方須守價（ask 未上移）且補量；tick-only 退回量價
            if not ba or (snap.ask_moved <= 0 and snap.ask_replenished > 0):
                direction, absorbed, repl = -1, buy_vol, snap.ask_replenished
        elif base and sell_vol >= minv and sell_vol > buy_vol * 1.5:
            # 買方吸收（底）：買方須守價（bid 未下移）且補量；tick-only 退回量價
            if not ba or (snap.bid_moved >= 0 and snap.bid_replenished > 0):
                direction, absorbed, repl = 1, sell_vol, snap.bid_replenished
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
