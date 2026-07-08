"""趨勢濾網（大週期方向濾網）— 雙層濾網架構的「上層」。

微觀結構（OBI/VPIN/大單）訊號時效只有毫秒～秒，單獨使用在盤整期會被雜訊洗到
懷疑人生。實務高勝算作法是「雙層濾網」：

    【大週期濾網】決定今天/當下只能做多或做空（過濾方向、過濾盤整）
          │
          ▼
    【微觀訊號】決定在哪一秒扣板機（精準進場）

本模組提供兩種可組合的濾網，皆為 pure-compute、防未來函數（只用已完成的 bar
計算指標，當前價只拿來比較，不偷看未來）：

* :func:`daily_trend_bias` — 以「日線」收盤 SMA 判斷當日方向偏多/偏空（回測時傳入
  回測日**之前**的日收盤序列即可，無盤中暖機成本）。
* :class:`IntradayTrendFilter` — 把逐筆 tick 重採樣成 N 秒 bar，提供兩種閘門：
  ``'ma'``（價在均線上/下 → 只做多/只做空）與 ``'squeeze'``（布林通道擠壓後的
  突破邊緣才放行，濾掉盤整期）。
"""

from __future__ import annotations

from collections import deque
from statistics import mean, pstdev


def daily_trend_bias(daily_closes: list[float], ma_period: int = 20
                     ) -> tuple[bool, bool, str]:
    """日線趨勢偏向 → (可做多, 可做空, 說明)。

    ``daily_closes`` 為回測日**之前**的日收盤序列（最舊在前、最新在後）。以最後一筆
    收盤（前一交易日收盤）對 SMA(ma_period) 比較：站上均線→當日只做多，跌破→只做空。
    資料不足時兩邊都放行（不封鎖），並在說明中標示暖機。
    """
    closes = [float(c) for c in daily_closes if c and c > 0]
    if len(closes) < ma_period:
        return True, True, f"日線資料不足（{len(closes)}/{ma_period}），不套用方向濾網"
    ma = mean(closes[-ma_period:])
    ref = closes[-1]
    if ref >= ma:
        return True, False, f"日線偏多（前收 {ref:.2f} ≥ MA{ma_period} {ma:.2f}）→ 只做多"
    return False, True, f"日線偏空（前收 {ref:.2f} < MA{ma_period} {ma:.2f}）→ 只做空"


class IntradayTrendFilter:
    """把 tick 重採樣成 N 秒 bar，輸出「當下可否做多/做空」的閘門。

    mode:
        'ma'      — 價 > 分線均線 → 可做多；價 < 均線 → 可做空。
        'squeeze' — 布林通道擠壓（近期帶寬收窄）後，價突破上/下軌才放行（抓突破邊緣）。
    暖機（bar 數不足）期間採寬鬆策略：兩邊皆放行，以免整段開盤都不能交易；state
    會標示暖機進度供診斷。
    """

    def __init__(self, mode: str = "ma", bar_seconds: int = 300,
                 ma_period: int = 10, bb_period: int = 20, bb_k: float = 2.0,
                 squeeze_factor: float = 0.6, squeeze_lookback: int = 20):
        self.mode = mode
        self.bar_seconds = max(1, int(bar_seconds))
        self.ma_period = max(1, int(ma_period))
        self.bb_period = max(2, int(bb_period))
        self.bb_k = float(bb_k)
        self.squeeze_factor = float(squeeze_factor)
        self.squeeze_lookback = max(2, int(squeeze_lookback))

        maxlen = max(self.ma_period, self.bb_period) + 1
        self._closes: deque[float] = deque(maxlen=maxlen)
        self._bandwidths: deque[float] = deque(maxlen=self.squeeze_lookback)

        self._bar_idx: int | None = None
        self._forming_close = 0.0

        # 已完成 bar 算出的指標（供當前 tick 比對，不含當前未完成 bar）
        self._ma = 0.0
        self._mid = 0.0
        self._upper = 0.0
        self._lower = 0.0

        # 輸出
        self.last_price = 0.0
        self.long_ok = True
        self.short_ok = True
        self.state = "暖機中"
        self.bars_done = 0

    def update(self, ts_ns, price: float) -> None:
        """吃一筆 tick（奈秒 epoch + 成交價），更新 bar 與閘門。

        ``ts_ns`` 須為一致的 UTC 基準 epoch（本專案統一採「台北牆鐘當成 UTC」，
        見 microstructure_service._tick_ns 與 backtest 的歷史 ts）。bar 以
        ``ts_ns // bar_seconds`` 分組，故同一基準才能保證即時與回測的 bar 對齊。
        """
        try:
            price = float(price)
        except (TypeError, ValueError):
            return
        if price <= 0:
            return
        self.last_price = price

        idx = int((int(ts_ns) / 1e9) // self.bar_seconds)
        if self._bar_idx is None:
            self._bar_idx = idx
        elif idx > self._bar_idx:
            # 收掉上一根 bar（以該 bar 最後一筆成交為收盤）
            self._closes.append(self._forming_close)
            self.bars_done += 1
            self._recompute()
            self._bar_idx = idx
        self._forming_close = price
        self._eval(price)

    def _recompute(self) -> None:
        """bar 收盤時重算指標（只用已完成 bar，防未來函數）。"""
        n = len(self._closes)
        if n >= self.ma_period:
            self._ma = mean(list(self._closes)[-self.ma_period:])
        if n >= self.bb_period:
            window = list(self._closes)[-self.bb_period:]
            self._mid = mean(window)
            sd = pstdev(window)
            self._upper = self._mid + self.bb_k * sd
            self._lower = self._mid - self.bb_k * sd
            bw = (self._upper - self._lower) / self._mid if self._mid else 0.0
            self._bandwidths.append(bw)

    def _eval(self, price: float) -> None:
        if self.mode == "ma":
            if len(self._closes) < self.ma_period or self._ma <= 0:
                self.long_ok = self.short_ok = True
                self.state = f"暖機 {self.bars_done}/{self.ma_period} 根"
                return
            self.long_ok = price > self._ma
            self.short_ok = price < self._ma
            self.state = (f"MA{self.ma_period}={self._ma:.2f} 價{price:.2f} → "
                          f"{'偏多' if price > self._ma else '偏空'}")
            return

        if self.mode == "squeeze":
            if len(self._closes) < self.bb_period or self._mid <= 0:
                self.long_ok = self.short_ok = True
                self.state = f"暖機 {self.bars_done}/{self.bb_period} 根"
                return
            # 近期是否出現擠壓：lookback 內最小帶寬 ≤ factor × 平均帶寬
            squeezed = False
            if len(self._bandwidths) >= 2:
                avg_bw = mean(self._bandwidths)
                squeezed = min(self._bandwidths) <= self.squeeze_factor * avg_bw
            brk_up = price >= self._upper
            brk_dn = price <= self._lower
            self.long_ok = squeezed and brk_up
            self.short_ok = squeezed and brk_dn
            tag = ("突破上軌" if brk_up else "突破下軌" if brk_dn else "帶內")
            self.state = (f"布林[{self._lower:.2f},{self._upper:.2f}] "
                          f"{'擠壓' if squeezed else '正常'}·{tag}")
            return

        # 未知 mode → 不濾
        self.long_ok = self.short_ok = True
        self.state = "濾網關閉"

    def snapshot(self) -> dict:
        return {
            "mode": self.mode, "state": self.state,
            "long_ok": self.long_ok, "short_ok": self.short_ok,
            "bars_done": self.bars_done, "ma": self._ma,
            "upper": self._upper, "lower": self._lower,
        }
