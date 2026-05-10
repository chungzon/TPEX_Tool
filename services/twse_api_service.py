"""TWSE (上市) API service — stock list, daily trading, institutional data."""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class StockVolume:
    stock_code: str
    stock_name: str
    close_price: str
    volume: int
    amount: str
    trade_count: str


def get_latest_twse_trading_date() -> str:
    """Get the latest TWSE trading date. Returns 'yyyy-mm-dd'."""
    url = "https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL?response=json"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    raw = data.get("date", "")  # '20260508'
    if len(raw) == 8:
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return ""


def _parse_int(v) -> int:
    try:
        return int(str(v).replace(",", "").replace(" ", ""))
    except (ValueError, TypeError):
        return 0


def fetch_twse_top_volume_stocks(top_n: int = 200,
                                  date: str | None = None) -> list[StockVolume]:
    """Fetch TWSE listed stocks sorted by volume descending.

    Args:
        top_n: Number of stocks to return.
        date: 'YYYYMMDD' format. If None, uses latest.
    """
    url = "https://www.twse.com.tw/exchangeReport/STOCK_DAY_ALL?response=json"
    if date:
        url += f"&date={date}"

    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    if data.get("stat") != "OK":
        raise RuntimeError(f"TWSE API error: {data.get('stat')}")

    rows = data.get("data", [])
    # fields: 證券代號, 證券名稱, 成交股數, 成交金額, 開盤價, 最高價, 最低價, 收盤價, 漲跌價差, 成交筆數

    stocks: list[StockVolume] = []
    for row in rows:
        code = str(row[0]).strip()
        if not re.match(r"^\d{4}$", code):
            continue
        vol = _parse_int(row[2])
        if vol == 0:
            continue
        stocks.append(StockVolume(
            stock_code=code,
            stock_name=str(row[1]).strip(),
            close_price=str(row[7]).strip(),
            volume=vol,
            amount=str(row[3]).strip(),
            trade_count=str(row[9]).strip(),
        ))

    stocks.sort(key=lambda s: s.volume, reverse=True)
    return stocks[:top_n]


def fetch_twse_insti_daily(trade_date: str) -> list[dict]:
    """Fetch TWSE institutional daily trading for all stocks.

    Args:
        trade_date: 'yyyy-mm-dd' or 'yyyymmdd' format.

    Returns:
        List of dicts with institutional buy/sell data per stock.
    """
    d = trade_date.replace("-", "")
    url = (f"https://www.twse.com.tw/rwd/zh/fund/T86"
           f"?response=json&date={d}&selectType=ALL")
    log.info("Fetching TWSE institutional data for %s", d)

    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    if data.get("stat") != "OK":
        raise RuntimeError(f"TWSE insti API: {data.get('stat')}")

    rows = data.get("data", [])
    # fields: 0代號, 1名稱, 2外資買, 3外資賣, 4外資淨,
    #         5外資自營買, 6外資自營賣, 7外資自營淨,
    #         8投信買, 9投信賣, 10投信淨,
    #         11自營合計淨, 12自營自行買, 13自營自行賣, 14自營自行淨,
    #         15自營避險買, 16自營避險賣, 17自營避險淨, 18三法合計淨

    results = []
    for row in rows:
        if len(row) < 19:
            continue
        code = str(row[0]).strip()
        if not re.match(r"^\d{4}$", code):
            continue
        results.append({
            "stock_code": code,
            "stock_name": str(row[1]).strip(),
            "trade_date": f"{d[:4]}-{d[4:6]}-{d[6:8]}",
            "foreign_buy": _parse_int(row[2]),
            "foreign_sell": _parse_int(row[3]),
            "foreign_net": _parse_int(row[4]),
            "trust_buy": _parse_int(row[8]),
            "trust_sell": _parse_int(row[9]),
            "trust_net": _parse_int(row[10]),
            "dealer_self_buy": _parse_int(row[12]),
            "dealer_self_sell": _parse_int(row[13]),
            "dealer_self_net": _parse_int(row[14]),
            "dealer_hedge_buy": _parse_int(row[15]),
            "dealer_hedge_sell": _parse_int(row[16]),
            "dealer_hedge_net": _parse_int(row[17]),
            "three_insti_net": _parse_int(row[18]),
        })

    log.info("TWSE insti: %d stocks for %s", len(results), d)
    return results
