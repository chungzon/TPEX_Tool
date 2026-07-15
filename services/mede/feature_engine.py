"""MEDE Feature Engine — 由 Tick / BidAsk 事件增量計算市場微結構特徵。

原則（Phase 3 決定性要求）：
- 只用「當下與過去」，不看未來；視窗以**交易所事件時間**(exchange time)為準，
  不用系統牆鐘 → 即時模擬與重播吃同一份事件會得到**完全相同**的 FeatureSnapshot。
- rolling 用 deque + running sum 增量維護，不每筆掃全歷史。
- OFI / MLOFI 依 Cont–Kukanov–Stoikov：用前後兩筆 BidAsk 的**最佳價與掛量變化**計算，
  絕不用「買五總量 − 賣五總量」冒充。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from services.mede.enums import TradeSide


def parse_exchange_ns(s: str) -> int | None:
    """'YYYY-MM-DD HH:MM:SS.ffffff' 或 'HH:MM:SS.ffffff' → 當日秒數(ns)。"""
    try:
        tp = s.strip().split(" ")[-1]
        hh, mm, rest = tp.split(":")
        if "." in rest:
            ss, frac = rest.split(".")
            frac = (frac + "000000")[:6]
        else:
            ss, frac = rest, "000000"
        return ((int(hh) * 3600 + int(mm) * 60 + int(ss)) * 1_000_000
                + int(frac)) * 1000
    except (ValueError, AttributeError, IndexError):
        return None


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


# 預設視窗（label, 毫秒 / 筆數）— 可由 MedeConfig 覆蓋
DEFAULT_TIME_WINDOWS = [("1s", 1000), ("3s", 3000), ("5s", 5000)]
DEFAULT_EVENT_WINDOWS = [("20t", 20), ("50t", 50)]


class _TimeWin:
    """時間滑動視窗（span 為 ns）。running sum 增量維護。"""
    __slots__ = ("span", "dq", "buy_vol", "sell_vol", "unk_vol",
                 "buy_cnt", "sell_cnt", "unk_cnt", "ofi_sum", "_max_buy", "_max_sell")

    def __init__(self, span_ns: int):
        self.span = span_ns
        self.dq: deque = deque()      # (t_ns, side, vol, ofi)
        self.buy_vol = self.sell_vol = self.unk_vol = 0.0
        self.buy_cnt = self.sell_cnt = self.unk_cnt = 0
        self.ofi_sum = 0.0
        self._max_buy = self._max_sell = 0.0

    def add_trade(self, t_ns, side, vol):
        self.dq.append((t_ns, side, vol, 0.0))
        if side > 0:
            self.buy_vol += vol; self.buy_cnt += 1
            if vol > self._max_buy:
                self._max_buy = vol
        elif side < 0:
            self.sell_vol += vol; self.sell_cnt += 1
            if vol > self._max_sell:
                self._max_sell = vol
        else:
            self.unk_vol += vol; self.unk_cnt += 1
        self._evict(t_ns)

    def add_ofi(self, t_ns, ofi):
        self.dq.append((t_ns, 0, 0.0, ofi))
        self.ofi_sum += ofi
        self._evict(t_ns)

    def _evict(self, now):
        rebuild_max = False
        while self.dq and now - self.dq[0][0] > self.span:
            t, side, vol, ofi = self.dq.popleft()
            if side > 0:
                self.buy_vol -= vol; self.buy_cnt -= 1
                if vol >= self._max_buy:
                    rebuild_max = True
            elif side < 0:
                self.sell_vol -= vol; self.sell_cnt -= 1
                if vol >= self._max_sell:
                    rebuild_max = True
            else:
                self.unk_vol -= vol; self.unk_cnt -= 1
            self.ofi_sum -= ofi
        if rebuild_max:
            self._max_buy = max((v for _, s, v, _ in self.dq if s > 0), default=0.0)
            self._max_sell = max((v for _, s, v, _ in self.dq if s < 0), default=0.0)

    def features(self) -> dict:
        bv, sv = self.buy_vol, self.sell_vol
        bc, sc = self.buy_cnt, self.sell_cnt
        return {
            "buy_vol": bv, "sell_vol": sv, "unk_vol": self.unk_vol,
            "buy_cnt": bc, "sell_cnt": sc,
            "trade_imbalance": (bv - sv) / max(bv + sv, 1e-9),
            "count_imbalance": (bc - sc) / max(bc + sc, 1),
            "avg_buy_size": bv / bc if bc else 0.0,
            "avg_sell_size": sv / sc if sc else 0.0,
            "largest_buy": self._max_buy, "largest_sell": self._max_sell,
            "ofi_sum": self.ofi_sum,
        }


class _CountWin:
    """最近 N 筆事件視窗。"""
    __slots__ = ("n", "dq", "buy_vol", "sell_vol", "unk_vol",
                 "buy_cnt", "sell_cnt", "unk_cnt", "ofi_sum")

    def __init__(self, n: int):
        self.n = n
        self.dq: deque = deque()
        self.buy_vol = self.sell_vol = self.unk_vol = 0.0
        self.buy_cnt = self.sell_cnt = self.unk_cnt = 0
        self.ofi_sum = 0.0

    def add(self, side, vol, ofi=0.0):
        if len(self.dq) >= self.n:
            s, v, o = self.dq.popleft()
            if s > 0:
                self.buy_vol -= v; self.buy_cnt -= 1
            elif s < 0:
                self.sell_vol -= v; self.sell_cnt -= 1
            else:
                self.unk_vol -= v; self.unk_cnt -= 1
            self.ofi_sum -= o
        self.dq.append((side, vol, ofi))
        if side > 0:
            self.buy_vol += vol; self.buy_cnt += 1
        elif side < 0:
            self.sell_vol += vol; self.sell_cnt += 1
        else:
            self.unk_vol += vol; self.unk_cnt += 1
        self.ofi_sum += ofi

    def features(self) -> dict:
        bv, sv, bc, sc = self.buy_vol, self.sell_vol, self.buy_cnt, self.sell_cnt
        return {
            "buy_vol": bv, "sell_vol": sv, "buy_cnt": bc, "sell_cnt": sc,
            "trade_imbalance": (bv - sv) / max(bv + sv, 1e-9),
            "count_imbalance": (bc - sc) / max(bc + sc, 1),
            "ofi_sum": self.ofi_sum,
        }


@dataclass
class FeatureSnapshot:
    t_ns: int
    seq: int
    code: str
    last_price: float
    # book（最新五檔）
    bidask_available: bool
    best_bid: float
    best_ask: float
    spread_ticks: float
    bid_qty1: float
    ask_qty1: float
    l1_imbalance: float
    l5_imbalance: float
    weighted_bid_depth: float
    weighted_ask_depth: float
    bid_liquidity_gap: float
    ask_liquidity_gap: float
    # OFI（最近一筆 BidAsk 的增量）+ MLOFI
    ofi_l1: float
    mlofi: list
    integrated_mlofi: float
    # 連續主動方向
    consec_buy: int
    consec_sell: int
    # 各視窗成交/流動特徵
    time_windows: dict = field(default_factory=dict)
    event_windows: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


class FeatureEngine:
    """吃 tick / bidask（與錄製/即時同格式的 dict），增量維護特徵。"""

    def __init__(self, tick_size_fn, time_windows=None, event_windows=None):
        self._tick_size = tick_size_fn
        self._tw_spec = time_windows or DEFAULT_TIME_WINDOWS
        self._ew_spec = event_windows or DEFAULT_EVENT_WINDOWS
        self._twins = {lab: _TimeWin(ms * 1_000_000) for lab, ms in self._tw_spec}
        self._ewins = {lab: _CountWin(n) for lab, n in self._ew_spec}
        # 即時狀態
        self.code = ""
        self.last_price = 0.0
        self.consec_buy = 0
        self.consec_sell = 0
        self._last_t_ns = 0
        self._last_seq = 0
        # book
        self._has_ba = False
        self._bid_p = [0.0] * 5
        self._bid_q = [0.0] * 5
        self._ask_p = [0.0] * 5
        self._ask_q = [0.0] * 5
        self._prev_bid_p = None
        self._prev_bid_q = None
        self._prev_ask_p = None
        self._prev_ask_q = None
        self._ofi_l1 = 0.0
        self._mlofi = [0.0] * 5

    # ---------------- Tick ----------------
    def on_tick(self, tick: dict) -> None:
        self.code = tick.get("code", self.code)
        price = _f(tick.get("close"))
        vol = _f(tick.get("volume"))
        if price <= 0 or vol <= 0:
            return
        self.last_price = price
        side = int(TradeSide.from_tick_type(tick.get("tick_type", 0)))
        t_ns = parse_exchange_ns(tick.get("time", "")) or (self._last_t_ns + 1)
        self._last_t_ns = t_ns
        self._last_seq = int(tick.get("seq", self._last_seq) or self._last_seq)
        # 連續主動方向
        if side > 0:
            self.consec_buy += 1; self.consec_sell = 0
        elif side < 0:
            self.consec_sell += 1; self.consec_buy = 0
        for w in self._twins.values():
            w.add_trade(t_ns, side, vol)
        for w in self._ewins.values():
            w.add(side, vol)

    # ---------------- BidAsk ----------------
    def on_bidask(self, ba: dict) -> None:
        self._has_ba = True
        bp = [_f(x) for x in (ba.get("bid_price") or [])][:5]
        bq = [_f(x) for x in (ba.get("bid_volume") or [])][:5]
        ap = [_f(x) for x in (ba.get("ask_price") or [])][:5]
        aq = [_f(x) for x in (ba.get("ask_volume") or [])][:5]
        bp += [0.0] * (5 - len(bp)); bq += [0.0] * (5 - len(bq))
        ap += [0.0] * (5 - len(ap)); aq += [0.0] * (5 - len(aq))
        # OFI / MLOFI（vs 前一筆 BidAsk）
        if self._prev_bid_p is not None:
            self._mlofi = [self._level_ofi(bp[i], bq[i], self._prev_bid_p[i],
                                           self._prev_bid_q[i], ap[i], aq[i],
                                           self._prev_ask_p[i], self._prev_ask_q[i])
                           for i in range(5)]
            self._ofi_l1 = self._mlofi[0]
        else:
            self._mlofi = [0.0] * 5
            self._ofi_l1 = 0.0
        self._bid_p, self._bid_q, self._ask_p, self._ask_q = bp, bq, ap, aq
        self._prev_bid_p, self._prev_bid_q = bp[:], bq[:]
        self._prev_ask_p, self._prev_ask_q = ap[:], aq[:]
        t_ns = parse_exchange_ns(ba.get("time", "")) or (self._last_t_ns + 1)
        self._last_t_ns = t_ns
        for w in self._twins.values():
            w.add_ofi(t_ns, self._ofi_l1)

    @staticmethod
    def _level_ofi(pb, qb, pb0, qb0, pa, qa, pa0, qa0) -> float:
        """單一檔位 OFI = ΔW(bid) − ΔV(ask)（Cont–Kukanov–Stoikov）。"""
        if pb > pb0:
            dw = qb
        elif pb == pb0:
            dw = qb - qb0
        else:
            dw = -qb0
        if pa < pa0:
            dv = qa
        elif pa == pa0:
            dv = qa - qa0
        else:
            dv = -qa0
        return dw - dv

    # ---------------- Snapshot ----------------
    def snapshot(self) -> FeatureSnapshot:
        bsum = sum(self._bid_q)
        asum = sum(self._ask_q)
        b1, a1 = self._bid_q[0], self._ask_q[0]
        # 深度加權（越近檔位權重越高：1..0.2）
        w = [1.0, 0.8, 0.6, 0.4, 0.2]
        wbid = sum(self._bid_q[i] * w[i] for i in range(5))
        wask = sum(self._ask_q[i] * w[i] for i in range(5))
        ts = self._tick_size(self.last_price) if self.last_price > 0 else 0.0
        spread_ticks = ((self._ask_p[0] - self._bid_p[0]) / ts
                        if ts > 0 and self._ask_p[0] > 0 and self._bid_p[0] > 0 else 0.0)
        # 流動性缺口：相鄰檔位價差（以 tick 計）
        bid_gap = ((self._bid_p[0] - self._bid_p[1]) / ts
                   if ts > 0 and self._bid_p[1] > 0 else 0.0)
        ask_gap = ((self._ask_p[1] - self._ask_p[0]) / ts
                   if ts > 0 and self._ask_p[1] > 0 else 0.0)
        return FeatureSnapshot(
            t_ns=self._last_t_ns, seq=self._last_seq, code=self.code,
            last_price=self.last_price,
            bidask_available=self._has_ba,
            best_bid=self._bid_p[0], best_ask=self._ask_p[0],
            spread_ticks=round(spread_ticks, 4),
            bid_qty1=b1, ask_qty1=a1,
            l1_imbalance=(b1 - a1) / max(b1 + a1, 1e-9),
            l5_imbalance=(bsum - asum) / max(bsum + asum, 1e-9),
            weighted_bid_depth=wbid, weighted_ask_depth=wask,
            bid_liquidity_gap=round(bid_gap, 4), ask_liquidity_gap=round(ask_gap, 4),
            ofi_l1=self._ofi_l1, mlofi=list(self._mlofi),
            integrated_mlofi=sum(self._mlofi),
            consec_buy=self.consec_buy, consec_sell=self.consec_sell,
            time_windows={lab: w.features() for lab, w in self._twins.items()},
            event_windows={lab: w.features() for lab, w in self._ewins.items()},
        )
