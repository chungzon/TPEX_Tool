"""補資料服務 — 下載指定日期的上市/上櫃「每日行情」。

用途：排程漏跑某天時，事後補抓該日的每日行情（開高低收、量、值）。
三大法人的補抓沿用 services.insti_service / services.twse_api_service
既有的日期參數函式，本檔不重複實作。

注意：分點明細（券商買賣超）無法事後補抓 —— TWSE BSR 與 TPEX 分點頁面
僅提供「最近一個交易日」，沒有歷史日期查詢。
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request

log = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
       "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")


def _http_json(url: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _s(v) -> str:
    return str(v).strip() if v is not None else ""


def _normalize_api_date(raw: str) -> str:
    """API 回傳日期 → 'yyyy-mm-dd'。支援 '20260508' 或 ROC '115/05/08'。"""
    raw = _s(raw)
    if re.match(r"^\d{8}$", raw):
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    m = re.match(r"^(\d{1,3})/(\d{1,2})/(\d{1,2})$", raw)
    if m:
        return (f"{int(m.group(1)) + 1911}-"
                f"{int(m.group(2)):02d}-{int(m.group(3)):02d}")
    return ""


def fetch_otc_daily(date_str: str) -> list[dict]:
    """上櫃（TPEX）指定日期每日行情。

    Args:
        date_str: 'yyyy-mm-dd'。

    Returns:
        list[dict]，欄位對應 StockDailySummary。非交易日回傳空 list。
        TPEX EW 端點支援歷史日期。
    """
    # TPEX EW 端點使用西元 yyyy/mm/dd
    url = ("https://www.tpex.org.tw/www/zh-tw/afterTrading/otc"
           f"?response=json&type=EW&date={date_str.replace('-', '/')}")
    data = _http_json(url)
    if data.get("stat") != "ok":
        raise RuntimeError(f"TPEX 回傳：{data.get('stat', '未知')}")

    tables = data.get("tables", [])
    if not tables or not tables[0].get("data"):
        return []

    api_date = _normalize_api_date(data.get("date", "")) or date_str
    # 欄位: 0代號 1名稱 2收盤 3漲跌 4開盤 5最高 6最低
    #       7成交股數 8成交金額 9成交筆數
    out: list[dict] = []
    for r in tables[0]["data"]:
        code = _s(r[0]) if r else ""
        if not re.match(r"^\d{4}$", code):
            continue
        out.append({
            "stock_code": code,
            "stock_name": _s(r[1]) if len(r) > 1 else "",
            "trade_date": api_date,
            "close_price": _s(r[2]) if len(r) > 2 else "",
            "open_price": _s(r[4]) if len(r) > 4 else "",
            "high_price": _s(r[5]) if len(r) > 5 else "",
            "low_price": _s(r[6]) if len(r) > 6 else "",
            "total_volume": _s(r[7]) if len(r) > 7 else "",
            "total_amount": _s(r[8]) if len(r) > 8 else "",
            "total_trades": _s(r[9]) if len(r) > 9 else "",
        })
    log.info("TPEX daily %s: %d stocks", api_date, len(out))
    return out


def fetch_twse_daily(date_str: str) -> list[dict]:
    """上市（TWSE）指定日期每日行情。

    使用 MI_INDEX 端點（支援歷史日期）；STOCK_DAY_ALL 會忽略 date 參數
    永遠回傳最新日，故不能用於補資料。

    Args:
        date_str: 'yyyy-mm-dd'。

    Returns:
        list[dict]，欄位對應 StockDailySummary。非交易日回傳空 list。
    """
    d = date_str.replace("-", "")
    url = ("https://www.twse.com.tw/exchangeReport/MI_INDEX"
           f"?response=json&date={d}&type=ALLBUT0999")
    data = _http_json(url)
    if data.get("stat") != "OK":
        raise RuntimeError(f"TWSE 回傳：{data.get('stat', '未知')}")

    api_date = _normalize_api_date(data.get("date", "")) or date_str

    # MI_INDEX 回傳多張表，找出含「證券代號」的個股表
    stock_table = None
    for t in data.get("tables", []):
        fields = t.get("fields") or []
        if any("證券代號" in str(f) for f in fields):
            stock_table = t
            break
    if not stock_table or not stock_table.get("data"):
        return []

    # 欄位: 0代號 1名稱 2成交股數 3成交筆數 4成交金額
    #       5開盤 6最高 7最低 8收盤 ...
    out: list[dict] = []
    for r in stock_table["data"]:
        code = _s(r[0]) if r else ""
        if not re.match(r"^\d{4}$", code):
            continue
        out.append({
            "stock_code": code,
            "stock_name": _s(r[1]) if len(r) > 1 else "",
            "trade_date": api_date,
            "total_volume": _s(r[2]) if len(r) > 2 else "",
            "total_trades": _s(r[3]) if len(r) > 3 else "",
            "total_amount": _s(r[4]) if len(r) > 4 else "",
            "open_price": _s(r[5]) if len(r) > 5 else "",
            "high_price": _s(r[6]) if len(r) > 6 else "",
            "low_price": _s(r[7]) if len(r) > 7 else "",
            "close_price": _s(r[8]) if len(r) > 8 else "",
        })
    log.info("TWSE daily %s: %d stocks", api_date, len(out))
    return out
