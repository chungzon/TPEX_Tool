"""三大法人每日買賣超 — fetch from TPEX old API (per stock, with dealer hedge)."""

from __future__ import annotations

import json
import logging
import urllib.request
from dataclasses import dataclass
from datetime import datetime

log = logging.getLogger(__name__)

_API_URL = (
    "https://www.tpex.org.tw/web/stock/3insti/daily_trade/"
    "3itrade_hedge_result.php?l=zh-tw&o=json&se=EW&t=D&d={roc_date}"
)

# Column layout (0-indexed):
#  0: 代號  1: 名稱
#  2-4:  外資 (buy/sell/net)
#  5-7:  投信 (buy/sell/net)
#  8-10: 外資+投信合計
# 11-13: 自營商自行買賣 (buy/sell/net)
# 14-16: 自營商避險 (buy/sell/net)
# 17-19: 自營商合計
# 20-22: 三大法人合計
# 23:    三大法人買賣超合計


def _parse_int(v: str) -> int:
    try:
        return int(v.replace(",", "").replace(" ", ""))
    except (ValueError, TypeError, AttributeError):
        return 0


def _to_roc_date(western: str) -> str:
    """Convert 'yyyy-mm-dd' or 'yyyy/mm/dd' to ROC 'yyy/mm/dd'."""
    western = western.replace("-", "/")
    parts = western.split("/")
    if len(parts) == 3:
        y = int(parts[0]) - 1911
        return f"{y}/{parts[1]}/{parts[2]}"
    return western


@dataclass
class InstiDaily:
    stock_code: str
    stock_name: str
    trade_date: str           # yyyy-mm-dd
    foreign_buy: int          # 外資買
    foreign_sell: int         # 外資賣
    foreign_net: int          # 外資買賣超
    trust_buy: int            # 投信買
    trust_sell: int           # 投信賣
    trust_net: int            # 投信買賣超
    dealer_self_buy: int      # 自營商自行買賣-買
    dealer_self_sell: int     # 自營商自行買賣-賣
    dealer_self_net: int      # 自營商自行買賣-買賣超
    dealer_hedge_buy: int     # 自營商避險-買
    dealer_hedge_sell: int    # 自營商避險-賣
    dealer_hedge_net: int     # 自營商避險-買賣超
    three_insti_net: int      # 三大法人合計買賣超


def fetch_insti_daily(trade_date: str) -> list[InstiDaily]:
    """Fetch institutional daily trading for all OTC stocks on a given date.

    Args:
        trade_date: 'yyyy-mm-dd' format.

    Returns:
        List of InstiDaily, one per stock.
    """
    roc = _to_roc_date(trade_date)
    url = _API_URL.format(roc_date=roc)
    log.info("Fetching institutional data for %s (ROC %s)", trade_date, roc)

    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.tpex.org.tw/",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    if data.get("stat") != "ok":
        raise RuntimeError(f"TPEX 三大法人 API 回傳：{data.get('stat', '未知')}")

    tables = data.get("tables", [])
    if not tables or not tables[0].get("data"):
        return []

    # Parse the western date from API response
    raw_date = data.get("date", "")  # '20260428'
    if len(raw_date) == 8:
        w_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
    else:
        w_date = trade_date

    results: list[InstiDaily] = []
    for row in tables[0]["data"]:
        if len(row) < 24:
            continue
        code = row[0].strip()
        # Only keep 4-digit codes (regular OTC stocks)
        if not code.isdigit() or len(code) != 4:
            continue

        results.append(InstiDaily(
            stock_code=code,
            stock_name=row[1].strip(),
            trade_date=w_date,
            foreign_buy=_parse_int(row[2]),
            foreign_sell=_parse_int(row[3]),
            foreign_net=_parse_int(row[4]),
            trust_buy=_parse_int(row[5]),
            trust_sell=_parse_int(row[6]),
            trust_net=_parse_int(row[7]),
            dealer_self_buy=_parse_int(row[11]),
            dealer_self_sell=_parse_int(row[12]),
            dealer_self_net=_parse_int(row[13]),
            dealer_hedge_buy=_parse_int(row[14]),
            dealer_hedge_sell=_parse_int(row[15]),
            dealer_hedge_net=_parse_int(row[16]),
            three_insti_net=_parse_int(row[23]),
        ))

    log.info("Parsed %d stocks for %s", len(results), w_date)
    return results
