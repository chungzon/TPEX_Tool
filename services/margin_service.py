"""融資融券資料下載 — TPEX 上櫃 + TWSE 上市。

兩端皆有歷史日期查詢支援，可以事後補抓。
回傳 list[dict]，欄位對應 MarginDailyTrade 表（單位：張）。
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


def _i(v) -> int:
    """寬鬆把字串轉 int；逗號千分位、空字串/'--' 視為 0。"""
    s = _s(v).replace(",", "").replace(" ", "")
    if s in ("", "--", "---", "N/A"):
        return 0
    try:
        return int(s)
    except (ValueError, TypeError):
        try:
            return int(float(s))
        except (ValueError, TypeError):
            return 0


def _normalize_api_date(raw: str) -> str:
    raw = _s(raw)
    if re.match(r"^\d{8}$", raw):
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    m = re.match(r"^(\d{1,3})/(\d{1,2})/(\d{1,2})$", raw)
    if m:
        return (f"{int(m.group(1)) + 1911}-"
                f"{int(m.group(2)):02d}-{int(m.group(3)):02d}")
    return ""


# ---------------------------------------------------------------------------
# TPEX 上櫃
# ---------------------------------------------------------------------------

def fetch_otc_margin(date_str: str) -> list[dict]:
    """上櫃（TPEX）指定日期融資融券餘額。

    Args:
        date_str: 'yyyy-mm-dd'。

    Returns:
        list[dict]，每筆 = 一檔股票的融資融券資料。非交易日回 []。

    TPEX 欄位順序（marginTrading/marginBalance, 2024+ 結構）：
      0  證券代號
      1  證券名稱
      2  融資-前日餘額
      3  融資-買進
      4  融資-賣出
      5  融資-現金償還
      6  融資-今日餘額
      7  融資-限額  (略過)
      8  融券-前日餘額
      9  融券-賣出
      10 融券-買進
      11 融券-現券償還
      12 融券-今日餘額
      13 融券-限額  (略過)
      14 資券互抵
      15 註記      (略過)
    單位：張（已是張數，不需除 1000）
    """
    # TPEX 近幾年改版頻繁，依序嘗試多個已知端點
    candidates = [
        ("https://www.tpex.org.tw/www/zh-tw/marginTrading/marginBalance"
         f"?response=json&date={date_str.replace('-', '/')}"),
        ("https://www.tpex.org.tw/www/zh-tw/margin/balance"
         f"?response=json&date={date_str.replace('-', '/')}"),
        ("https://www.tpex.org.tw/web/stock/margin_trading/margin_balance/"
         f"margin_bal_result.php?l=zh-tw&d={date_str.replace('-', '/')}"
         "&s=0,asc&o=json"),
    ]
    data = None
    last_err = None
    for url in candidates:
        try:
            data = _http_json(url)
            if data.get("stat") == "ok" or data.get("stat") == "OK":
                break
            last_err = f"TPEX 回傳：{data.get('stat', '未知')}"
        except Exception as e:
            last_err = str(e)
            continue
    if data is None:
        raise RuntimeError(f"TPEX 連線失敗：{last_err}")
    if data.get("stat") not in ("ok", "OK"):
        raise RuntimeError(last_err or f"TPEX 回傳：{data.get('stat', '未知')}")

    api_date = _normalize_api_date(data.get("date", "")) or date_str

    rows: list[list] = []
    for t in data.get("tables", []):
        if t.get("data"):
            rows = t["data"]
            break
    if not rows:
        return []

    out: list[dict] = []
    for r in rows:
        if not r:
            continue
        code = _s(r[0])
        if not re.match(r"^\d{4}$", code):
            continue
        out.append({
            "stock_code":     code,
            "trade_date":     api_date,
            "margin_buy":     _i(r[3])  if len(r) > 3  else 0,
            "margin_sell":    _i(r[4])  if len(r) > 4  else 0,
            "margin_cash":    _i(r[5])  if len(r) > 5  else 0,
            "margin_balance": _i(r[6])  if len(r) > 6  else 0,
            "short_sell":     _i(r[9])  if len(r) > 9  else 0,
            "short_cover":    _i(r[10]) if len(r) > 10 else 0,
            "short_cash":     _i(r[11]) if len(r) > 11 else 0,
            "short_balance":  _i(r[12]) if len(r) > 12 else 0,
            "offset_volume":  _i(r[14]) if len(r) > 14 else 0,
        })
    log.info("TPEX margin %s: %d stocks", api_date, len(out))
    return out


# ---------------------------------------------------------------------------
# TWSE 上市
# ---------------------------------------------------------------------------

def fetch_twse_margin(date_str: str) -> list[dict]:
    """上市（TWSE）指定日期融資融券餘額。

    Args:
        date_str: 'yyyy-mm-dd'。

    Returns:
        list[dict]。非交易日回 []。

    TWSE MI_MARGN 欄位順序（selectType=ALL）：
      0  證券代號
      1  證券名稱
      2  融資-買進
      3  融資-賣出
      4  融資-現金償還
      5  融資-前日餘額
      6  融資-今日餘額
      7  融資-限額    (略過)
      8  融券-買進    (=回補)
      9  融券-賣出
      10 融券-現券償還
      11 融券-前日餘額
      12 融券-今日餘額
      13 融券-限額    (略過)
      14 資券互抵
      15 註記         (略過)
    """
    d = date_str.replace("-", "")
    url = ("https://www.twse.com.tw/exchangeReport/MI_MARGN"
           f"?response=json&date={d}&selectType=ALL")
    try:
        data = _http_json(url)
    except Exception as e:
        raise RuntimeError(f"TWSE 連線失敗：{e}")
    if data.get("stat") != "OK":
        raise RuntimeError(f"TWSE 回傳：{data.get('stat', '未知')}")

    api_date = _normalize_api_date(data.get("date", "")) or date_str

    # 找含「代號」欄位的個股表（TWSE 曾把標題「證券代號」改為「代號」，故放寬比對）
    stock_table = None
    for t in data.get("tables", []):
        fields = t.get("fields") or []
        if any("代號" in str(f) for f in fields):
            stock_table = t
            break
    # 舊格式 fallback
    if not stock_table and data.get("data"):
        stock_table = {"data": data["data"]}
    if not stock_table or not stock_table.get("data"):
        return []

    out: list[dict] = []
    for r in stock_table["data"]:
        if not r:
            continue
        code = _s(r[0])
        if not re.match(r"^\d{4}$", code):
            continue
        out.append({
            "stock_code":     code,
            "trade_date":     api_date,
            "margin_buy":     _i(r[2])  if len(r) > 2  else 0,
            "margin_sell":    _i(r[3])  if len(r) > 3  else 0,
            "margin_cash":    _i(r[4])  if len(r) > 4  else 0,
            # r[5] 前日餘額 / r[6] 今日餘額
            "margin_balance": _i(r[6])  if len(r) > 6  else 0,
            # 注意 TWSE 把「買進」「賣出」順序與 TPEX 相反
            "short_cover":    _i(r[8])  if len(r) > 8  else 0,
            "short_sell":     _i(r[9])  if len(r) > 9  else 0,
            "short_cash":     _i(r[10]) if len(r) > 10 else 0,
            "short_balance":  _i(r[12]) if len(r) > 12 else 0,
            "offset_volume":  _i(r[14]) if len(r) > 14 else 0,
        })
    log.info("TWSE margin %s: %d stocks", api_date, len(out))
    return out
