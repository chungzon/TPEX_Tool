"""台股價格分級跳動（tick size）— 供 FeatureEngine / 重播偵測計算 spread、gap。

TWSE/TPEX 一般股票的六段升降單位（單位：元）：
    < 10       → 0.01
    10 ~ 50    → 0.05
    50 ~ 100   → 0.10
    100 ~ 500  → 0.50
    500 ~ 1000 → 1.00
    >= 1000    → 5.00
邊界採「未滿」歸下一級（如 50.0 屬 50~100 級 = 0.10）。
"""

from __future__ import annotations


def tw_tick_size(price: float) -> float:
    """回傳該價位的最小跳動單位（元）。price<=0 時回 0.0（呼叫端據此略過）。"""
    if price <= 0:
        return 0.0
    if price < 10:
        return 0.01
    if price < 50:
        return 0.05
    if price < 100:
        return 0.10
    if price < 500:
        return 0.50
    if price < 1000:
        return 1.00
    return 5.00
