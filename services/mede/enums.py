"""MEDE 統一列舉 — 取代散落各處的 magic number。"""

from __future__ import annotations

from enum import Enum, IntEnum


class TradeSide(IntEnum):
    """成交主動方向（由 Shioaji tick_type 對應，全系統統一使用）。"""
    UNKNOWN = 0
    BUY = 1      # 外盤：買方主動觸發（aggressive buy）
    SELL = -1    # 內盤：賣方主動觸發（aggressive sell）

    @classmethod
    def from_tick_type(cls, tick_type) -> "TradeSide":
        try:
            tt = int(tick_type)
        except (TypeError, ValueError):
            return cls.UNKNOWN
        if tt == 1:
            return cls.BUY
        if tt == 2:
            return cls.SELL
        return cls.UNKNOWN


class StreamKind(str, Enum):
    TICK = "tick"
    BIDASK = "bidask"


class DataQualityStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"     # 有缺口/延遲/亂序，但仍可用
    INVALID = "invalid"       # 資料不足或異常，不納入正式統計
