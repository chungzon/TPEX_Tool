"""Direct TPEX API service for fetching OTC stock market data."""

from __future__ import annotations

import re
import urllib.request
import json
from dataclasses import dataclass


_DAILY_TRADING_URL = (
    "https://www.tpex.org.tw/www/zh-tw/afterTrading/otc"
    "?response=json&type=EW"
)


@dataclass
class StockVolume:
    stock_code: str
    stock_name: str
    close_price: str
    volume: int          # 成交股數
    amount: str          # 成交金額
    trade_count: str     # 成交筆數


def fetch_top_volume_stocks(top_n: int = 200, date: str | None = None) -> list[StockVolume]:
    """Fetch all OTC stocks and return top N by trading volume.

    Args:
        top_n: Number of stocks to return (default 200).
        date:  Western date 'YYYY/MM/DD'. If None, uses latest available date.

    Returns:
        List of StockVolume sorted by volume descending.
    """
    url = _DAILY_TRADING_URL
    if date:
        url += f"&date={date}"

    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://www.tpex.org.tw/zh-tw/afterTrading/otc.html",
    })

    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    if data.get("stat") != "ok":
        raise RuntimeError(f"TPEX API 回傳錯誤: {data.get('stat', '未知')}")

    tables = data.get("tables", [])
    if not tables or not tables[0].get("data"):
        raise RuntimeError("TPEX API 無資料（可能非交易日或尚未更新）")

    rows = tables[0]["data"]
    # Column indices: 0=代號, 1=名稱, 2=收盤, 7=成交股數, 8=成交金額, 9=成交筆數

    stocks: list[StockVolume] = []
    for row in rows:
        code = str(row[0]).strip()
        # Only keep 4-digit stock codes (regular OTC stocks)
        if not re.match(r"^\d{4}$", code):
            continue

        vol = _parse_int(row[7]) if len(row) > 7 else 0
        if vol == 0:
            continue

        stocks.append(StockVolume(
            stock_code=code,
            stock_name=str(row[1]).strip() if len(row) > 1 else "",
            close_price=str(row[2]).strip() if len(row) > 2 else "",
            volume=vol,
            amount=str(row[8]).strip() if len(row) > 8 else "",
            trade_count=str(row[9]).strip() if len(row) > 9 else "",
        ))

    # Sort by volume descending, take top N
    stocks.sort(key=lambda s: s.volume, reverse=True)
    return stocks[:top_n]


def _parse_int(v) -> int:
    try:
        return int(str(v).replace(",", "").replace(" ", ""))
    except (ValueError, TypeError):
        return 0
