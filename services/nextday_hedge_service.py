"""隔日沖監控 — 候選標的載入（自營避險買入為主）。

概念：自營商發行權證（認購/認售）後，需以標的股票避險（認購→買股避險）。前一交易
日「自營避險買入」較多者，隔日常有避險部位調整（若權證需求下降，自營可能賣股解避
險）。本服務把前一交易日 InstiDailyTrade 依「自營避險買入(dealer_hedge_buy)」排序，
補上名稱／三大法人買賣超／主力成本／漲跌幅／權證多空，供使用者挑選盤中即時監控標的。

純資料 + DB I/O。權證即時買賣壓由盤中另以 Shioaji 訂閱權證逐筆取得（見 viewmodel）。
"""

from __future__ import annotations

import logging

from services.turnover_monitor_service import concentration_for_rows

log = logging.getLogger(__name__)


def _numf(v) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def latest_insti_date(db) -> str | None:
    """InstiDailyTrade 最新一個交易日（yyyy-mm-dd）。無資料回 None。"""
    cur = db._cursor()
    cur.execute("SELECT MAX(trade_date) FROM InstiDailyTrade")
    r = cur.fetchone()
    return str(r[0])[:10] if r and r[0] else None


def _prev_price_date(db, date: str) -> str | None:
    """StockDailySummary 中 date 之前最近一個交易日（算漲跌幅用）。"""
    cur = db._cursor()
    cur.execute("""
        SELECT TOP 1 trade_date FROM StockDailySummary
        WHERE trade_date < %s ORDER BY trade_date DESC
    """, (date,))
    r = cur.fetchone()
    return str(r[0])[:10] if r and r[0] else None


def load_candidates(db, date: str | None = None, top_n: int = 40) -> dict:
    """前一交易日「自營避險買入」前 N 名，補監控欄位。db 需已 connect。

    回 {date, prev_date, rows:[{stock_code, name, hedge_buy_lots, hedge_net_lots,
    three_insti_lots, foreign_lots, trust_lots, dealer_self_lots, main_buy_cost,
    close, change_pct, warrant_long_share, warrant_bias}]}，依避險買入降冪。
    """
    date = date or latest_insti_date(db)
    if not date:
        return {"date": None, "prev_date": None, "rows": []}
    cur = db._cursor()
    cur.execute(f"""
        SELECT TOP {int(top_n)} stock_code, foreign_net, trust_net,
               dealer_self_net, dealer_hedge_buy, dealer_hedge_sell,
               dealer_hedge_net, three_insti_net
        FROM InstiDailyTrade
        WHERE trade_date = %s AND dealer_hedge_buy > 0
        ORDER BY dealer_hedge_buy DESC
    """, (date,))
    rows: list[dict] = []
    for r in cur.fetchall():
        rows.append({
            "stock_code": r[0],
            "foreign_net": r[1] or 0, "trust_net": r[2] or 0,
            "dealer_self_net": r[3] or 0,
            "hedge_buy": r[4] or 0, "hedge_sell": r[5] or 0,
            "hedge_net": r[6] or 0, "three_insti_net": r[7] or 0,
        })
    if not rows:
        return {"date": date, "prev_date": None, "rows": []}

    # 名稱 + 當日收盤 + 成交量（股）
    name_map: dict[str, str] = {}
    close_map: dict[str, float] = {}
    vol_map: dict[str, float] = {}
    cur.execute("""
        SELECT stock_code, stock_name, close_price, total_volume
        FROM StockDailySummary WHERE trade_date = %s
    """, (date,))
    for c, n, cl, tv in cur.fetchall():
        name_map[c] = n or ""
        close_map[c] = _numf(cl)
        vol_map[c] = _numf(tv)
    # 前一交易日收盤 → 漲跌
    prev = _prev_price_date(db, date)
    prev_close: dict[str, float] = {}
    if prev:
        cur.execute("""
            SELECT stock_code, close_price
            FROM StockDailySummary WHERE trade_date = %s
        """, (prev,))
        for c, cl in cur.fetchall():
            prev_close[c] = _numf(cl)
    # 流通股數（算周轉率）
    try:
        shares_map = db.get_latest_total_shares()
    except Exception as e:  # noqa: BLE001
        log.warning("nextday shares map failed: %s", e)
        shares_map = {}

    for r in rows:
        c = r["stock_code"]
        r["name"] = name_map.get(c, "")
        r["close"] = close_map.get(c)
        pc = prev_close.get(c)
        r["change_val"] = (round(r["close"] - pc, 2)
                           if (r["close"] and pc) else None)
        r["change_pct"] = (round((r["close"] - pc) / pc * 100, 2)
                           if (r["close"] and pc) else None)
        vol_shares = vol_map.get(c, 0.0)
        r["volume_lots"] = int(vol_shares / 1000) if vol_shares else None
        shares = shares_map.get(c, 0)
        r["turnover_pct"] = (round(vol_shares / shares * 100, 2)
                             if (vol_shares and shares) else None)
        # 股 → 張
        r["hedge_buy_lots"] = round(r["hedge_buy"] / 1000)
        r["hedge_net_lots"] = round(r["hedge_net"] / 1000)
        r["three_insti_lots"] = round(r["three_insti_net"] / 1000)
        r["foreign_lots"] = round(r["foreign_net"] / 1000)
        r["trust_lots"] = round(r["trust_net"] / 1000)
        r["dealer_self_lots"] = round(r["dealer_self_net"] / 1000)

    # 主力成本（+ 主力買賣超 / 集中度）：沿用高周轉率監控的分點彙總
    date_c = date.replace("-", "")
    try:
        concentration_for_rows(db, rows, date_c)
    except Exception as e:  # noqa: BLE001
        log.warning("nextday concentration failed %s: %s", date, e)
        for r in rows:
            r.setdefault("main_buy_cost", None)

    # 權證多空（EOD；上櫃依代碼、上市依名稱）
    try:
        from services.warrant_service import bias_by_name, bias_by_code_tpex
        wt = bias_by_name(date_c)
        wp = bias_by_code_tpex()
        for r in rows:
            w = wp.get(r["stock_code"]) or wt.get(r.get("name", ""))
            r["warrant_long_share"] = w["long_share"] if w else None
            r["warrant_bias"] = w["bias"] if w else None
    except Exception as e:  # noqa: BLE001
        log.warning("nextday warrant bias failed %s: %s", date, e)
        for r in rows:
            r.setdefault("warrant_long_share", None)
            r.setdefault("warrant_bias", None)

    return {"date": date, "prev_date": prev, "rows": rows}
