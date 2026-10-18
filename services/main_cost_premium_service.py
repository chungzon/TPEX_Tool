"""MainCostPremiumService — 主力追價：近5日主力買入均價高於市場均價的標的。

宇集：最新交易日成交量 > min_lots 張的全市場個股。
- 5日均價：近5交易日成交金額 ÷ 成交股數（VWAP），取自每日行情 API 視窗
  （同 turnover_service，避開 StockDailySummary.total_volume 單位不一致問題）。
- 5日主力買均價：近5交易日分點彙總（同名合併）後，淨買超前15家分點
  以各家買進量加權的買進均價（與高周轉率監控「主力均價」同一定義，僅區間改5日）。
溢價% = (主力買均價 − 5日均價) ÷ 5日均價 × 100；> 0 才納入，依溢價降冪取前 N，
再以 turnover_monitor_service.decorate_rows 補齊與高周轉率監控相同的欄位。
"""

from __future__ import annotations

import logging
from collections import defaultdict

from services.turnover_monitor_service import (
    _CONC_TOPN, _numf, _to_dash, base_row, decorate_rows)

log = logging.getLogger(__name__)

WINDOW = 5            # 近 N 個交易日
_SQL_CHUNK = 500      # IN 參數分批（MSSQL 單句上限 2100 個參數）


def window_vwap(code: str, days: list[tuple[str, dict]]) -> float | None:
    """行情視窗最後 WINDOW 日的成交量加權均價。缺金額/量回 None。"""
    amt = vol = 0.0
    for _d, by_code in days[-WINDOW:]:
        r = by_code.get(code)
        if not r:
            continue
        a = _numf(r.get("total_amount"))
        v = _numf(r.get("total_volume"))
        if a > 0 and v > 0:
            amt += a
            vol += v
    return round(amt / vol, 2) if vol > 0 else None


def _broker_window(db, codes: list[str], start: str, end: str
                   ) -> dict[str, dict[str, list]]:
    """批次查多檔個股在區間內各分點彙總。

    回 {stock_code: {broker_name: [net, 有均價的買進量, 買進金額]}}，單位股/元。
    以 broker_name 分組＝同名分點合併（與 concentration_for_rows 一致）。
    """
    out: dict[str, dict[str, list]] = defaultdict(dict)
    cur = db._cursor()
    for i in range(0, len(codes), _SQL_CHUNK):
        chunk = codes[i:i + _SQL_CHUNK]
        ph = ",".join(["%s"] * len(chunk))
        cur.execute(f"""
            SELECT stock_code, broker_name,
                   SUM(net_volume), SUM(buy_volume),
                   SUM(CASE WHEN avg_buy_price IS NOT NULL
                            THEN avg_buy_price * buy_volume ELSE 0 END),
                   SUM(CASE WHEN avg_buy_price IS NOT NULL
                            THEN buy_volume ELSE 0 END)
            FROM BrokerDailyStats
            WHERE stock_code IN ({ph})
              AND trade_date >= %s AND trade_date <= %s
            GROUP BY stock_code, broker_name
        """, (*chunk, start, end))
        for code, name, net, bv, bamt, bv_priced in cur.fetchall():
            out[code][name] = [int(net or 0), int(bv_priced or 0),
                               float(bamt or 0)]
    return out


def _broker_day_counts(db, codes: list[str], start: str, end: str
                       ) -> dict[str, int]:
    """各股在區間內實際有分點資料的交易日數（爬蟲漏抓的日子會少）。"""
    out: dict[str, int] = {}
    cur = db._cursor()
    for i in range(0, len(codes), _SQL_CHUNK):
        chunk = codes[i:i + _SQL_CHUNK]
        ph = ",".join(["%s"] * len(chunk))
        cur.execute(f"""
            SELECT stock_code, COUNT(DISTINCT trade_date)
            FROM BrokerDailyStats
            WHERE stock_code IN ({ph})
              AND trade_date >= %s AND trade_date <= %s
            GROUP BY stock_code
        """, (*chunk, start, end))
        for code, n in cur.fetchall():
            out[code] = int(n or 0)
    return out


def _main_buy(brokers: dict[str, list]) -> tuple[float | None, int]:
    """淨買超前15家：(買進量加權買均價, 合計淨買超股數)。"""
    top = sorted(((n, v) for n, v in brokers.items() if v[0] > 0),
                 key=lambda x: x[1][0], reverse=True)[:_CONC_TOPN]
    qty = sum(v[1] for _n, v in top)
    amt = sum(v[2] for _n, v in top)
    net = sum(v[0] for _n, v in top)
    return (round(amt / qty, 2) if qty > 0 else None), net


def premium_for_rows(db, rows: list[dict], days: list) -> None:
    """就地為每列補 5日欄位：main_cost5 / vwap5 / premium_pct /
    main_net5_lots / broker_days。db 需已 connect。"""
    if not rows or not days:
        return
    win = days[-WINDOW:]
    start, end = _to_dash(win[0][0]), _to_dash(win[-1][0])
    codes = [r["stock_code"] for r in rows]
    try:
        bw = _broker_window(db, codes, start, end)
        cnt = _broker_day_counts(db, codes, start, end)
    except Exception as e:  # noqa: BLE001
        log.warning("5d broker window failed: %s", e)
        bw, cnt = {}, {}
    for r in rows:
        code = r["stock_code"]
        cost, net = _main_buy(bw.get(code, {}))
        vwap = window_vwap(code, days)
        r["main_cost5"] = cost
        r["vwap5"] = vwap
        r["main_net5_lots"] = round(net / 1000) if cost is not None else None
        r["broker_days"] = cnt.get(code, 0)
        r["premium_pct"] = (round((cost - vwap) / vwap * 100, 2)
                            if cost is not None and vwap else None)


def latest_premium(db, min_lots: int = 1000, top_n: int = 30,
                   anchor: str | None = None, return_ctx: bool = False):
    """主力追價排行。回 (資料日 yyyymmdd, rows[, ctx])；ctx 同 latest_monitor。

    db 需已 connect。rows 依溢價% 降冪，只含溢價 > 0 者。
    """
    from services.industry_service import get_industry_map
    from services.turnover_service import _collect_window

    days = _collect_window(anchor, WINDOW + 1, 14)   # 多1日供漲跌/量增減
    industry_map = get_industry_map()
    ctx = {"days": days, "industry_map": industry_map}
    if not days:
        return ("", [], ctx) if return_ctx else ("", [])
    date, latest = days[-1]
    shares_map = db.get_latest_total_shares()
    cands = [h for h in latest.values()
             if _numf(h.get("total_volume")) / 1000 > min_lots]
    rows = [base_row(h, days, shares_map) for h in cands]
    premium_for_rows(db, rows, days)
    rows = [r for r in rows if (r.get("premium_pct") or 0) > 0]
    rows.sort(key=lambda r: r["premium_pct"], reverse=True)
    rows = rows[:top_n]
    if rows:
        decorate_rows(db, rows, date, days, industry_map)
    ctx["universe"] = len(cands)
    return (date, rows, ctx) if return_ctx else (date, rows)
