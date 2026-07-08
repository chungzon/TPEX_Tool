"""事件驅動微觀結構回測模組（MicrostructureBacktester）。

用永豐歷史逐筆資料（Tick，含 level-1 買賣價量）重放，套用與即時追蹤相同的
OBI / VPIN / 大單 偵測邏輯，模擬做多/做空進出場並統計績效。

策略邏輯（對稱）
----------------
🟢 做多進場（同時滿足，且在 confluence 窗口內）：
   1. OBI 持續 > obi_threshold（買盤蓄勢，setup_side == 'buy'）
   2. 連續 ≥2 個成交量桶買方推進 > buy_push_ratio
   3. 出現外盤連續大單且價格向上跳檔（起漲點 attack buy）
🔴 做空進場（鏡像）：
   1. OBI 持續 < -obi_threshold（賣壓蓄勢，setup_side == 'sell'）
   2. 連續 ≥2 個量桶賣方推進 > buy_push_ratio
   3. 內盤連續大單且價格向下跳檔（起跌點 attack sell）

出場：固定停利 / 固定停損 / 移動停損 / OBI 反轉（穿越 0）/ 反向訊號。

防未來函數（Look-ahead bias）
------------------------------
每筆 tick 只用「當下與過去」的資訊決策，成交價用「當前 tick」的對手檔位
（做多吃 Ask、做空砍 Bid）再加滑價，不偷看下一筆。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime

from services.microstructure_service import MicrostructureEngine, MicroConfig, Alert


# --------------------------------------------------------------------------- #
#  台股跳動單位（tick size）
# --------------------------------------------------------------------------- #

def tw_tick_size(price: float) -> float:
    """台股股票各價格區間的最小跳動單位。"""
    if price < 10:
        return 0.01
    if price < 50:
        return 0.05
    if price < 100:
        return 0.1
    if price < 500:
        return 0.5
    if price < 1000:
        return 1.0
    return 5.0


# --------------------------------------------------------------------------- #
#  參數 / 結果資料結構
# --------------------------------------------------------------------------- #

@dataclass
class BacktestParams:
    allow_long: bool = True
    allow_short: bool = True
    confluence_window: int = 40      # 三條件需在最近 N 筆 tick 內同時成立
    take_profit_pct: float = 0.5     # 固定停利 %
    stop_loss_pct: float = 0.3       # 固定停損 %
    trailing_pct: float = 0.0        # 移動停損 %（0 = 關閉）
    exit_on_obi_flip: bool = True    # OBI 穿越 0 反向即出場
    exit_on_reverse: bool = True     # 出現反向 attack 訊號即出場
    slippage_ticks: float = 1.0      # 進出場各加幾個 tick 的滑價
    fee_rate: float = 0.001425       # 手續費率（單邊）
    fee_discount: float = 0.3        # 手續費折數（電子下單常見 3 折）
    tax_rate: float = 0.0015         # 證交稅（當沖賣出 0.15%）
    min_hold_ticks: int = 3          # 進場後最少持有幾筆才允許出場（濾雜訊）


@dataclass
class TradeRecord:
    direction: str          # 'long' | 'short'
    entry_time: str
    entry_price: float
    exit_time: str
    exit_price: float
    ret_pct: float          # 淨報酬率（含手續費/稅/滑價）%
    exit_reason: str
    hold_ticks: int

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class BacktestResult:
    code: str = ""
    date: str = ""
    tick_count: int = 0
    trades: list[TradeRecord] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)  # NAV 序列（起始 1.0）
    # 績效指標
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    total_return_pct: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    expectancy_pct: float = 0.0
    long_trades: int = 0
    short_trades: int = 0
    error: str = ""

    def summary_dict(self) -> dict:
        d = asdict(self)
        d.pop("trades", None)
        d.pop("equity_curve", None)
        return d


# --------------------------------------------------------------------------- #
#  Backtester
# --------------------------------------------------------------------------- #

class MicrostructureBacktester:
    """事件驅動回測器：重放逐筆資料 → 產生交易 → 統計績效。"""

    def __init__(self, cfg: MicroConfig, params: BacktestParams):
        self.cfg = cfg
        self.p = params

    # ---- 主流程 ----
    def run(self, ticks: list[dict], code: str = "", date: str = "") -> BacktestResult:
        res = BacktestResult(code=code, date=date, tick_count=len(ticks))
        if not ticks:
            res.error = "無逐筆資料"
            return res

        # 每筆 tick 的 alert 收集器（不看未來）
        tick_alerts: list[Alert] = []
        engine = MicrostructureEngine(
            self.cfg, on_alert=lambda a: tick_alerts.append(a))

        # 訊號新鮮度追蹤（記錄最後一次成立的 tick index）
        last_attack_buy = -10**9
        last_attack_sell = -10**9

        # 部位狀態
        pos = 0                 # 0 flat, 1 long, -1 short
        entry_price = 0.0
        entry_i = 0
        entry_time = ""
        peak = 0.0              # 移動停損用：多單記最高、空單記最低
        nav = 1.0
        res.equity_curve.append(nav)

        W = self.p.confluence_window

        for i, tk in enumerate(ticks):
            bid = tk["bid_price"]
            ask = tk["ask_price"]
            close = tk["close"]
            t_str = self._ts_str(tk.get("ts"))

            # --- 餵引擎（先 bidask 再 tick），收集本筆 alert ---
            tick_alerts.clear()
            if bid > 0 and ask > 0:
                engine.on_bidask({
                    "bid_price": [bid], "bid_volume": [tk["bid_volume"]],
                    "ask_price": [ask], "ask_volume": [tk["ask_volume"]],
                })
            engine.on_tick({
                "code": code, "close": close, "volume": tk["volume"],
                "tick_type": tk["tick_type"],
            })

            for a in tick_alerts:
                if a.kind == "attack" and a.side == "buy":
                    last_attack_buy = i
                elif a.kind == "attack" and a.side == "sell":
                    last_attack_sell = i

            snap = engine.snapshot()
            obi = snap["obi5"]

            # ===================== 出場判斷 =====================
            if pos != 0:
                held = i - entry_i
                exit_reason = ""
                # 目前浮動報酬（用 close 概估，實際成交用對手檔位）
                if pos == 1:
                    cur_ret = (close - entry_price) / entry_price * 100
                    peak = max(peak, close)
                    draw = (peak - close) / peak * 100 if peak > 0 else 0
                else:
                    cur_ret = (entry_price - close) / entry_price * 100
                    peak = min(peak, close) if peak > 0 else close
                    draw = (close - peak) / peak * 100 if peak > 0 else 0

                if held >= self.p.min_hold_ticks:
                    if cur_ret >= self.p.take_profit_pct:
                        exit_reason = "停利"
                    elif cur_ret <= -self.p.stop_loss_pct:
                        exit_reason = "停損"
                    elif self.p.trailing_pct > 0 and draw >= self.p.trailing_pct:
                        exit_reason = "移動停損"
                    elif self.p.exit_on_obi_flip and (
                            (pos == 1 and obi <= 0) or (pos == -1 and obi >= 0)):
                        exit_reason = "OBI反轉"
                    elif self.p.exit_on_reverse and (
                            (pos == 1 and last_attack_sell == i) or
                            (pos == -1 and last_attack_buy == i)):
                        exit_reason = "反向訊號"

                if exit_reason:
                    # 成交：多單以 Bid 賣出、空單以 Ask 買回，含滑價
                    if pos == 1:
                        fill = self._slip(bid if bid > 0 else close, -1, close)
                        ret = self._net_return(entry_price, fill, "long")
                    else:
                        fill = self._slip(ask if ask > 0 else close, +1, close)
                        ret = self._net_return(entry_price, fill, "short")
                    nav *= (1 + ret / 100)
                    res.equity_curve.append(nav)
                    res.trades.append(TradeRecord(
                        direction="long" if pos == 1 else "short",
                        entry_time=entry_time, entry_price=round(entry_price, 4),
                        exit_time=t_str, exit_price=round(fill, 4),
                        ret_pct=round(ret, 4), exit_reason=exit_reason,
                        hold_ticks=held))
                    pos = 0
                    continue  # 出場當筆不再進場

            # ===================== 進場判斷 =====================
            if pos == 0:
                long_ok = (
                    self.p.allow_long
                    and snap["setup_active"] and snap["setup_side"] == "buy"
                    and snap["consec_buy_buckets"] >= 2
                    and (i - last_attack_buy) <= W)
                short_ok = (
                    self.p.allow_short
                    and snap["setup_active"] and snap["setup_side"] == "sell"
                    and snap["consec_sell_buckets"] >= 2
                    and (i - last_attack_sell) <= W)

                if long_ok and bid > 0 and ask > 0:
                    entry_price = self._slip(ask, +1, close)  # 吃 Ask + 滑價
                    pos = 1
                    entry_i = i
                    entry_time = t_str
                    peak = close
                elif short_ok and bid > 0 and ask > 0:
                    entry_price = self._slip(bid, -1, close)  # 砍 Bid - 滑價
                    pos = -1
                    entry_i = i
                    entry_time = t_str
                    peak = close

        # 收盤強制平倉
        if pos != 0:
            last = ticks[-1]
            close = last["close"]
            t_str = self._ts_str(last.get("ts"))
            if pos == 1:
                fill = self._slip(last["bid_price"] or close, -1, close)
                ret = self._net_return(entry_price, fill, "long")
            else:
                fill = self._slip(last["ask_price"] or close, +1, close)
                ret = self._net_return(entry_price, fill, "short")
            nav *= (1 + ret / 100)
            res.equity_curve.append(nav)
            res.trades.append(TradeRecord(
                direction="long" if pos == 1 else "short",
                entry_time=entry_time, entry_price=round(entry_price, 4),
                exit_time=t_str, exit_price=round(fill, 4),
                ret_pct=round(ret, 4), exit_reason="收盤平倉",
                hold_ticks=len(ticks) - 1 - entry_i))

        self._compute_metrics(res)
        return res

    # ---- 成交價：滑價（direction +1 買方向上、-1 賣方向下）----
    def _slip(self, price: float, direction: int, ref_price: float) -> float:
        ts = tw_tick_size(ref_price)
        return price + direction * self.p.slippage_ticks * ts

    # ---- 淨報酬率（含手續費/稅），以進場價為分母 ----
    def _net_return(self, entry: float, exit_: float, direction: str) -> float:
        fee = self.p.fee_rate * self.p.fee_discount
        tax = self.p.tax_rate
        if entry <= 0:
            return 0.0
        if direction == "long":
            # 買進付 fee，賣出付 fee+tax
            cost = entry * fee + exit_ * (fee + tax)
            pnl = (exit_ - entry) - cost
        else:
            # 放空：賣出付 fee+tax，買回付 fee
            cost = entry * (fee + tax) + exit_ * fee
            pnl = (entry - exit_) - cost
        return pnl / entry * 100

    @staticmethod
    def _ts_str(ts) -> str:
        """永豐 ts 為奈秒 epoch → HH:MM:SS。"""
        try:
            return datetime.fromtimestamp(int(ts) / 1e9).strftime("%H:%M:%S")
        except (TypeError, ValueError, OSError):
            return ""

    # ---- 績效統計 ----
    def _compute_metrics(self, res: BacktestResult) -> None:
        trades = res.trades
        res.total_trades = len(trades)
        if not trades:
            return
        rets = [t.ret_pct for t in trades]
        wins = [r for r in rets if r > 0]
        losses = [r for r in rets if r <= 0]
        res.wins = len(wins)
        res.losses = len(losses)
        res.win_rate = len(wins) / len(trades) * 100
        res.long_trades = sum(1 for t in trades if t.direction == "long")
        res.short_trades = sum(1 for t in trades if t.direction == "short")

        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        res.profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
        res.avg_win_pct = (gross_profit / len(wins)) if wins else 0.0
        res.avg_loss_pct = (-gross_loss / len(losses)) if losses else 0.0
        res.expectancy_pct = sum(rets) / len(rets)

        # 以 NAV 複利序列計算總報酬與最大回撤
        nav = res.equity_curve
        res.total_return_pct = (nav[-1] - 1.0) * 100 if nav else 0.0
        peak = nav[0] if nav else 1.0
        mdd = 0.0
        for v in nav:
            peak = max(peak, v)
            dd = (peak - v) / peak * 100 if peak > 0 else 0
            mdd = max(mdd, dd)
        res.max_drawdown_pct = mdd

    # ---- 文字報告 ----
    def report_text(self, res: BacktestResult) -> str:
        if res.error:
            return f"回測失敗：{res.error}"
        if res.total_trades == 0:
            return (f"{res.code} {res.date}｜{res.tick_count} 筆 tick\n"
                    f"未觸發任何交易（訊號條件未同時成立）。")
        pf = "∞" if res.profit_factor == float("inf") else f"{res.profit_factor:.2f}"
        lines = [
            f"===== 微觀結構回測報告 =====",
            f"標的：{res.code}　日期：{res.date}　Tick 數：{res.tick_count:,}",
            f"總交易：{res.total_trades}（多 {res.long_trades} / 空 {res.short_trades}）",
            f"勝率：{res.win_rate:.1f}%（{res.wins} 勝 / {res.losses} 敗）",
            f"總報酬：{res.total_return_pct:+.2f}%",
            f"獲利因子：{pf}",
            f"最大回撤：{res.max_drawdown_pct:.2f}%",
            f"平均獲利：{res.avg_win_pct:+.3f}%　平均虧損：{res.avg_loss_pct:+.3f}%",
            f"每筆期望值：{res.expectancy_pct:+.3f}%",
            f"============================",
        ]
        return "\n".join(lines)
