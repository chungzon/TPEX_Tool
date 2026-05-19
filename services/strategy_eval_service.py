"""策略效益評估 — 計算「主力集中度突破策略」的歷史報酬。

策略一：5/15 集中度黃金交叉
- 進場條件：5日集中度「上穿」15日集中度（前一日 ≤、當日 >）
  （不限正負 — 兩線皆負時的交叉視為賣壓減弱的反轉訊號）
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
        # 黃金交叉（短期上穿長期）— 不限制正負，
        # 賣壓減弱（兩線皆負時的交叉）也算反轉訊號
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


# ---------------------------------------------------------------------------
# Filter: 主力集中度即將黃金交叉（候選掃描）
# ---------------------------------------------------------------------------

@dataclass
class ImminentCrossCandidate:
    """短期集中度即將上穿長期集中度的候選個股。"""
    stock_code: str
    stock_name: str
    trade_date: str
    close_price: float
    conc_short: float          # 當日短期集中度 (%)
    conc_long: float           # 當日長期集中度 (%)
    gap: float                 # long − short（正值代表還沒交叉）
    prev_gap: float            # 前一交易日的 gap
    narrowing: float           # prev_gap − gap（>0 = 縮窄中）
    eta_days: float | None     # 依 narrowing 速率推估幾日內交叉
    short_slope: float         # short 較前一日的變化量（正 = 上升）


def find_imminent_crossovers(
    grouped_rows: dict[str, list[dict]],
    trade_date: str,
    short_window: int = 5,
    long_window: int = 15,
    top_n: int = 15,
    max_gap_pct: float = 2.0,
    require_narrowing: bool = True,
    eta_cap: float = 30.0,
) -> list[ImminentCrossCandidate]:
    """掃所有個股，找出當日「短期集中度即將上穿長期集中度」的候選。

    條件：
      • 當日 short ≤ long（還沒交叉）
      • gap (= long − short) ≤ max_gap_pct
      • 當日「最近交易日」必須等於 trade_date（避免拿到停牌的舊資料）
      • 若 require_narrowing：要求 prev_gap > gap（gap 在收窄）

    集中度本身不限制正負 — 兩線皆負（賣壓中）時的即將交叉也算反轉候選。

    Args:
        grouped_rows: {stock_code: [broker_row, ...]}，可由
            ``itertools.groupby`` 或 dict 累積取得。每筆 broker_row 與
            ``db.get_broker_history_range`` 回傳格式相同。
        trade_date: 'yyyy-mm-dd'，當日。
        eta_cap: 推估天數上限，超過就顯示為 cap 值。

    Returns:
        排序好的候選清單（eta_days 升冪、無 eta 者按 gap 升冪排在後段）。
    """
    out: list[ImminentCrossCandidate] = []
    for code, rows in grouped_rows.items():
        if not rows:
            continue
        dates, by_date_net, vol_by_date, close_by_date = _aggregate_by_date(rows)
        n = len(dates)
        if n < long_window + 1:
            continue
        # 嚴格要求當日就是 trade_date（避免停牌資料）
        if dates[-1] != trade_date:
            continue

        # 取最後一日與前一交易日
        i = n - 1
        cur_dates_s = dates[i - short_window + 1: i + 1]
        cur_dates_l = dates[i - long_window + 1: i + 1]
        prev_dates_s = dates[i - short_window: i]
        prev_dates_l = dates[i - long_window: i]

        cs = _window_concentration(cur_dates_s, by_date_net, vol_by_date, top_n)
        cl = _window_concentration(cur_dates_l, by_date_net, vol_by_date, top_n)
        ps = _window_concentration(prev_dates_s, by_date_net, vol_by_date, top_n)
        pl = _window_concentration(prev_dates_l, by_date_net, vol_by_date, top_n)

        if cs > cl:
            continue  # 已經交叉，不算「即將」
        gap = cl - cs
        if gap > max_gap_pct:
            continue
        prev_gap = pl - ps
        narrowing = prev_gap - gap
        if require_narrowing and narrowing <= 0:
            continue

        # 推估天數 = 目前 gap / 每日收窄速率
        eta: float | None = None
        if narrowing > 0:
            raw_eta = gap / narrowing
            eta = round(min(raw_eta, eta_cap), 1)

        name = rows[0].get("stock_name") or code
        close = close_by_date.get(dates[i]) or 0.0
        out.append(ImminentCrossCandidate(
            stock_code=code,
            stock_name=name,
            trade_date=dates[i],
            close_price=round(close, 2),
            conc_short=round(cs, 2),
            conc_long=round(cl, 2),
            gap=round(gap, 2),
            prev_gap=round(prev_gap, 2),
            narrowing=round(narrowing, 2),
            eta_days=eta,
            short_slope=round(cs - ps, 2),
        ))

    # 排序：有 eta 的按 eta 升冪，沒 eta 的按 gap 升冪排後面
    out.sort(key=lambda c: (
        c.eta_days is None, c.eta_days if c.eta_days is not None else c.gap))
    return out


def candidates_to_dicts(
    cands: list[ImminentCrossCandidate],
) -> list[dict]:
    return [asdict(c) for c in cands]


# ---------------------------------------------------------------------------
# 三大法人連續買超 streak（給策略三搭配集中度過濾使用）
# ---------------------------------------------------------------------------

INSTI_TYPES = ("foreign", "trust", "dealer")
INSTI_LABELS = {"foreign": "外資", "trust": "投信", "dealer": "自營"}


def _insti_net(row: dict, type_key: str) -> int:
    """取出指定法人在這筆 row 的當日淨買賣（張數）。

    ``dealer`` = 自營商自行 + 自營商避險（市場慣例的「自營合計」）。
    """
    if type_key == "foreign":
        return row.get("foreign_net") or 0
    if type_key == "trust":
        return row.get("trust_net") or 0
    if type_key == "dealer":
        return ((row.get("dealer_self_net") or 0)
                + (row.get("dealer_hedge_net") or 0))
    return 0


def insti_buy_streak(rows_for_stock: list[dict], trade_date: str,
                      type_key: str) -> int:
    """指定法人在 trade_date 之前（含當日）連續買超的天數。

    rows_for_stock 必須是同一檔股票的 InstiDailyTrade 列、依日期升冪排序。
    嚴格要求最後一筆日期 = trade_date —— 否則代表該股當日沒法人資料，
    回 0（不採信過時 streak）。

    遇到「淨買 ≤ 0」即中斷，從尾巴往前數。
    """
    if not rows_for_stock:
        return 0
    if str(rows_for_stock[-1]["trade_date"])[:10] != trade_date:
        return 0
    streak = 0
    for r in reversed(rows_for_stock):
        if _insti_net(r, type_key) > 0:
            streak += 1
        else:
            break
    return streak
