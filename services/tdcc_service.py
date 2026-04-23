"""TDCC 集保戶股權分散表 — fetch shareholding distribution from OpenData API."""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass
from collections import defaultdict

log = logging.getLogger(__name__)

_API_URL = "https://openapi.tdcc.com.tw/v1/opendata/1-5"

# Holding level mapping (持股分級 1-17)
# 1張 = 1,000股
_LEVEL_LABELS = {
    "1":  "1-999",
    "2":  "1,000-5,000",
    "3":  "5,001-10,000",
    "4":  "10,001-15,000",
    "5":  "15,001-20,000",
    "6":  "20,001-30,000",
    "7":  "30,001-40,000",
    "8":  "40,001-50,000",
    "9":  "50,001-100,000",
    "10": "100,001-200,000",
    "11": "200,001-400,000",
    "12": "400,001-600,000",
    "13": "600,001-800,000",
    "14": "800,001-1,000,000",
    "15": "1,000,001以上",
    "17": "合計",
}

_RETAIL_LEVELS = {"1", "2", "3", "4", "5"}
_MID_LEVELS = {"6", "7", "8", "9", "10", "11"}
_BIG_LEVELS = {"12", "13", "14", "15"}


@dataclass
class HoldingLevel:
    level: str
    label: str
    holders: int
    shares: int
    pct: float


@dataclass
class StockDistribution:
    stock_code: str
    report_date: str        # yyyy-mm-dd
    levels: list[HoldingLevel]
    retail_pct: float       # 散戶 (< 20張)
    mid_pct: float          # 中實戶 (20-400張)
    big_pct: float          # 大戶 (> 400張)
    total_holders: int
    total_shares: int


# ---------------------------------------------------------------------------
# Raw data cache — the API returns ALL stocks at once, so we cache it
# to avoid repeated large downloads within the same session.
# ---------------------------------------------------------------------------

_raw_cache: list[dict] | None = None
_cache_date: str = ""


def _fetch_raw() -> list[dict]:
    """Fetch the full TDCC dataset (all stocks), with session cache."""
    global _raw_cache, _cache_date
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")

    if _raw_cache is not None and _cache_date == today:
        return _raw_cache

    log.info("Fetching TDCC OpenData (all stocks)...")
    req = urllib.request.Request(_API_URL, headers={
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    _raw_cache = data
    _cache_date = today
    log.info("TDCC data fetched: %d rows", len(data))
    return data


def _parse_stock(rows: list[dict], stock_code: str) -> StockDistribution | None:
    """Parse rows belonging to a single stock into StockDistribution."""
    if not rows:
        return None

    raw_date = rows[0].get("資料日期", "")
    report_date = (
        f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
        if len(raw_date) == 8 else raw_date
    )

    levels: list[HoldingLevel] = []
    retail_pct = 0.0
    mid_pct = 0.0
    big_pct = 0.0
    total_holders = 0
    total_shares = 0

    for r in rows:
        lv = r.get("持股分級", "").strip()
        label = _LEVEL_LABELS.get(lv, lv)
        holders = _parse_int(r.get("人數", "0"))
        shares = _parse_int(r.get("股數", "0"))
        pct = _parse_float(r.get("占集保庫存數比例%", "0"))

        if lv == "17":
            total_holders = holders
            total_shares = shares
            continue
        if lv == "16":
            continue

        levels.append(HoldingLevel(
            level=lv, label=label,
            holders=holders, shares=shares, pct=pct,
        ))

        if lv in _RETAIL_LEVELS:
            retail_pct += pct
        elif lv in _MID_LEVELS:
            mid_pct += pct
        elif lv in _BIG_LEVELS:
            big_pct += pct

    return StockDistribution(
        stock_code=stock_code,
        report_date=report_date,
        levels=levels,
        retail_pct=round(retail_pct, 2),
        mid_pct=round(mid_pct, 2),
        big_pct=round(big_pct, 2),
        total_holders=total_holders,
        total_shares=total_shares,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_distribution(stock_code: str) -> StockDistribution | None:
    """Fetch latest shareholding distribution for a single stock."""
    data = _fetch_raw()
    target = stock_code.strip()
    rows = [r for r in data if r.get("證券代號", "").strip() == target]
    if not rows:
        return None
    return _parse_stock(rows, target)


def fetch_distributions_batch(stock_codes: list[str]) -> dict[str, StockDistribution]:
    """Fetch distributions for multiple stocks in one API call.

    Returns a dict mapping stock_code -> StockDistribution.
    Only includes stocks that were found in the TDCC data.
    """
    data = _fetch_raw()

    # Group all rows by stock code
    by_code: dict[str, list[dict]] = defaultdict(list)
    target_set = {c.strip() for c in stock_codes}
    for r in data:
        code = r.get("證券代號", "").strip()
        if code in target_set:
            by_code[code].append(r)

    results: dict[str, StockDistribution] = {}
    for code, rows in by_code.items():
        dist = _parse_stock(rows, code)
        if dist:
            results[code] = dist

    return results


def _parse_int(v) -> int:
    try:
        return int(str(v).replace(",", "").replace(" ", ""))
    except (ValueError, TypeError):
        return 0


def _parse_float(v) -> float:
    try:
        return float(str(v).replace(",", "").replace(" ", ""))
    except (ValueError, TypeError):
        return 0.0
