"""策略效益評估 — 計算「主力集中度突破策略」的歷史報酬。

策略一：5/15 集中度黃金交叉
- 進場條件：當日 5日集中度 > 0 且 15日集中度 > 0
  且 5日集中度「上穿」15日集中度（前一日 ≤、當日 >）
- 進場價：訊號日收盤
- 出場：訊號日後第 N 個交易日收盤（預設 N = 4）
- 報酬：(出場價 / 進場價 − 1) × 100%

集中度公式（與分點分析頁一致）：
  (買超前15家張數合計 − 賣超前15家張數合計) / 區間成交量 × 100%
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, asdict

log = logging.getLogger(__name__)


@dataclass
class StrategySignal:
    """單一進場訊號 + 後續報酬。"""
    stock_code: str
    stock_name: str
    signal_date: str        # 訊號日 (D)
    exit_date: str          # 出場日 (D+N)
    hold_days: int          # 持有交易日數
    conc_short: float       # 訊號日 5日集中度 (%)
    conc_long: float        # 訊號日 15日集中度 (%)
    entry_price: float
    exit_price: float
    return_pct: float       # 報酬 (%)


# ---------------------------------------------------------------------------

def _pi(v) -> int:
    try:
        return int(str(v).replace(",", "").replace(" ", ""))
    except (ValueError, TypeError):
        return 0


def _pf(v):
    try:
        return float(str(v).replace(",", "").replace(" ", ""))
    except (ValueError, TypeError):
        return None


def _aggregate_by_date(broker_rows: list[dict]):
    """把 broker 列群組為 per-date 結構。

    Returns: (dates, broker_net_by_date, vol_by_date, close_by_date)
    """
    by_date_net: dict[str, dict[str, int]] = defaultdict(
        lambda: defaultdict(int))
    vol_by_date: dict[str, int] = {}
    close_by_date: dict[str, float | None] = {}
    for r in broker_rows:
        d = str(r["trade_date"])[:10]
        net = r.get("net_volume")
        if net is None:
            net = (r.get("buy_volume") or 0) - (r.get("sell_volume") or 0)
        by_date_net[d][r["broker_name"]] += net
        vol_by_date[d] = _pi(r.get("total_volume"))
        close_by_date[d] = _pf(r.get("close_price"))
    return sorted(by_date_net.keys()), by_date_net, vol_by_date, close_by_date


def _window_concentration(window_dates: list[str],
                           by_date_net: dict, vol_by_date: dict,
                           top_n: int = 15) -> float:
    """單一窗口的主力集中度 (%)。0 區間量回 0。"""
    broker_net: dict[str, int] = defaultdict(int)
    total_vol = 0
    for d in window_dates:
        for bn, net in by_date_net[d].items():
            broker_net[bn] += net
        total_vol += vol_by_date.get(d, 0)
    if total_vol <= 0:
        return 0.0
    buyers = sorted((v for v in broker_net.values() if v > 0), reverse=True)
    sellers = sorted(v for v in broker_net.values() if v < 0)
    top_buy = sum(buyers[:top_n])
    top_sell = sum(sellers[:top_n])   # 負值
    return (top_buy + top_sell) / total_vol * 100


def detect_breakout_signals(
    broker_rows: list[dict],
    stock_code: str,
    stock_name: str,
    short_window: int = 5,
    long_window: int = 15,
    hold_days: int = 4,
    top_n: int = 15,
) -> list[StrategySignal]:
    """偵測「短期集中度上穿長期集中度」訊號並算後續報酬。

    Args:
        broker_rows: 來自 db.get_all_brokers_daily 的列表（單一股票）。
        short_window: 短期窗口（預設 5 日）。
        long_window: 長期窗口（預設 15 日）。
        hold_days: 持有日數（預設 4 日）。
        top_n: 主力家數（預設 15 家）。

    Returns: 該股票所有訊號。出場資料不足者跳過。
    """
    dates, by_date_net, vol_by_date, close_by_date = _aggregate_by_date(
        broker_rows)
    n = len(dates)
    if n < long_window + 1:
        return []

    # 預計算兩個窗口的每日集中度
    conc_s: list[float | None] = [None] * n
    conc_l: list[float | None] = [None] * n
    for i in range(n):
        if i >= short_window - 1:
            conc_s[i] = _window_concentration(
                dates[i - short_window + 1: i + 1],
                by_date_net, vol_by_date, top_n)
        if i >= long_window - 1:
            conc_l[i] = _window_concentration(
                dates[i - long_window + 1: i + 1],
                by_date_net, vol_by_date, top_n)

    signals: list[StrategySignal] = []
    # 從 long_window 開始才會有 i-1 的長期集中度
    for i in range(long_window, n):
        ps, pl = conc_s[i - 1], conc_l[i - 1]
        cs, cl = conc_s[i], conc_l[i]
        if ps is None or pl is None or cs is None or cl is None:
            continue
        # 雙正 + 黃金交叉
        if not (cs > 0 and cl > 0):
            continue
        if not (ps <= pl and cs > cl):
            continue

        entry = close_by_date.get(dates[i])
        if entry is None or entry <= 0:
            continue
        exit_idx = i + hold_days
        if exit_idx >= n:
            continue  # 還沒走完 hold_days 個交易日，跳過
        exit_price = close_by_date.get(dates[exit_idx])
        if exit_price is None or exit_price <= 0:
            continue

        signals.append(StrategySignal(
            stock_code=stock_code,
            stock_name=stock_name,
            signal_date=dates[i],
            exit_date=dates[exit_idx],
            hold_days=hold_days,
            conc_short=round(cs, 2),
            conc_long=round(cl, 2),
            entry_price=round(entry, 2),
            exit_price=round(exit_price, 2),
            return_pct=round((exit_price / entry - 1) * 100, 2),
        ))
    return signals


# ---------------------------------------------------------------------------

def summarise(signals: list[StrategySignal]) -> dict:
    """彙總 signal list → KPI dict（勝率、平均報酬、期望值…）。"""
    n = len(signals)
    if n == 0:
        return {
            "count": 0, "win_rate": 0.0, "avg_return": 0.0,
            "median_return": 0.0, "best": 0.0, "worst": 0.0,
            "avg_win": 0.0, "avg_loss": 0.0, "expectancy": 0.0,
            "total_return": 0.0,
        }
    returns = [s.return_pct for s in signals]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    sorted_r = sorted(returns)
    mid = sorted_r[n // 2] if n % 2 == 1 else (
        (sorted_r[n // 2 - 1] + sorted_r[n // 2]) / 2)
    win_rate = len(wins) / n
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
    return {
        "count": n,
        "win_rate": round(win_rate * 100, 1),
        "avg_return": round(sum(returns) / n, 2),
        "median_return": round(mid, 2),
        "best": round(max(returns), 2),
        "worst": round(min(returns), 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(expectancy, 2),
        "total_return": round(sum(returns), 2),
    }


def signals_to_dicts(signals: list[StrategySignal]) -> list[dict]:
    return [asdict(s) for s in signals]
