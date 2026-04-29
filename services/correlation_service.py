"""Broker-price correlation analysis with volume behaviour features.

Scoring dimensions:
  1. IC (Information Coefficient) — rank correlation of net vs next-day return
  2. Cross-Correlation — peak correlation at lag 0..5 days
  3. Consecutive-direction streak — how many days in a row the broker
     buys or sells in the same direction (planned accumulation signal)
  4. Volume share — broker's (buy+sell) as % of total stock volume
  5. Buy/sell asymmetry — how one-sided the broker's trading is
"""

from __future__ import annotations

from dataclasses import dataclass
from collections import defaultdict

import numpy as np
from scipy.stats import spearmanr


@dataclass
class BrokerCorrelation:
    broker_code: str
    broker_name: str
    # Core correlation
    ic_score: float           # Spearman rank corr (net vs next-day return)
    ic_pvalue: float
    cross_corr_max: float     # peak |cross-correlation|
    cross_corr_lag: int       # lag in days at peak
    # Volume behaviour
    avg_streak: float         # average consecutive same-direction days
    max_streak: int           # longest streak
    volume_share_pct: float   # broker volume / total stock volume (%)
    asymmetry: float          # |buy - sell| / (buy + sell), 0~1
    # Summary
    composite_score: float    # weighted ranking score
    active_days: int
    total_days: int


def _parse_price(v) -> float | None:
    try:
        return float(str(v).replace(",", "").replace(" ", ""))
    except (ValueError, TypeError):
        return None


def _parse_vol(v) -> int:
    try:
        return int(str(v).replace(",", "").replace(" ", ""))
    except (ValueError, TypeError):
        return 0


def _normalized_cross_corr(a: np.ndarray, b: np.ndarray, max_lag: int = 5):
    n = len(a)
    if n < max_lag + 2:
        return 0.0, 0
    a_std = np.std(a)
    b_std = np.std(b)
    if a_std < 1e-12 or b_std < 1e-12:
        return 0.0, 0
    a_norm = (a - np.mean(a)) / a_std
    b_norm = (b - np.mean(b)) / b_std

    best_corr = 0.0
    best_lag = 0
    for lag in range(0, min(max_lag + 1, n - 1)):
        m = n - lag
        if lag == 0:
            corr = np.dot(a_norm, b_norm) / n
        else:
            corr = np.dot(a_norm[:m], b_norm[lag:]) / m
        if abs(corr) > abs(best_corr):
            best_corr = corr
            best_lag = lag
    return abs(best_corr), best_lag


def _calc_streaks(net_vol: np.ndarray) -> tuple[float, int]:
    """Calculate average and max consecutive same-direction streaks.

    Only counts days where net != 0. Direction = sign of net.
    Returns (avg_streak_length, max_streak_length).
    """
    streaks: list[int] = []
    current = 0
    prev_sign = 0

    for v in net_vol:
        if v == 0:
            if current > 0:
                streaks.append(current)
            current = 0
            prev_sign = 0
            continue
        sign = 1 if v > 0 else -1
        if sign == prev_sign:
            current += 1
        else:
            if current > 0:
                streaks.append(current)
            current = 1
            prev_sign = sign

    if current > 0:
        streaks.append(current)

    if not streaks:
        return 0.0, 0
    return round(np.mean(streaks), 2), max(streaks)


def compute_broker_correlations(
    all_brokers_daily: list[dict],
    min_active_days: int = 10,
    max_lag: int = 5,
) -> list[BrokerCorrelation]:
    """Analyze all brokers' correlation with price movement.

    Composite score weights:
      IC              0.30  — directional predictiveness
      Cross-corr      0.20  — lead-lag relationship
      Streak          0.20  — planned accumulation behaviour
      Volume share    0.15  — market impact ability
      Asymmetry       0.15  — one-sided (institutional) behaviour
    """
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in all_brokers_daily:
        key = (row["broker_code"], row["broker_name"])
        groups[key].append(row)

    results: list[BrokerCorrelation] = []

    for (b_code, b_name), rows in groups.items():
        n = len(rows)

        net_vol = np.array([r["net_volume"] or 0 for r in rows], dtype=float)
        buy_vol = np.array([r.get("buy_volume") or 0 for r in rows], dtype=float)
        sell_vol = np.array([r.get("sell_volume") or 0 for r in rows], dtype=float)
        prices_raw = [_parse_price(r["close_price"]) for r in rows]
        total_vol_raw = [_parse_vol(r.get("total_volume", 0)) for r in rows]

        if any(p is None for p in prices_raw):
            continue
        prices = np.array(prices_raw, dtype=float)
        total_vol = np.array(total_vol_raw, dtype=float)

        active = int(np.count_nonzero(net_vol))
        if active < min_active_days:
            continue
        if n < 3:
            continue

        # ---- 1. IC ----
        ret = np.diff(prices) / np.where(prices[:-1] != 0, prices[:-1], 1.0)
        nv_aligned = net_vol[:-1]
        try:
            ic, ic_p = spearmanr(nv_aligned, ret)
            if np.isnan(ic):
                ic, ic_p = 0.0, 1.0
        except Exception:
            ic, ic_p = 0.0, 1.0

        # ---- 2. Cross-correlation ----
        cc_max, cc_lag = _normalized_cross_corr(nv_aligned, ret, max_lag)

        # ---- 3. Consecutive streaks ----
        avg_streak, max_streak = _calc_streaks(net_vol)
        # Normalize: streak of 5+ days is strong signal, cap at 10
        streak_score = min(avg_streak / 5.0, 1.0)

        # ---- 4. Volume share ----
        broker_total = float(np.sum(buy_vol + sell_vol))
        stock_total = float(np.sum(total_vol))
        vol_share_pct = (broker_total / stock_total * 100) if stock_total > 0 else 0.0
        # Normalize: 1% share is significant, cap at 5%
        vol_share_score = min(vol_share_pct / 1.0, 1.0)

        # ---- 5. Asymmetry ----
        total_buy = float(np.sum(buy_vol))
        total_sell = float(np.sum(sell_vol))
        total_both = total_buy + total_sell
        asymmetry = abs(total_buy - total_sell) / total_both if total_both > 0 else 0.0
        # Already 0~1, higher = more one-sided

        # ---- Composite ----
        composite = (
            0.30 * abs(ic)
            + 0.20 * cc_max
            + 0.20 * streak_score
            + 0.15 * vol_share_score
            + 0.15 * asymmetry
        )

        results.append(BrokerCorrelation(
            broker_code=b_code,
            broker_name=b_name,
            ic_score=round(ic, 4),
            ic_pvalue=round(ic_p, 4),
            cross_corr_max=round(cc_max, 4),
            cross_corr_lag=cc_lag,
            avg_streak=avg_streak,
            max_streak=max_streak,
            volume_share_pct=round(vol_share_pct, 3),
            asymmetry=round(asymmetry, 3),
            composite_score=round(composite, 4),
            active_days=active,
            total_days=n,
        ))

    results.sort(key=lambda r: r.composite_score, reverse=True)
    return results
