"""神手 × 葛蘭碧 當沖回測引擎（單日 5 分 K + 逐筆）。

策略（使用者定案）：
- 進場：葛蘭碧(5分K)買訊 AND 神手連買達門檻（連買 >= N 筆）
- 出場：葛蘭碧(5分K)賣訊 OR 買盤竭盡
- 單日當沖，收盤前平倉。做多方向。

輸入：
- kbars_1min: [{time, open, high, low, close, volume}]（Shioaji get_intraday_kbars）
- ticks: [{time, close, volume, tick_type}]（Shioaji get_historical_ticks）
純計算、無 I/O。葛蘭碧沿用 turnover_monitor_service.granville_signal（吃 daily_trend
格式：以 5 分 K 當「日線」餵入）。神手連次/竭盡沿用 tick_streak_service。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from services.turnover_monitor_service import granville_signal
from services.tick_streak_service import StreakState, update as tick_update


_STREAK_ENTER = 5        # 神手連買 >= 此筆數 → 確認買盤動能
_GRAN_MA = 20            # 葛蘭碧月線在 5 分 K 上的週期（需 >= 此根數才有訊號）


@dataclass
class Trade:
    entry_time: str
    entry_price: float
    exit_time: str = ""
    exit_price: float = 0.0
    ret: float = 0.0            # 報酬 %
    entry_rule: str = ""
    exit_reason: str = ""


@dataclass
class BacktestResult:
    code: str = ""
    date: str = ""
    bars_5min: int = 0
    trades: list = field(default_factory=list)
    win_rate: float = 0.0
    total_ret: float = 0.0     # 單利加總 %
    compound_ret: float = 0.0  # 複利累積 %
    note: str = ""


def aggregate_5min(kbars_1min: list[dict]) -> list[dict]:
    """1 分 K 聚合成 5 分 K（每 5 根一組，OHLCV）。時間取該組末根。"""
    out = []
    for i in range(0, len(kbars_1min), 5):
        grp = kbars_1min[i:i + 5]
        if not grp:
            continue
        out.append({
            "time": grp[-1].get("time", ""),
            "open": grp[0].get("open", 0),
            "high": max(b.get("high", 0) for b in grp),
            "low": min(b.get("low", 0) for b in grp if b.get("low", 0)) or grp[-1].get("low", 0),
            "close": grp[-1].get("close", 0),
            "volume": sum(b.get("volume", 0) for b in grp),
        })
    return out


def run_backtest(code: str, date: str, kbars_1min: list[dict],
                 ticks: list[dict], streak_enter: int = _STREAK_ENTER,
                 big_lots: int = 100) -> BacktestResult:
    """執行單日回測，回 BacktestResult。

    單次逐筆重播對齊 5 分 K：每根邊界快照神手連次，並記錄「該根區間內」是否
    發生買盤竭盡（非整日累積），避免回測誤判出場。
    """
    res = BacktestResult(code=code, date=date)
    bars = aggregate_5min(kbars_1min)
    res.bars_5min = len(bars)
    if len(bars) < _GRAN_MA + 2:
        res.note = f"5分K僅 {len(bars)} 根，不足以算葛蘭碧月線({_GRAN_MA})"
        return res

    closes = [b["close"] for b in bars]
    opens = [b["open"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    times = [str(b["time"]) for b in bars]
    ma = _sma_series(closes, _GRAN_MA)

    # 單次逐筆重播：對每根 5 分 K，取「時間 <= 該根末」的逐筆推進神手，
    # 並記錄自上一根以來是否觸發買盤竭盡（bar_exhaust）。
    st = StreakState(code)
    sorted_ticks = sorted(ticks, key=lambda t: str(t.get("time", "")))
    ti = 0
    bar_streak_dir = [0] * len(bars)
    bar_streak_cnt = [0] * len(bars)
    bar_buy_exhaust = [False] * len(bars)
    for i in range(len(bars)):
        while ti < len(sorted_ticks) and \
                str(sorted_ticks[ti].get("time", "")) <= times[i]:
            tick_update(st, sorted_ticks[ti], big_lots=big_lots)
            if st.exhaust > 0:
                bar_buy_exhaust[i] = True      # 本根內曾買盤竭盡
            ti += 1
        bar_streak_dir[i] = st.streak_dir
        bar_streak_cnt[i] = st.streak_count

    pos = None
    trades: list[Trade] = []
    for i in range(_GRAN_MA, len(bars)):
        dt = {
            "dates": times[:i + 1], "close": closes[:i + 1],
            "open": opens[:i + 1], "high": highs[:i + 1], "low": lows[:i + 1],
            "ma": ma[:i + 1],
        }
        g = granville_signal(dt)
        if not g:
            continue
        px = closes[i]
        t = times[i]
        streak_ok = (bar_streak_dir[i] > 0 and bar_streak_cnt[i] >= streak_enter)
        buy_exhaust = bar_buy_exhaust[i]

        if pos is None:
            # 進場：葛蘭碧買 AND 神手連買達門檻
            if g["signal"] == "買進" and streak_ok:
                pos = Trade(entry_time=t, entry_price=px,
                            entry_rule=g["rule_name"])
        else:
            # 出場：葛蘭碧賣 OR 買盤竭盡
            if g["signal"] == "賣出" or buy_exhaust:
                pos.exit_time = t
                pos.exit_price = px
                pos.ret = (px / pos.entry_price - 1) * 100
                pos.exit_reason = "葛蘭碧賣訊" if g["signal"] == "賣出" else "買盤竭盡"
                trades.append(pos)
                pos = None

    # 收盤前平倉
    if pos is not None:
        px = closes[-1]
        pos.exit_time = times[-1]
        pos.exit_price = px
        pos.ret = (px / pos.entry_price - 1) * 100
        pos.exit_reason = "收盤平倉"
        trades.append(pos)

    res.trades = trades
    if trades:
        wins = sum(1 for t in trades if t.ret > 0)
        res.win_rate = wins / len(trades) * 100
        res.total_ret = sum(t.ret for t in trades)
        comp = 1.0
        for t in trades:
            comp *= (1 + t.ret / 100)
        res.compound_ret = (comp - 1) * 100
    else:
        res.note = "整日無符合條件的進場訊號"
    return res


def _sma_series(closes: list[float], period: int) -> list:
    """收盤序列的簡單移動平均，前段不足補 None（配合 granville 的月線格式）。"""
    out = [None] * len(closes)
    for i in range(len(closes)):
        if i + 1 >= period:
            out[i] = round(sum(closes[i + 1 - period:i + 1]) / period, 2)
    return out
