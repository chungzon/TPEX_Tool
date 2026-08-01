"""RealtimeQuoteService — 個股即時報價（TWSE MIS getStockInfo，免登入）。

盤中每次呼叫回最新快照，可一次批多檔（上市用 tse_、上櫃用 otc_ 前綴）。
供監控彈窗的「個股即時報價」與「同類股即時成交量排行」使用——無需永豐登入。
純 I/O：回 {code: quote}。MIS 對單次 ex_ch 檔數有上限，故分塊查詢。

MIS 欄位：c 代號 / n 名稱 / z 現價 / y 昨收 / o,h,l 開高低 / v 累積成交量(張)
/ t 時間。z 於盤前/無成交可能為 '-'，此時以開盤或昨收視為現值。
"""

from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)

_URL = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
_TIMEOUT = 10
_CHUNK = 50          # 單次 ex_ch 檔數上限（保守）


def _num(v) -> float | None:
    if v is None:
        return None
    s = str(v).replace(",", "").strip()
    if s in ("", "-", "--"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _ch(code: str, market: str) -> str:
    prefix = "otc_" if market == "上櫃" else "tse_"
    return f"{prefix}{code}.tw"


def _parse(q: dict) -> dict:
    last = _num(q.get("z"))
    prev = _num(q.get("y"))
    if last is None:                        # 盤前/無成交 → 用開盤或昨收
        last = _num(q.get("o")) or prev
    change = (last - prev) if (last is not None and prev is not None) else None
    change_pct = (change / prev * 100) if (change is not None and prev) else None
    return {
        "code": str(q.get("c") or "").strip(),
        "name": str(q.get("n") or "").strip(),
        "last": last,
        "prev_close": prev,
        "open": _num(q.get("o")),
        "high": _num(q.get("h")),
        "low": _num(q.get("l")),
        "change": change,
        "change_pct": change_pct,
        "volume_lots": int(_num(q.get("v")) or 0),   # 累積成交量(張)
        "time": str(q.get("t") or "").strip(),
    }


def fetch_quotes(items: list[tuple[str, str]]) -> dict[str, dict]:
    """批次即時報價。items = [(code, market), ...]，回 {code: quote}。

    market 為 '上市'/'上櫃'（決定 tse_/otc_ 前綴）。逾 _CHUNK 檔自動分塊。
    任一塊失敗僅略過該塊、不影響其餘。
    """
    out: dict[str, dict] = {}
    for i in range(0, len(items), _CHUNK):
        chunk = items[i:i + _CHUNK]
        ex_ch = "|".join(_ch(c, m) for c, m in chunk)
        try:
            r = requests.get(
                _URL,
                params={"ex_ch": ex_ch, "json": "1", "delay": "0"},
                headers=_HEADERS, timeout=_TIMEOUT,
            )
            arr = r.json().get("msgArray") or []
        except Exception as e:  # noqa: BLE001
            log.warning("MIS fetch_quotes failed: %s", e)
            continue
        for q in arr:
            parsed = _parse(q)
            if parsed["code"]:
                out[parsed["code"]] = parsed
    return out


def fetch_quote(code: str, market: str) -> dict | None:
    """單檔即時報價（回 quote dict 或 None）。"""
    return fetch_quotes([(code, market)]).get(code)
