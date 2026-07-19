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


def latest_top_turnover(db, min_lots: int = 1000, top_n: int = 20,
                        anchor: str | None = None,
                        max_lookback: int = 7) -> tuple[str, list[dict]]:
    """取最近交易日的高周轉率排行。回 (資料日 yyyymmdd, rows)。

    以 TWSE 每日行情是否有資料判定「真正的交易日」（TWSE 非交易日正確回空；
    TPEX 端點在非交易日可能回舊資料，故不可單用），再用同一交易日抓 TPEX，
    避免跨日混用。anchor 預設今天，往回找最多 max_lookback 天。
    db 需已 connect（呼叫端負責連線/關閉）。
    """
    try:
        cur = datetime.strptime(anchor, "%Y%m%d") if anchor else datetime.now()
    except (ValueError, TypeError):
        cur = datetime.now()
    shares_map = db.get_latest_total_shares()
    for _ in range(max_lookback + 1):
        date = cur.strftime("%Y%m%d")
        twse = _fetch_twse(date)
        if twse:                        # 有 TWSE 資料 = 確定為交易日
            for r in twse:
                r["market"] = "上市"
            otc = _fetch_otc(date)
            for r in otc:
                r["market"] = "上櫃"
            return date, rank_turnover(twse + otc, shares_map, min_lots, top_n)
        cur -= timedelta(days=1)
    return "", []
