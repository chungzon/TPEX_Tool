"""TurnoverService — 全市場高周轉率標的排行。

周轉率 = 當日成交股數 ÷ 流通股數 × 100%。
- 分子：TWSE `MI_INDEX` + TPEX `otc`（每日行情，成交股數，單位一致為「股」，
  重用 backfill_service；避免 StockDailySummary.total_volume 單位不一致問題）。
- 分母：DB 集保總股數（各級 shares 加總 ≈ 發行股數），db.get_latest_total_shares。

回排序後 top N（預設成交量 > 1000 張才納入）。日期非交易日自動往回找。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

log = logging.getLogger(__name__)


def _num(v) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def rank_turnover(daily_rows: list[dict], shares_map: dict[str, int],
                  min_lots: int = 1000, top_n: int = 20) -> list[dict]:
    """純計算：合併每日行情 + 流通股數 → 周轉率排行。

    daily_rows: 每筆含 stock_code / stock_name / total_volume(股) / close_price。
    shares_map: {code: 流通股數}。min_lots: 成交量張數下限（含）。
    """
    out: list[dict] = []
    for r in daily_rows:
        code = r.get("stock_code", "")
        vol_shares = _num(r.get("total_volume"))
        lots = vol_shares / 1000.0
        if lots < min_lots:
            continue
        shares = shares_map.get(code, 0)
        if shares <= 0:
            continue
        turnover = vol_shares / shares * 100.0
        out.append({
            "stock_code": code,
            "stock_name": r.get("stock_name", ""),
            "market": r.get("market", ""),      # 上市 / 上櫃
            "close": _num(r.get("close_price")),
            "volume_lots": int(lots),
            "turnover_pct": round(turnover, 2),
        })
    out.sort(key=lambda x: x["turnover_pct"], reverse=True)
    return out[:top_n]


def _fetch_twse(date: str) -> list[dict]:
    from services.backfill_service import fetch_twse_daily
    try:
        return fetch_twse_daily(date) or []
    except Exception as e:  # noqa: BLE001
        log.warning("fetch_twse_daily(%s) failed: %s", date, e)
        return []


def _fetch_otc(date: str) -> list[dict]:
    from services.backfill_service import fetch_otc_daily
    try:
        return fetch_otc_daily(date) or []
    except Exception as e:  # noqa: BLE001
        log.warning("fetch_otc_daily(%s) failed: %s", date, e)
        return []


def _collect_window(anchor: str | None, window: int,
                    max_lookback: int) -> list[tuple[str, dict]]:
    """往回收集最近 `window` 個交易日的每日行情。

    以 TWSE 是否有資料判定真正交易日（TPEX 非交易日會回舊資料，不可單用）。
    回傳升冪 list of (date, {code: row})；row 內含 market 標記。
    """
    try:
        cur = datetime.strptime(anchor, "%Y%m%d") if anchor else datetime.now()
    except (ValueError, TypeError):
        cur = datetime.now()
    days: list[tuple[str, dict]] = []
    for _ in range(max_lookback + 1):
        if len(days) >= window:
            break
        date_key = cur.strftime("%Y%m%d")
        date_dash = cur.strftime("%Y-%m-%d")   # 兩個 API 皆吃 yyyy-mm-dd
        cur -= timedelta(days=1)
        twse = _fetch_twse(date_dash)
        if not twse:                    # 非交易日
            continue
        for r in twse:
            r["market"] = "上市"
        otc = _fetch_otc(date_dash)
        for r in otc:
            r["market"] = "上櫃"
        by_code = {r["stock_code"]: r for r in (twse + otc)}
        days.append((date_key, by_code))
    days.sort(key=lambda x: x[0])
    return days


def _amplitude_and_change(code: str,
                          days: list[tuple[str, dict]]) -> tuple[float | None,
                                                                 float | None]:
    """回 (近5日平均振幅%, 當日漲跌額)。

    振幅[d] = (最高 − 最低) / 昨收 × 100；取最近至多 5 日平均。
    漲跌 = 最新日收盤 − 前一日收盤。資料不足回 None。
    """
    closes: list[tuple[str, float]] = []
    amps: list[float] = []
    prev_close: float | None = None
    for _date, by_code in days:          # 升冪
        r = by_code.get(code)
        if not r:
            prev_close = None
            continue
        close = _num(r.get("close_price"))
        high = _num(r.get("high_price"))
        low = _num(r.get("low_price"))
        if close > 0:
            closes.append((_date, close))
        if prev_close and prev_close > 0 and high > 0 and low > 0:
            amps.append((high - low) / prev_close * 100.0)
        prev_close = close if close > 0 else prev_close
    amp5 = round(sum(amps[-5:]) / len(amps[-5:]), 2) if amps else None
    change = None
    if len(closes) >= 2:
        change = round(closes[-1][1] - closes[-2][1], 2)
    return amp5, change


def latest_top_turnover(db, min_lots: int = 1000, top_n: int = 20,
                        anchor: str | None = None, window: int = 6,
                        max_lookback: int = 12) -> tuple[str, list[dict]]:
    """取最近交易日的高周轉率排行（含當日漲跌 + 近5日平均振幅）。

    回 (資料日 yyyymmdd, rows)。一次抓最近 `window` 個交易日行情視窗，排行
    以最新日計算，漲跌/振幅由視窗歷史算出（同一權威來源，無單位/跨日問題）。
    db 需已 connect（呼叫端負責連線/關閉）。
    """
    shares_map = db.get_latest_total_shares()
    days = _collect_window(anchor, window, max_lookback)
    if not days:
        return "", []
    date, latest = days[-1]
    ranked = rank_turnover(list(latest.values()), shares_map, min_lots, top_n)
    for row in ranked:
        amp5, change = _amplitude_and_change(row["stock_code"], days)
        row["amp5"] = amp5
        row["change"] = change
        row["change_pct"] = (round(change / (row["close"] - change) * 100, 2)
                             if change is not None and (row["close"] - change)
                             else None)
    return date, ranked
