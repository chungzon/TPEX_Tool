"""BrokerProfileService — 逐檔判定「主力分點」的交易風格並落地。

針對每個 (個股, 分點) 配對，從 BrokerDailyStats 的日級買賣量還原持有天期，
再歸類為 隔日沖 / 短沖 / 波段 / 混合，寫入 BrokerStockProfile 供後續應用
（選股、監控頁標記、回測分群）。

與 `broker_tags` 的差異：那份是全市場通用的人工清單（同一家分點在所有股票都
同一個標籤）；這裡是「這家分點在這檔股票上」的實際行為，資料驅動、逐檔不同。
同一家分點完全可能在 A 股做波段、在 B 股隔日沖。

### 為什麼用「淨額」而不是「買賣毛額」

分點是**分公司**，不是單一交易者。大型分公司幾乎每天都同時有客戶買與賣，
若把同日 min(買, 賣) 當成「當沖」，實測會讓 50 家主力分點中有 40 家被判成
當沖（day_ratio 普遍 0.6~0.9）—— 那衡量的是分公司規模，不是交易風格。
故改以**每日淨額**還原部位：淨買進佇列、淨賣出沖銷，持有天期才有意義。
同日對敲的程度另存 `churn_ratio` 當輔助指標，不作為風格標籤。

### 演算法（單一配對）

輸入為該分點在該股的日序淨額 net_d = 買 − 賣（股）。

1. net > 0：視為加碼，推入 FIFO 佇列 [日序, 量]。
2. net < 0：依序沖銷最早的未平倉部位，記錄持有天期
   （以「該股在資料庫中實際有分點資料的交易日」計）。
3. 天期分桶：<=1 日 → 隔日沖；2~5 日 → 短沖；>5 日 → 波段。
4. 區間結束仍未沖銷的部位計入波段（還抱著就是波段）。
5. 沖銷不到買量的賣出（部位建立於區間之前）不計入分類 —— 持有天期無從得知，
   硬算會污染結果，改記於 `orphan_sell_lots`。

各桶量佔「可分類總量」的比重即 flip/short/swing_ratio，取最大者為標籤；
最大比重 < `_MIXED_TH` 時標為「混合」。比重一併落地，日後要改判定門檻不必重算。

### 重要限制

- 持有天期以「資料庫裡有該股分點資料的交易日」為單位，不是日曆交易日。排程漏抓
  的日子會被跳過，使天期被低估。`active_days` 可用來判斷樣本是否足夠。
- 分點身分鍵為 (broker_code, broker_name)：DB 中約 32% 的列 broker_code 為空字串
  （上市 BSR 來源不提供代碼），只用 code 分組會把數百家分點混成同一桶。
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque

log = logging.getLogger(__name__)

# 風格標籤（與 broker_tags.TAG_LABELS 用語一致，方便兩者對照）
STYLE_FLIP = "隔日沖"
STYLE_SHORT = "短沖"
STYLE_SWING = "波段"
STYLE_MIXED = "混合"

STYLE_ALL = (STYLE_FLIP, STYLE_SHORT, STYLE_SWING, STYLE_MIXED)

# 持有天期分桶上界（交易日）
_FLIP_MAX = 1        # <=1 日 → 隔日沖
_SHORT_MAX = 5       # 2~5 日 → 短沖；之後為波段
_MIXED_TH = 0.45     # 最大比重低於此 → 混合

# 納入計算的門檻（過濾零星進出，否則上百萬組配對多數是雜訊）
MIN_ACTIVE_DAYS = 10         # 該分點在該股至少有幾個交易日進出
MIN_TOTAL_LOTS = 1000        # 區間買量+賣量至少幾張
TOP_BROKERS_PER_STOCK = 50   # 每檔股票取進出量前幾大的分點（＝「主力分點」）


def classify_series(rows: list[dict]) -> dict | None:
    """單一 (股, 分點) 日序 → 風格分類結果。rows 需依 trade_date 升冪。

    rows 每筆需含 buy_volume / sell_volume（股）。
    回 dict（欄位見模組說明）；無可分類量回 None。
    """
    if not rows:
        return None
    bucket = {STYLE_FLIP: 0, STYLE_SHORT: 0, STYLE_SWING: 0}
    hold_sum = 0           # 天期 × 量，算加權平均持有天數
    hold_vol = 0
    orphan_sell = 0        # 沖銷不到買量的賣出（區間前既有部位）
    buy_total = sell_total = 0
    churn = 0              # Σ min(買, 賣)：同日對敲程度
    active_days = 0
    queue: deque[list[int]] = deque()      # [日序index, 剩餘淨買量]

    for i, r in enumerate(rows):
        bv = int(r.get("buy_volume") or 0)
        sv = int(r.get("sell_volume") or 0)
        if bv <= 0 and sv <= 0:
            continue
        active_days += 1
        buy_total += bv
        sell_total += sv
        churn += min(bv, sv)
        net = bv - sv
        if net > 0:
            queue.append([i, net])
            continue
        rs = -net
        while rs > 0 and queue:
            head = queue[0]
            take = min(head[1], rs)
            held = i - head[0]
            if held <= _FLIP_MAX:
                bucket[STYLE_FLIP] += take
            elif held <= _SHORT_MAX:
                bucket[STYLE_SHORT] += take
            else:
                bucket[STYLE_SWING] += take
            hold_sum += held * take
            hold_vol += take
            head[1] -= take
            rs -= take
            if head[1] <= 0:
                queue.popleft()
        if rs > 0:
            orphan_sell += rs          # 區間前就持有，天期不明，不分類

    open_lots = sum(q[1] for q in queue)    # 未平倉（仍抱著）→ 計入波段
    bucket[STYLE_SWING] += open_lots

    classified = sum(bucket.values())
    if classified <= 0:
        return None
    ratios = {k: v / classified for k, v in bucket.items()}
    top_style = max(ratios, key=lambda k: ratios[k])
    label = top_style if ratios[top_style] >= _MIXED_TH else STYLE_MIXED
    gross = buy_total + sell_total
    return {
        "label": label,
        "flip_ratio": round(ratios[STYLE_FLIP], 4),
        "short_ratio": round(ratios[STYLE_SHORT], 4),
        "swing_ratio": round(ratios[STYLE_SWING], 4),
        # 同日對敲佔毛額比重（0~1）：高＝分公司內對向流多/疑似當沖，僅供參考
        "churn_ratio": round(2 * churn / gross, 4) if gross else 0.0,
        "avg_hold_days": round(hold_sum / hold_vol, 2) if hold_vol else 0.0,
        "active_days": active_days,
        "buy_lots": round(buy_total / 1000),
        "sell_lots": round(sell_total / 1000),
        "net_lots": round((buy_total - sell_total) / 1000),
        "open_lots": round(open_lots / 1000),
        "orphan_sell_lots": round(orphan_sell / 1000),
    }


def stock_profiles(db, stock_code: str, start: str, end: str,
                   min_days: int = MIN_ACTIVE_DAYS,
                   min_lots: int = MIN_TOTAL_LOTS,
                   top_n: int = TOP_BROKERS_PER_STOCK) -> list[dict]:
    """單檔股票的主力分點風格清單。db 需已 connect。

    先以 SQL 篩出「進出量前 top_n 且達門檻」的分點，再只拉這些分點的日序 ——
    單檔全分點動輒數萬列，不過濾會讓全市場重算慢到無法接受。
    分組鍵為 (broker_code, broker_name)，不可只用 code（約三成列 code 為空）。
    """
    cur = db._cursor()
    # buy_volume 是 INT，整檔加總會溢位，必須先 CAST BIGINT
    cur.execute(f"""
        SELECT TOP {int(top_n)} broker_code, broker_name,
               SUM(CAST(buy_volume AS BIGINT) + sell_volume) AS tot
        FROM BrokerDailyStats
        WHERE stock_code=%s AND trade_date>=%s AND trade_date<=%s
        GROUP BY broker_code, broker_name
        HAVING COUNT(DISTINCT trade_date) >= %s
           AND SUM(CAST(buy_volume AS BIGINT) + sell_volume) >= %s
        ORDER BY tot DESC
    """, (stock_code, start, end, int(min_days), int(min_lots) * 1000))
    picked = [(r[0], r[1]) for r in cur.fetchall()]
    if not picked:
        return []

    names = sorted({n for _c, n in picked})
    ph = ",".join(["%s"] * len(names))
    cur.execute(f"""
        SELECT broker_code, broker_name, trade_date, buy_volume, sell_volume
        FROM BrokerDailyStats
        WHERE stock_code=%s AND trade_date>=%s AND trade_date<=%s
          AND broker_name IN ({ph})
        ORDER BY broker_code, broker_name, trade_date
    """, (stock_code, start, end, *names))
    by_key: dict[tuple, list[dict]] = defaultdict(list)
    for bc, bn, _td, bv, sv in cur.fetchall():
        by_key[(bc, bn)].append({"buy_volume": bv, "sell_volume": sv})

    out = []
    for key in picked:
        rows = by_key.get(key)
        if not rows:
            continue
        res = classify_series(rows)
        if not res:
            continue
        res["stock_code"] = stock_code
        res["broker_code"] = key[0]
        res["broker_name"] = key[1]
        res["window_start"] = start
        res["window_end"] = end
        out.append(res)
    out.sort(key=lambda x: x["buy_lots"] + x["sell_lots"], reverse=True)
    return out


def rebuild_all(db, start: str, end: str, stock_codes: list[str] | None = None,
                on_progress=None, should_stop=None,
                min_days: int = MIN_ACTIVE_DAYS,
                min_lots: int = MIN_TOTAL_LOTS,
                top_n: int = TOP_BROKERS_PER_STOCK) -> dict:
    """全市場重算並寫入 BrokerStockProfile。db 需已 connect。

    on_progress(done, total, stock_code, saved) 每檔回報一次；
    should_stop() 回 True 即中止（已寫入的保留）。
    回 {stocks, profiles, skipped, elapsed, stopped}。
    """
    import time

    if stock_codes is None:
        cur = db._cursor()
        cur.execute("""
            SELECT DISTINCT stock_code FROM BrokerDailyStats
            WHERE trade_date>=%s AND trade_date<=%s
            ORDER BY stock_code
        """, (start, end))
        stock_codes = [r[0] for r in cur.fetchall()]

    total = len(stock_codes)
    saved = skipped = 0
    t0 = time.time()
    stopped = False
    for i, code in enumerate(stock_codes, 1):
        if should_stop and should_stop():
            stopped = True
            break
        try:
            rows = stock_profiles(db, code, start, end, min_days, min_lots,
                                  top_n)
            if rows:
                saved += db.save_broker_profiles(rows)
            else:
                skipped += 1
        except Exception as e:  # noqa: BLE001
            skipped += 1
            log.warning("profile failed %s: %s", code, e)
        if on_progress:
            on_progress(i, total, code, saved)
    return {"stocks": total, "profiles": saved, "skipped": skipped,
            "elapsed": time.time() - t0, "stopped": stopped}
