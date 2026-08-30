"""連次連量引擎 — 逐筆成交的「連續買/賣」統計（仿當沖神手）。

每檔維護一個 StreakState：消費 Shioaji tick（含 tick_type 內外盤），累計
- 連次：連續同方向（外盤買/內盤賣）成交筆數，反向即重置
- 連量：該段連續累計成交張數
- 當日外盤/內盤總量、總成交量、大單筆數
純計算、無 I/O；由 viewmodel 在 tick callback 呼叫 update()。

tick_type: 1=外盤(買方主動/上漲成交) 2=內盤(賣方主動/下跌成交) 0=無法判定
單位：Shioaji tick.volume 為「張」。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StreakState:
    code: str
    name: str = ""
    last_price: float = 0.0        # 最新成交價
    prev_close: float = 0.0        # 昨收（算漲跌幅）
    open_price: float = 0.0        # 開盤（無昨收時備援基準）
    # 連續狀態：dir +1=連買(外盤) -1=連賣(內盤) 0=無
    streak_dir: int = 0
    streak_count: int = 0          # 連次
    streak_vol: int = 0            # 連量（張）
    # 當日累計
    outer_vol: int = 0             # 外盤總量（張）
    inner_vol: int = 0             # 內盤總量（張）
    total_vol: int = 0            # 總成交量（張）
    big_orders: int = 0            # 大單筆數（單筆量 >= 門檻）
    last_big: int = 0             # 最近一筆大單量（0=無）
    last_side: int = 0            # 最近一筆方向（+1/-1/0）
    # 竭盡偵測：追蹤本段連續的峰值（反轉判斷用）
    peak_count: int = 0            # 本段連續峰值筆數
    peak_vol: int = 0             # 本段連續峰值累計量
    exhaust: int = 0             # 竭盡旗標：+1=買盤竭盡(做頭) -1=賣盤竭盡(打底) 0=無
    exhaust_at: str = ""          # 觸發時間戳（供 UI 顯示/去重）
    exhaust_count: int = 0        # 觸發竭盡時「前一段連續」筆數（peak 已重置，另存供錄製）
    exhaust_vol: int = 0          # 觸發竭盡時「前一段連續」累計量（張）

    @property
    def change(self) -> float:
        base = self.prev_close or self.open_price
        return (self.last_price - base) if base else 0.0

    @property
    def change_pct(self) -> float:
        base = self.prev_close or self.open_price
        return (self.change / base * 100) if base else 0.0

    @property
    def outer_ratio(self) -> float:
        """外盤佔比%（外盤 / (外+內)）。"""
        tot = self.outer_vol + self.inner_vol
        return (self.outer_vol / tot * 100) if tot else 0.0


# 竭盡偵測門檻：本段連續達「N 筆」或「M 張」後反轉，視為買/賣盤竭盡
_EXHAUST_MIN_COUNT = 8      # 反轉前連續筆數 >= 此值
_EXHAUST_MIN_VOL = 300     # 或反轉前連續累計量(張) >= 此值


def update(st: StreakState, tick: dict, big_lots: int = 100) -> None:
    """以一筆 tick 更新連次連量狀態（就地）。

    tick: {close, volume(張), tick_type(1外/2內/0), ...}。
    big_lots: 單筆量 >= 此值視為大單。
    反轉時若前段連續達門檻 → 設 st.exhaust（+1買盤竭盡/-1賣盤竭盡）。
    """
    price = tick.get("close") or tick.get("price") or 0
    vol = int(tick.get("volume") or 0)
    tt = tick.get("tick_type", 0)
    st.exhaust = 0      # 每筆先清，僅在觸發的那一筆為非零（供 UI 閃燈）
    if price:
        st.last_price = float(price)
    if vol <= 0:
        return
    side = 1 if tt == 1 else -1 if tt == 2 else 0
    st.total_vol += vol
    if side == 1:
        st.outer_vol += vol
    elif side == -1:
        st.inner_vol += vol
    # 大單
    st.last_big = vol if vol >= big_lots else 0
    if vol >= big_lots:
        st.big_orders += 1
    st.last_side = side
    # 連次連量：同向累加，反向或中性→以本筆方向重啟
    if side == 0:
        return
    if side == st.streak_dir:
        st.streak_count += 1
        st.streak_vol += vol
        if st.streak_count > st.peak_count:
            st.peak_count = st.streak_count
        if st.streak_vol > st.peak_vol:
            st.peak_vol = st.streak_vol
    else:
        # 方向反轉：檢查「前一段連續」峰值是否達竭盡門檻
        if st.streak_dir != 0 and (
                st.streak_count >= _EXHAUST_MIN_COUNT
                or st.streak_vol >= _EXHAUST_MIN_VOL):
            # 前段為連買(+1)反轉 → 買盤竭盡(+1，做頭)；連賣反轉 → 賣盤竭盡(-1)
            st.exhaust = st.streak_dir
            st.exhaust_at = str(tick.get("time", ""))
            st.exhaust_count = st.streak_count      # 峰值即將被重置，先存供錄製
            st.exhaust_vol = st.streak_vol
        st.streak_dir = side
        st.streak_count = 1
        st.streak_vol = vol
        st.peak_count = 1
        st.peak_vol = vol


def reset_streak(st: StreakState) -> None:
    """僅重置連續段（跨日或手動歸零時用），保留當日累計另由呼叫端處理。"""
    st.streak_dir = 0
    st.streak_count = 0
    st.streak_vol = 0
