"""TurnoverMonitorService — 高周轉率監控：沿用周轉率排行，逐檔加籌碼/技術欄位。

在 turnover_service 的排行基礎上，對每檔補：
- 主力型態：近期分點淨買中，隔日沖分點(隔) vs 波段分點(波) 誰主導 → 波段 / 隔日沖 / 混合。
- 均線斜率：MA20 斜率 %（越線動能），重用 strategy_eval._price_metrics.slope。
- 布林位階：K 棒在布林通道相對位置（−10~+10，突破可超出），._price_metrics.rank_pos。

價格歷史取自 DB StockDailySummary（OHLC 可靠）；分點取自 BrokerDailyStats。
無資料（未下載/未爬）之欄位以 None 表示，UI 顯示「—」。
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta

from services import broker_tags as bt
from services.strategy_eval_service import _price_metrics

log = logging.getLogger(__name__)

MF_SWING = "波段"
MF_FLIP = "隔日沖"
MF_MIXED = "混合"

_MA_PERIOD = 20        # 均線扣抵基準（月線）


def _to_dash(yyyymmdd: str) -> str:
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"


def _numf(v) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def classify_main_force(broker_rows: list[dict]) -> tuple[str | None, float | None]:
    """依區間分點淨買，判定主力為波段 or 隔日沖。

    只看有明確標記的分點：隔日沖(TAG_NEXT) 對 波段(TAG_SWING)，取淨買加權比重。
    回 (型態, 隔日沖佔比)。皆無標記淨買 → (None, None)。
    """
    net_by: dict[str, int] = defaultdict(int)
    for r in broker_rows:
        name = r.get("broker_name", "")
        net = r.get("net_volume")
        if net is None:
            net = (r.get("buy_volume") or 0) - (r.get("sell_volume") or 0)
        net_by[name] += net
    flip = swing = 0
    for name, net in net_by.items():
        if net <= 0:
            continue
        tags = bt.get_broker_tags(name)
        if bt.TAG_NEXT in tags:
            flip += net
        elif bt.TAG_SWING in tags:
            swing += net
    total = flip + swing
    if total <= 0:
        return None, None
    ratio = flip / total
    if ratio >= 0.6:
        return MF_FLIP, ratio
    if ratio <= 0.4:
        return MF_SWING, ratio
    return MF_MIXED, ratio


def enrich_rows(db, rows: list[dict], rank_date: str,
                price_days: int = 90, broker_days: int = 15) -> list[dict]:
    """對周轉率排行每列補主力型態 / 均線斜率 / 布林位階（就地並回傳）。

    db 需已 connect。rank_date 為 yyyymmdd。
    """
    if not rows:
        return rows
    try:
        end_dt = datetime.strptime(rank_date, "%Y%m%d")
    except (ValueError, TypeError):
        end_dt = datetime.now()
    end = end_dt.strftime("%Y-%m-%d")
    price_start = (end_dt - timedelta(days=price_days)).strftime("%Y-%m-%d")
    broker_start = (end_dt - timedelta(days=broker_days)).strftime("%Y-%m-%d")

    for r in rows:
        code = r["stock_code"]
        # 技術面
        r["ma_slope"] = None
        r["bb_pos"] = None
        r["ma_deduct"] = None       # 均線扣抵值（明日將被扣抵的收盤價）
        r["deduct_dir"] = None      # 1 助漲 / -1 助跌 / 0 持平（現價 vs 扣抵值）
        try:
            prices = db.get_stock_prices(code, price_start, end)
            # _price_metrics 遇任一 close 為 null 會整個回 None，先濾掉空值列
            prices = [p for p in prices
                      if p.get("close_price") not in (None, "")
                      and p.get("high_price") not in (None, "")
                      and p.get("low_price") not in (None, "")]
            m = _price_metrics(prices) if prices else None
            if m:
                r["ma_slope"] = round(m["slope"], 2)
                r["bb_pos"] = m["rank_pos"]
            # 均線扣抵：現價 vs 20 日視窗最舊（明日將被扣抵）那筆收盤，孰高孰低
            closes = [_numf(p.get("close_price")) for p in prices]
            if len(closes) >= _MA_PERIOD:
                deduct = closes[-_MA_PERIOD]
                last = closes[-1]
                r["ma_deduct"] = round(deduct, 2)
                r["deduct_dir"] = (1 if last > deduct
                                   else -1 if last < deduct else 0)
        except Exception as e:  # noqa: BLE001
            log.warning("price metrics failed %s: %s", code, e)
        # 主力型態
        r["main_force"] = None
        r["mf_ratio"] = None
        try:
            brokers = db.get_all_brokers_daily(code, broker_start, end)
            mf, ratio = classify_main_force(brokers)
            r["main_force"] = mf
            r["mf_ratio"] = ratio
        except Exception as e:  # noqa: BLE001
            log.warning("main force failed %s: %s", code, e)
    return rows


def _peer_stats(industry_map: dict[str, str],
                change_map: dict[str, dict]) -> dict[str, tuple]:
    """依 (市場, 產業別) 分組，算每組上漲/下跌家數與平均漲跌幅%。

    回 {group_key: (up, down, total, 平均漲跌幅%)}。group_key = f"{market}:{ind}"。
    兩市場產業代碼體系不同，故以市場別區隔，避免混組。
    """
    groups: dict[str, list[float]] = defaultdict(list)
    for code, info in change_map.items():
        ind = industry_map.get(code)
        if not ind:
            continue
        key = f"{info['market']}:{ind}"
        groups[key].append(info["change_pct"])
    out: dict[str, tuple] = {}
    for key, chgs in groups.items():
        if not chgs:
            continue
        up = sum(1 for c in chgs if c > 0)
        down = sum(1 for c in chgs if c < 0)
        out[key] = (up, down, len(chgs), sum(chgs) / len(chgs))
    return out


def peer_leaders(days: list[tuple[str, dict]], industry_map: dict[str, str],
                 code: str, market: str, top_n: int = 5) -> list[dict]:
    """同市場同產業「成交量前 N」同類股（含最新價、當日漲跌幅%）。

    days 為 turnover_service 收集的行情視窗（升冪 (date, {code: row})）；
    重用 market_change_map 算漲跌幅。回 [{code, name, close, volume_lots,
    change_pct}]，依成交量降冪，排除自己。無產業或無視窗回 []。
    """
    from services.turnover_service import market_change_map
    if not days:
        return []
    target_ind = industry_map.get(code)
    if not target_ind:
        return []
    _d, latest = days[-1]
    change_map = market_change_map(days)
    peers: list[dict] = []
    for c, r in latest.items():
        if c == code or r.get("market") != market:
            continue
        if industry_map.get(c) != target_ind:
            continue
        vol = _numf(r.get("total_volume"))
        cm = change_map.get(c)
        peers.append({
            "code": c,
            "name": r.get("stock_name", ""),
            "close": _numf(r.get("close_price")),
            "volume_lots": int(vol / 1000),
            "change_pct": round(cm["change_pct"], 2) if cm else None,
        })
    peers.sort(key=lambda x: x["volume_lots"], reverse=True)
    return peers[:top_n]


def industry_members(days: list[tuple[str, dict]], industry_map: dict[str, str],
                     code: str, market: str) -> list[str]:
    """同市場同產業的個股代碼清單（排除自己），供即時報價批次查詢。

    「屬於哪個產業」相對穩定，用 EOD 行情視窗最新日圈出成員宇集；其價量再由
    即時端點抓取。無產業或無視窗回 []。
    """
    if not days:
        return []
    target_ind = industry_map.get(code)
    if not target_ind:
        return []
    _d, latest = days[-1]
    return [c for c, r in latest.items()
            if c != code and r.get("market") == market
            and industry_map.get(c) == target_ind]


def daily_trend(prices: list[dict], ma_period: int = 20,
                bb_k: float = 2.0) -> dict:
    """由 DB 日線算 月線(MA20) + 布林通道 + 量，供監控趨勢圖。

    prices: db.get_stock_prices 回傳（升冪，含 close/high/low/open/total_volume）。
    回 {dates, open, high, low, close, ma, bb_up, bb_dn, vol_lots, up}；
    open/high/low 供箱型圖（K 線）繪製；ma/bb 前段不足以 None 補齊，
    up[i] 為當根較前一日收漲(True)/跌(False)供量棒上色。資料不足回空欄位。
    """
    rows = [p for p in prices
            if _numf(p.get("close_price")) > 0]
    n = len(rows)
    empty = {"dates": [], "open": [], "high": [], "low": [], "close": [],
             "ma": [], "bb_up": [], "bb_dn": [], "vol_lots": [], "up": []}
    if n == 0:
        return empty
    dates = [str(p.get("trade_date", ""))[:10] for p in rows]
    close = [_numf(p.get("close_price")) for p in rows]
    opn = [_numf(p.get("open_price")) for p in rows]
    high = [_numf(p.get("high_price")) for p in rows]
    low = [_numf(p.get("low_price")) for p in rows]
    vol_lots = [int(_numf(p.get("total_volume")) / 1000) for p in rows]
    up = [close[i] >= close[i - 1] if i > 0 else True for i in range(n)]
    ma: list[float | None] = [None] * n
    bb_up: list[float | None] = [None] * n
    bb_dn: list[float | None] = [None] * n
    for i in range(n):
        if i + 1 < ma_period:
            continue
        window = close[i + 1 - ma_period:i + 1]
        m = sum(window) / ma_period
        var = sum((x - m) ** 2 for x in window) / ma_period
        sd = var ** 0.5
        ma[i] = round(m, 2)
        bb_up[i] = round(m + bb_k * sd, 2)
        bb_dn[i] = round(m - bb_k * sd, 2)
    return {"dates": dates, "open": opn, "high": high, "low": low,
            "close": close, "ma": ma, "bb_up": bb_up, "bb_dn": bb_dn,
            "vol_lots": vol_lots, "up": up}


def warrant_for(date: str, code: str, name: str, market: str) -> dict | None:
    """單檔權證多空（最新可得日）。上櫃依代碼、上市依名稱對應。

    回 {bias, long_share, long, short} 或 None。純轉呼叫 warrant_service（同日快取）。
    """
    from services.warrant_service import bias_by_name, bias_by_code_tpex
    if market == "上櫃":
        return bias_by_code_tpex().get(code)
    return bias_by_name(date).get(name)


def latest_monitor(db, min_lots: int = 1000, top_n: int = 30,
                   anchor: str | None = None, return_ctx: bool = False):
    """周轉率排行 + 監控欄位（含同類股漲跌）。回 (資料日 yyyymmdd, rows)。

    return_ctx=True 時回 (date, rows, ctx)，ctx = {days, industry_map} 供監控
    彈窗算同類股前五等統計，避免重抓行情視窗。db 需已 connect。"""
    from services.turnover_service import latest_top_turnover, market_change_map
    from services.industry_service import get_industry_map

    date, rows, days = latest_top_turnover(db, min_lots=min_lots, top_n=top_n,
                                           anchor=anchor, return_days=True)
    industry_map = get_industry_map()
    ctx = {"days": days, "industry_map": industry_map}
    if not rows:
        return (date, rows, ctx) if return_ctx else (date, rows)
    enrich_rows(db, rows, date)

    # 同類股漲跌（同市場 + 同產業別）
    change_map = market_change_map(days)
    stats = _peer_stats(industry_map, change_map)
    # 權證多空：上市依標的名稱、上櫃依標的代碼對應
    from services.warrant_service import bias_by_name, bias_by_code_tpex
    warrant_twse = bias_by_name(date)
    warrant_tpex = bias_by_code_tpex()
    # 自營商自行 / 避險 買賣超淨額（單日全市場一次查，轉張；可正買超可負賣超）
    try:
        dealer_map = db.get_insti_dealer_by_date(_to_dash(date))
    except Exception as e:  # noqa: BLE001
        log.warning("dealer net load failed %s: %s", date, e)
        dealer_map = {}
    for r in rows:
        ind = industry_map.get(r["stock_code"])
        key = f"{r.get('market', '')}:{ind}" if ind else None
        s = stats.get(key) if key else None
        if s:
            r["peer_up"], r["peer_down"], r["peer_total"] = s[0], s[1], s[2]
            r["peer_avg_chg"] = round(s[3], 2)
        else:
            r["peer_up"] = r["peer_down"] = r["peer_total"] = None
            r["peer_avg_chg"] = None
        w = (warrant_tpex.get(r["stock_code"]) if r.get("market") == "上櫃"
             else warrant_twse.get(r["stock_name"]))
        r["warrant_bias"] = w["bias"] if w else None
        r["warrant_long_share"] = w["long_share"] if w else None
        d = dealer_map.get(r["stock_code"])
        if d:
            r["foreign_lots"] = round(d["foreign_net"] / 1000)
            r["dealer_self_lots"] = round(d["self_net"] / 1000)
            r["dealer_hedge_lots"] = round(d["hedge_net"] / 1000)
        else:
            r["foreign_lots"] = None
            r["dealer_self_lots"] = None
            r["dealer_hedge_lots"] = None
    return (date, rows, ctx) if return_ctx else (date, rows)
