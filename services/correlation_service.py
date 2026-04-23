"""Broker-price correlation analysis using Cross-Correlation and IC."""

from __future__ import annotations

from dataclasses import dataclass
from collections import defaultdict

import numpy as np
from scipy.stats import spearmanr


@dataclass
class BrokerCorrelation:
    broker_code: str
    broker_name: str
    ic_score: float           # Information Coefficient (Spearman rank corr)
    ic_pvalue: float          # p-value of the IC
    cross_corr_max: float     # peak |cross-correlation|
    cross_corr_lag: int       # lag in days at peak (positive = broker leads)
    composite_score: float    # weighted ranking score
    active_days: int          # days with non-zero net volume
    total_days: int           # total trading days in range


def _parse_price(v) -> float | None:
    try:
        return float(str(v).replace(",", "").replace(" ", ""))
    except (ValueError, TypeError):
        return None


def _normalized_cross_corr(a: np.ndarray, b: np.ndarray, max_lag: int = 5):
    """Compute normalized cross-correlation of a and b at lags 0..max_lag.

    Positive lag means a leads b (broker action precedes price move).
    Returns (max_abs_corr, best_lag).
    """
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
        if lag == 0:
            corr = np.dot(a_norm, b_norm) / n
        else:
            # a[:-lag] vs b[lag:] — a leads b by `lag` days
            m = n - lag
            corr = np.dot(a_norm[:m], b_norm[lag:]) / m
        if abs(corr) > abs(best_corr):
            best_corr = corr
            best_lag = lag

    return abs(best_corr), best_lag


def compute_broker_correlations(
    all_brokers_daily: list[dict],
    min_active_days: int = 10,
    max_lag: int = 5,
) -> list[BrokerCorrelation]:
    """Analyze all brokers' correlation with price movement.

    Args:
        all_brokers_daily: rows from DbService.get_all_brokers_daily(),
            sorted by broker_code, broker_name, trade_date.
        min_active_days: skip brokers with fewer active days.
        max_lag: max lag days for cross-correlation.

    Returns:
        List of BrokerCorrelation sorted by composite_score descending.
    """
    # Group by (broker_code, broker_name)
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in all_brokers_daily:
        key = (row["broker_code"], row["broker_name"])
        groups[key].append(row)

    results: list[BrokerCorrelation] = []

    for (b_code, b_name), rows in groups.items():
        n = len(rows)

        # Parse net_volume and close_price arrays
        net_vol = np.array([r["net_volume"] or 0 for r in rows], dtype=float)
        prices_raw = [_parse_price(r["close_price"]) for r in rows]

        # Skip if prices can't be parsed
        if any(p is None for p in prices_raw):
            continue
        prices = np.array(prices_raw, dtype=float)

        # Active days (non-zero net)
        active = int(np.count_nonzero(net_vol))
        if active < min_active_days:
            continue

        # Forward 1-day return: ret[t] = (price[t+1] - price[t]) / price[t]
        if n < 3:
            continue
        ret = np.diff(prices) / np.where(prices[:-1] != 0, prices[:-1], 1.0)

        # ---- IC: Spearman(net_vol[t], return[t]) for t=0..n-2 ----
        # net_vol[t] → does today's broker action correlate with today→tomorrow return?
        nv_aligned = net_vol[:-1]  # same length as ret
        try:
            ic, ic_p = spearmanr(nv_aligned, ret)
            if np.isnan(ic):
                ic, ic_p = 0.0, 1.0
        except Exception:
            ic, ic_p = 0.0, 1.0

        # ---- Cross-correlation: net_vol vs return at lags 0..max_lag ----
        cc_max, cc_lag = _normalized_cross_corr(nv_aligned, ret, max_lag)

        # ---- Composite score ----
        composite = 0.6 * abs(ic) + 0.4 * cc_max

        results.append(BrokerCorrelation(
            broker_code=b_code,
            broker_name=b_name,
            ic_score=round(ic, 4),
            ic_pvalue=round(ic_p, 4),
            cross_corr_max=round(cc_max, 4),
            cross_corr_lag=cc_lag,
            composite_score=round(composite, 4),
            active_days=active,
            total_days=n,
        ))

    results.sort(key=lambda r: r.composite_score, reverse=True)
    return results
