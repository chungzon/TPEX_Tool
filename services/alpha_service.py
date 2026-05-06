"""Branch Alpha Model — anomaly detection, event study, clustering.

Phase 1: Anomaly detection + event study
Phase 2: Branch Alpha + Branch×Stock Alpha
Phase 3: Clustering + composite signal score
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class BranchSignal:
    """One signal record: a branch's activity on a stock on a date."""
    stock_code: str
    stock_name: str
    trade_date: str
    broker_code: str
    broker_name: str
    # Raw data
    buy_volume: int = 0
    sell_volume: int = 0
    net_volume: int = 0
    total_volume: int = 0       # stock total volume that day
    close_price: float = 0.0
    # Phase 1: Anomaly
    net_buy_z: float = 0.0      # Z-score vs 60-day history
    volume_share: float = 0.0   # branch buy / total volume %
    consecutive_days: int = 0   # consecutive same-direction days
    # Phase 2: Alpha
    branch_alpha: float = 0.0       # this branch's overall historical alpha
    branch_stock_alpha: float = 0.0 # this branch × this stock alpha
    branch_win_rate: float = 0.0    # D+1~D+5 win rate
    branch_avg_return: float = 0.0  # D+1~D+5 avg return
    # Phase 3: Clustering
    cluster_score: float = 0.0  # how many branches bought same stock same day
    # Event study (filled later if future data available)
    d1_return: float | None = None
    d3_return: float | None = None
    d5_return: float | None = None
    d1_max_high_pct: float | None = None
    # Composite
    signal_score: float = 0.0


@dataclass
class BranchAlpha:
    """Aggregated alpha stats for one branch (or branch×stock)."""
    broker_code: str
    broker_name: str
    stock_code: str = ""       # empty = overall, filled = per-stock
    total_signals: int = 0
    buy_signals: int = 0
    d1_win_count: int = 0
    d1_avg_return: float = 0.0
    d3_avg_return: float = 0.0
    d5_avg_return: float = 0.0
    d1_avg_max_high: float = 0.0
    d1_avg_max_drawdown: float = 0.0
    win_rate: float = 0.0
    alpha_score: float = 0.0


def _parse_price(v) -> float:
    try:
        return float(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return 0.0


def _parse_vol(v) -> int:
    try:
        return int(str(v).replace(",", ""))
    except (ValueError, TypeError):
        return 0


# =====================================================================
# Phase 1: Anomaly Detection + Event Study
# =====================================================================

def compute_signals_for_date(
    broker_rows: list[dict],
    history_rows: list[dict],
    price_history: dict[str, list[dict]],
    target_date: str,
) -> list[BranchSignal]:
    """Compute branch signals for a single trading date.

    Args:
        broker_rows: from db.get_all_broker_buys_by_date(target_date)
        history_rows: from db.get_all_broker_buys_by_date for past 60 days
            (list of dicts with stock_code, broker_name, buy_volume, sell_volume, etc.)
        price_history: {stock_code: [{trade_date, close_price}, ...]} sorted by date
        target_date: 'yyyy-mm-dd'

    Returns:
        List of BranchSignal for branches with notable activity.
    """
    # Build today's per-branch-per-stock data
    today: dict[tuple[str, str, str], dict] = {}  # (stock, b_code, b_name) -> data
    stock_totals: dict[str, int] = defaultdict(int)  # stock -> total vol
    stock_names: dict[str, str] = {}

    for r in broker_rows:
        code = r["stock_code"]
        bcode = r.get("broker_code", "")
        bname = r["broker_name"]
        bv = r["buy_volume"] or 0
        sv = r["sell_volume"] or 0
        stock_totals[code] += bv + sv
        stock_names[code] = r.get("stock_name", "")
        key = (code, bcode, bname)
        if key not in today:
            today[key] = {"buy": 0, "sell": 0, "close": _parse_price(r.get("close_price", 0))}
        today[key]["buy"] += bv
        today[key]["sell"] += sv

    # Build history: per (stock, branch) -> list of daily net values
    hist_nets: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    hist_dirs: dict[tuple[str, str, str], list[int]] = defaultdict(list)  # +1/-1/0

    for r in history_rows:
        code = r["stock_code"]
        bcode = r.get("broker_code", "")
        bname = r["broker_name"]
        bv = r["buy_volume"] or 0
        sv = r["sell_volume"] or 0
        net = bv - sv
        key = (code, bcode, bname)
        hist_nets[key].append(net)
        hist_dirs[key].append(1 if net > 0 else (-1 if net < 0 else 0))

    signals: list[BranchSignal] = []

    for (stock, bcode, bname), d in today.items():
        net = d["buy"] - d["sell"]
        total_vol = stock_totals.get(stock, 0)
        if total_vol <= 0:
            continue

        # Volume share
        vol_share = d["buy"] / total_vol * 100

        # Z-score
        key = (stock, bcode, bname)
        past = hist_nets.get(key, [])
        if len(past) >= 5:
            mean = float(np.mean(past))
            std = float(np.std(past))
            z = (net - mean) / std if std > 1e-6 else 0.0
        else:
            z = 0.0

        # Consecutive days
        dirs = hist_dirs.get(key, [])
        today_dir = 1 if net > 0 else (-1 if net < 0 else 0)
        consec = 1 if today_dir != 0 else 0
        for d_val in reversed(dirs):
            if d_val == today_dir and today_dir != 0:
                consec += 1
            else:
                break

        # Event study: D+1, D+3, D+5 returns
        prices = price_history.get(stock, [])
        d1_ret, d3_ret, d5_ret, d1_max = None, None, None, None
        if prices:
            # Find target_date index
            date_idx = None
            for i, p in enumerate(prices):
                if str(p["trade_date"])[:10] == target_date:
                    date_idx = i
                    break
            if date_idx is not None:
                close_t = _parse_price(prices[date_idx]["close_price"])
                if close_t > 0:
                    if date_idx + 1 < len(prices):
                        c1 = _parse_price(prices[date_idx + 1]["close_price"])
                        d1_ret = round((c1 - close_t) / close_t * 100, 3)
                    if date_idx + 3 < len(prices):
                        c3 = _parse_price(prices[date_idx + 3]["close_price"])
                        d3_ret = round((c3 - close_t) / close_t * 100, 3)
                    if date_idx + 5 < len(prices):
                        c5 = _parse_price(prices[date_idx + 5]["close_price"])
                        d5_ret = round((c5 - close_t) / close_t * 100, 3)
                    # D+1 max high
                    if date_idx + 1 < len(prices):
                        h1 = _parse_price(prices[date_idx + 1].get("high_price", 0))
                        if h1 > 0:
                            d1_max = round((h1 - close_t) / close_t * 100, 3)

        # Only keep notable signals (|Z| >= 1.5 or volume_share >= 3%)
        if abs(z) >= 1.5 or vol_share >= 3.0 or consec >= 3:
            sig = BranchSignal(
                stock_code=stock,
                stock_name=stock_names.get(stock, ""),
                trade_date=target_date,
                broker_code=bcode,
                broker_name=bname,
                buy_volume=d["buy"],
                sell_volume=d["sell"],
                net_volume=net,
                total_volume=total_vol,
                close_price=d["close"],
                net_buy_z=round(z, 3),
                volume_share=round(vol_share, 2),
                consecutive_days=consec,
                d1_return=d1_ret,
                d3_return=d3_ret,
                d5_return=d5_ret,
                d1_max_high_pct=d1_max,
            )
            signals.append(sig)

    return signals


# =====================================================================
# Phase 2: Branch Alpha + Branch×Stock Alpha
# =====================================================================

def compute_branch_alphas(
    all_signals: list[BranchSignal],
) -> tuple[list[BranchAlpha], list[BranchAlpha]]:
    """Compute branch-level and branch×stock-level alpha from historical signals.

    Returns:
        (branch_alphas, branch_stock_alphas)
    """
    # Group by branch
    by_branch: dict[tuple[str, str], list[BranchSignal]] = defaultdict(list)
    by_branch_stock: dict[tuple[str, str, str], list[BranchSignal]] = defaultdict(list)

    for s in all_signals:
        by_branch[(s.broker_code, s.broker_name)].append(s)
        by_branch_stock[(s.broker_code, s.broker_name, s.stock_code)].append(s)

    branch_alphas = []
    for (bcode, bname), sigs in by_branch.items():
        alpha = _calc_alpha(bcode, bname, "", sigs)
        if alpha:
            branch_alphas.append(alpha)

    branch_stock_alphas = []
    for (bcode, bname, scode), sigs in by_branch_stock.items():
        alpha = _calc_alpha(bcode, bname, scode, sigs)
        if alpha:
            branch_stock_alphas.append(alpha)

    branch_alphas.sort(key=lambda a: a.alpha_score, reverse=True)
    branch_stock_alphas.sort(key=lambda a: a.alpha_score, reverse=True)
    return branch_alphas, branch_stock_alphas


def _calc_alpha(bcode: str, bname: str, scode: str,
                sigs: list[BranchSignal]) -> BranchAlpha | None:
    buy_sigs = [s for s in sigs if s.net_volume > 0]
    if len(buy_sigs) < 3:
        return None

    d1_rets = [s.d1_return for s in buy_sigs if s.d1_return is not None]
    d3_rets = [s.d3_return for s in buy_sigs if s.d3_return is not None]
    d5_rets = [s.d5_return for s in buy_sigs if s.d5_return is not None]
    d1_highs = [s.d1_max_high_pct for s in buy_sigs if s.d1_max_high_pct is not None]

    if not d1_rets:
        return None

    d1_wins = sum(1 for r in d1_rets if r > 0)
    win_rate = d1_wins / len(d1_rets) if d1_rets else 0
    d1_avg = float(np.mean(d1_rets)) if d1_rets else 0
    d3_avg = float(np.mean(d3_rets)) if d3_rets else 0
    d5_avg = float(np.mean(d5_rets)) if d5_rets else 0
    d1_high_avg = float(np.mean(d1_highs)) if d1_highs else 0

    # Drawdown: negative returns
    d1_negs = [r for r in d1_rets if r < 0]
    d1_dd = float(np.mean(d1_negs)) if d1_negs else 0

    # Alpha score: weighted combination
    alpha = (
        0.30 * win_rate
        + 0.25 * min(d1_avg / 3.0, 1.0)   # cap at 3% avg return
        + 0.20 * min(d1_high_avg / 5.0, 1.0)
        + 0.15 * min(d3_avg / 5.0, 1.0)
        + 0.10 * max(d1_dd / -5.0, -1.0)   # penalize drawdown
    )

    return BranchAlpha(
        broker_code=bcode,
        broker_name=bname,
        stock_code=scode,
        total_signals=len(sigs),
        buy_signals=len(buy_sigs),
        d1_win_count=d1_wins,
        d1_avg_return=round(d1_avg, 3),
        d3_avg_return=round(d3_avg, 3),
        d5_avg_return=round(d5_avg, 3),
        d1_avg_max_high=round(d1_high_avg, 3),
        d1_avg_max_drawdown=round(d1_dd, 3),
        win_rate=round(win_rate, 3),
        alpha_score=round(alpha, 4),
    )


# =====================================================================
# Phase 3: Clustering + Composite Signal Score
# =====================================================================

def enrich_cluster_scores(signals: list[BranchSignal]) -> None:
    """Add cluster_score: how many distinct branches bought the same stock
    on the same date with net > 0. Modifies signals in-place."""
    # Count distinct buy-branches per stock per date
    counts: dict[tuple[str, str], set[str]] = defaultdict(set)
    for s in signals:
        if s.net_volume > 0:
            counts[(s.stock_code, s.trade_date)].add(s.broker_name)

    for s in signals:
        if s.net_volume > 0:
            s.cluster_score = len(counts.get((s.stock_code, s.trade_date), set()))


def compute_composite_scores(
    signals: list[BranchSignal],
    branch_alpha_map: dict[tuple[str, str], float],
    branch_stock_alpha_map: dict[tuple[str, str, str], float],
) -> None:
    """Compute final signal_score. Modifies signals in-place.

    Weights:
      0.25 * net_buy_z (anomaly)
      0.20 * branch_alpha (historical effectiveness)
      0.15 * branch_stock_alpha (pair-specific effectiveness)
      0.15 * cluster_score (herding)
      0.15 * volume_share (market impact)
      0.10 * consecutive_days (persistence)
    """
    # Normalize helpers
    def _norm(val, cap):
        return min(abs(val) / cap, 1.0) if cap > 0 else 0

    for s in signals:
        ba = branch_alpha_map.get((s.broker_code, s.broker_name), 0)
        bsa = branch_stock_alpha_map.get(
            (s.broker_code, s.broker_name, s.stock_code), 0)

        s.branch_alpha = ba
        s.branch_stock_alpha = bsa

        score = (
            0.25 * _norm(s.net_buy_z, 4.0)
            + 0.20 * min(ba, 1.0)
            + 0.15 * min(bsa, 1.0)
            + 0.15 * _norm(s.cluster_score, 10.0)
            + 0.15 * _norm(s.volume_share, 10.0)
            + 0.10 * _norm(s.consecutive_days, 5.0)
        )
        s.signal_score = round(score, 4)
