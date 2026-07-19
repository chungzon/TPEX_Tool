"""InstiFlowService — 全市場三大法人資金流（TWSE 官方總計，免登入）。

來源：TWSE `BFI82U`（三大法人買賣金額統計表），回指定日全市場買/賣/差額（元）。
本服務彙整為 外資 / 投信 / 自營 / 合計 淨額（億元），並可往回收集近 N 個交易日。
非交易日該端點回 stat != OK → 視為無資料跳過。純 I/O，不碰 DB。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import requests

log = logging.getLogger(__name__)

_URL = "https://www.twse.com.tw/rwd/zh/fund/BFI82U"
_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
_TIMEOUT = 10
_YI = 1e8   # 億元


def _num(v) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def fetch_market_insti(date: str) -> dict | None:
    """指定日（yyyymmdd）全市場三大法人淨額（億元）。非交易日回 None。

    keys: date, foreign, trust, dealer, total（買賣差額 / 1e8，正=買超）。
    """
    try:
        r = requests.get(
            _URL, params={"dayDate": date, "type": "day", "response": "json"},
            headers=_HEADERS, timeout=_TIMEOUT,
        )
        d = r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("fetch_market_insti failed (%s): %s", date, e)
        return None
    if str(d.get("stat")) != "OK":
        return None
    rows = d.get("data") or []
    if not rows:
        return None
    foreign = trust = dealer = total = 0.0
    for row in rows:
        if len(row) < 4:
            continue
        name = str(row[0])
        net = _num(row[3]) / _YI
        if "外資" in name:                 # 外資及陸資 + 外資自營商
            foreign += net
        elif "投信" in name:
            trust += net
        elif "自營" in name:               # 自行 + 避險
            dealer += net
        elif "合計" in name:
            total = net
    if total == 0.0:
        total = foreign + trust + dealer
    return {"date": date, "foreign": round(foreign, 2),
            "trust": round(trust, 2), "dealer": round(dealer, 2),
            "total": round(total, 2)}


def fetch_recent_market_insti(anchor_date: str, days: int = 10,
                              max_lookback: int = 25) -> list[dict]:
    """自 anchor_date（yyyymmdd）往回收集近 `days` 個交易日的三大法人淨額。

    升冪回傳。跳過非交易日；最多回看 `max_lookback` 個日曆日以免無限。
    """
    try:
        cur = datetime.strptime(anchor_date, "%Y%m%d")
    except (ValueError, TypeError):
        cur = datetime.now()
    out: list[dict] = []
    for _ in range(max_lookback):
        if len(out) >= days:
            break
        rec = fetch_market_insti(cur.strftime("%Y%m%d"))
        if rec:
            out.append(rec)
        cur -= timedelta(days=1)
    out.sort(key=lambda x: x["date"])
    return out
