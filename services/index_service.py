"""IndexService — 加權指數（TAIEX）即時 + 當日分時，免登入公開端點。

兩個來源：
- 即時現值：TWSE MIS `getStockInfo`（tse_t00.tw），盤中每次呼叫回最新快照。
- 當日分時曲線：TWSE `MI_5MINS_INDEX`（指定日，回當日完整時間序列），
  用於開圖時 seed 整條走勢，之後由即時快照往後延伸。

純 I/O，回傳 dict / list，不碰 DB、不需 Shioaji 登入。
"""

from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)

_MIS_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
_5MIN_URL = "https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_INDEX"
_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
_TIMEOUT = 10


def _num(v) -> float | None:
    """'42,671.27' / '-' / '' → float | None。"""
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if s in ("", "-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fetch_realtime_index() -> dict | None:
    """加權指數即時快照。回傳 dict 或 None（失敗）。

    keys: name, last, prev_close, open, high, low, change, change_pct,
          time (HH:MM:SS), date (yyyymmdd)。
    last 於開盤前可能為 None（此時以 prev_close 視為現值）。
    """
    try:
        r = requests.get(
            _MIS_URL,
            params={"ex_ch": "tse_t00.tw", "json": "1", "delay": "0"},
            headers=_HEADERS, timeout=_TIMEOUT,
        )
        d = r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("fetch_realtime_index failed: %s", e)
        return None
    arr = d.get("msgArray") or []
    if not arr:
        return None
    q = arr[0]
    last = _num(q.get("z"))
    prev = _num(q.get("y"))
    if last is None:                       # 盤前無成交價 → 用開盤或昨收
        last = _num(q.get("o")) or prev
    change = (last - prev) if (last is not None and prev is not None) else None
    change_pct = (change / prev * 100) if (change is not None and prev) else None
    return {
        "name": q.get("n") or "加權指數",
        "last": last,
        "prev_close": prev,
        "open": _num(q.get("o")),
        "high": _num(q.get("h")),
        "low": _num(q.get("l")),
        "change": change,
        "change_pct": change_pct,
        "time": q.get("t") or "",
        "date": q.get("d") or "",
    }


def fetch_intraday_series(date: str) -> list[tuple[str, float]]:
    """指定日加權指數當日分時序列 [(HH:MM:SS, value), ...]（升冪）。

    date: yyyymmdd。查無資料回空 list。第 0 欄為時間、第 1 欄為加權指數。
    """
    try:
        r = requests.get(
            _5MIN_URL, params={"date": date, "response": "json"},
            headers=_HEADERS, timeout=_TIMEOUT,
        )
        d = r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("fetch_intraday_series failed: %s", e)
        return []
    if str(d.get("stat")) != "OK":
        return []
    rows = d.get("data") or d.get("data1") or []
    out: list[tuple[str, float]] = []
    for row in rows:
        if len(row) < 2:
            continue
        t = str(row[0]).strip()
        v = _num(row[1])
        if v is not None and t:
            out.append((t, v))
    out.sort(key=lambda x: x[0])
    return out
