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


def _to_dash(yyyymmdd: str) -> str:
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"


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


def latest_monitor(db, min_lots: int = 1000, top_n: int = 30,
                   anchor: str | None = None) -> tuple[str, list[dict]]:
    """周轉率排行 + 監控欄位（含同類股漲跌）。回 (資料日 yyyymmdd, rows)。
    db 需已 connect。"""
    from services.turnover_service import latest_top_turnover, market_change_map
    from services.industry_service import get_industry_map

    date, rows, days = latest_top_turnover(db, min_lots=min_lots, top_n=top_n,
                                           anchor=anchor, return_days=True)
    if not rows:
        return date, rows
    enrich_rows(db, rows, date)

    # 同類股漲跌（同市場 + 同產業別）
    industry_map = get_industry_map()
    change_map = market_change_map(days)
    stats = _peer_stats(industry_map, change_map)
    # 權證多空：上市依標的名稱、上櫃依標的代碼對應
    from services.warrant_service import bias_by_name, bias_by_code_tpex
    warrant_twse = bias_by_name(date)
    warrant_tpex = bias_by_code_tpex()
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
    return date, rows
