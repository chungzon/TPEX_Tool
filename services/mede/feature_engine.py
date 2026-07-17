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
    bidask_updates: int
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
    # 價格衍生
    vwap: float
    recent_high: float
    recent_low: float
    price_velocity: float
    price_accel: float
    price_range_ticks: float
    # BED 空方結構（Phase 3）：開盤價、VWAP 斜率、因果式 swing 擺動點
    open_price: float
    vwap_slope: float                # VWAP 每秒變化（price/sec，>0 上彎 <0 下彎）
    swing_high: float                # 最近已確認 swing high（0=尚未形成）
    swing_low: float                 # 最近已確認 swing low
    prev_swing_high: float           # 前一個 swing high（供 Lower High 比較）
    prev_swing_low: float            # 前一個 swing low（供 Lower Low 比較）
    # 委託簿動態（最近一筆 BidAsk）
    bid_consumed: float
    bid_cancelled: float
    bid_replenished: float
    ask_consumed: float
    ask_cancelled: float
    ask_replenished: float
    bid_moved: int
    ask_moved: int
    bid_repl_ratio: float
    ask_repl_ratio: float
    bid_collapse_ratio: float
    ask_collapse_ratio: float
    # 各視窗成交/流動特徵
    time_windows: dict = field(default_factory=dict)
    event_windows: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


class _SwingTracker:
    """因果式 ZigZag 擺動點（只用當下與過去，確認後不 repaint）。

    價格自當前段極值反向回撤達 reversal_ticks（且極值後至少經過 confirm_ticks 筆）
    即確認一個 swing，並翻轉段方向。這樣得到的 swing high/low 交替出現，且一旦確認
    就固定 → 供 Lower High / 跌破微結構低點 使用，避免用未來大量資料決定 swing。
    """

    __slots__ = ("_tsf", "rev", "confirm", "leg", "ext", "ext_seq", "bars",
                 "swing_high", "swing_low", "prev_high", "prev_low",
                 "high_seq", "low_seq")

    def __init__(self, tick_size_fn, reversal_ticks: float, confirm_ticks: int):
        self._tsf = tick_size_fn
        self.rev = max(0.5, reversal_ticks)
        self.confirm = max(1, confirm_ticks)
        self.leg = 0                 # +1 上升段找高 / -1 下降段找低 / 0 未定
        self.ext = None              # 當前段極值
        self.ext_seq = 0
        self.bars = 0                # 自極值更新後經過筆數
        self.swing_high = None
        self.swing_low = None
        self.prev_high = None
        self.prev_low = None
        self.high_seq = 0
        self.low_seq = 0

    def update(self, price: float, seq: int) -> None:
        if self.ext is None:
            self.ext = price
            self.ext_seq = seq
            self.leg = 1
            self.bars = 0
            return
        ts = self._tsf(price) if price > 0 else 0.0
        thr = self.rev * ts
        if self.leg >= 0:            # 找高點
            if price >= self.ext:
                self.ext = price
                self.ext_seq = seq
                self.bars = 0
            else:
                self.bars += 1
                if self.bars >= self.confirm and thr > 0 and (self.ext - price) >= thr:
                    self.prev_high = self.swing_high
                    self.swing_high = self.ext
                    self.high_seq = self.ext_seq
                    self.leg = -1
                    self.ext = price
                    self.ext_seq = seq
                    self.bars = 0
        else:                        # 找低點
            if price <= self.ext:
                self.ext = price
                self.ext_seq = seq
                self.bars = 0
            else:
                self.bars += 1
                if self.bars >= self.confirm and thr > 0 and (price - self.ext) >= thr:
                    self.prev_low = self.swing_low
                    self.swing_low = self.ext
                    self.low_seq = self.ext_seq
                    self.leg = 1
                    self.ext = price
                    self.ext_seq = seq
                    self.bars = 0


class FeatureEngine:
    """吃 tick / bidask（與錄製/即時同格式的 dict），增量維護特徵。"""

    def __init__(self, tick_size_fn, time_windows=None, event_windows=None,
                 breakout_lookback_ticks: int = 100, velocity_window_ms: int = 1000,
                 swing_reversal_ticks: float = 3.0, swing_confirm_ticks: int = 3,
                 vwap_slope_window_ms: int = 3000):
        self._tick_size = tick_size_fn
        self._tw_spec = time_windows or DEFAULT_TIME_WINDOWS
        self._ew_spec = event_windows or DEFAULT_EVENT_WINDOWS
        self._twins = {lab: _TimeWin(ms * 1_000_000) for lab, ms in self._tw_spec}
        self._ewins = {lab: _CountWin(n) for lab, n in self._ew_spec}
        self._lookback = max(2, breakout_lookback_ticks)
        self._vel_span = velocity_window_ms * 1_000_000
        # BED 空方結構
        self._open_price = 0.0
        self._swing = _SwingTracker(tick_size_fn, swing_reversal_ticks,
                                    swing_confirm_ticks)
        self._vwap_span = vwap_slope_window_ms * 1_000_000
        self._vwap_hist: deque = deque()      # (t_ns, vwap) 供斜率
        self._vwap_slope = 0.0
        # 即時狀態
        self.code = ""
        self.last_price = 0.0
        self.consec_buy = 0
        self.consec_sell = 0
        self._last_t_ns = 0
        self._last_seq = 0
        # book
        self._has_ba = False
        self._ba_updates = 0
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
        # 價格衍生：VWAP / 近高低 / 價速
        self._pv_sum = 0.0
        self._v_sum = 0.0
        self._price_hist: deque = deque()   # (t_ns, price) 近 lookback 筆
        self._vel_hist: deque = deque()     # (t_ns, price) 速度窗
        self._recent_high = 0.0
        self._recent_low = 0.0
        self._velocity = 0.0
        self._accel = 0.0
        # 委託簿動態（每筆 BidAsk 更新）：消耗 / 撤單 / 補單 / 移價
        self._traded_at_bid = 0.0
        self._traded_at_ask = 0.0
        self._bid_consumed = self._bid_cancelled = self._bid_replenished = 0.0
        self._ask_consumed = self._ask_cancelled = self._ask_replenished = 0.0
        self._bid_moved = 0
        self._ask_moved = 0
        self._bid_consumed_cum = 0.0   # 同價累積消耗（供補單/隱藏流動性判斷）
        self._ask_consumed_cum = 0.0
        self._bid_collapse_ratio = 0.0
        self._ask_collapse_ratio = 0.0

    # ---------------- Tick ----------------
    def on_tick(self, tick: dict) -> None:
        self.code = tick.get("code", self.code)
        price = _f(tick.get("close"))
        vol = _f(tick.get("volume"))
        if price <= 0 or vol <= 0:
            return
        self.last_price = price
        if self._open_price <= 0:
            self._open_price = price
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
        # VWAP
        self._pv_sum += price * vol
        self._v_sum += vol
        # 近高低（以「加入本筆前」窗為基準 → 突破＝現價越過前高/前低）
        if self._price_hist:
            self._recent_high = max(p for _, p in self._price_hist)
            self._recent_low = min(p for _, p in self._price_hist)
        else:
            self._recent_high = self._recent_low = price
        self._price_hist.append((t_ns, price))
        while len(self._price_hist) > self._lookback:
            self._price_hist.popleft()
        # 價速 / 加速度
        self._vel_hist.append((t_ns, price))
        while self._vel_hist and t_ns - self._vel_hist[0][0] > self._vel_span:
            self._vel_hist.popleft()
        if len(self._vel_hist) >= 2:
            t0, p0 = self._vel_hist[0]
            dt = (t_ns - t0) / 1e9
            v = (price - p0) / dt if dt > 0 else 0.0
            self._accel = v - self._velocity
            self._velocity = v
        # BED：swing 擺動點（因果）+ VWAP 斜率
        self._swing.update(price, self._last_seq)
        cur_vwap = self._pv_sum / self._v_sum if self._v_sum > 0 else price
        self._vwap_hist.append((t_ns, cur_vwap))
        while self._vwap_hist and t_ns - self._vwap_hist[0][0] > self._vwap_span:
            self._vwap_hist.popleft()
        if len(self._vwap_hist) >= 2:
            vt0, vv0 = self._vwap_hist[0]
            vdt = (t_ns - vt0) / 1e9
            self._vwap_slope = (cur_vwap - vv0) / vdt if vdt > 0 else 0.0
        # 成交歸屬最佳檔（供 QueueCollapse / Replenishment）
        if self._has_ba:
            if abs(price - self._bid_p[0]) < 1e-9:
                self._traded_at_bid += vol
                self._bid_consumed_cum += vol
            elif abs(price - self._ask_p[0]) < 1e-9:
                self._traded_at_ask += vol
                self._ask_consumed_cum += vol

    # ---------------- BidAsk ----------------
    def on_bidask(self, ba: dict) -> None:
        self._has_ba = True
        self._ba_updates += 1
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
        # 委託簿動態（用「前值 self._bid_p」對比「新值 bp」）
        self._book_dynamics(bp, bq, ap, aq)
        self._bid_p, self._bid_q, self._ask_p, self._ask_q = bp, bq, ap, aq
        self._prev_bid_p, self._prev_bid_q = bp[:], bq[:]
        self._prev_ask_p, self._prev_ask_q = ap[:], aq[:]
        t_ns = parse_exchange_ns(ba.get("time", "")) or (self._last_t_ns + 1)
        self._last_t_ns = t_ns
        for w in self._twins.values():
            w.add_ofi(t_ns, self._ofi_l1)

    def _book_dynamics(self, bp, bq, ap, aq) -> None:
        """最佳檔量變化拆解為 消耗/撤單/補單/移價（消耗＝打在該檔的主動量）。"""
        had = self._prev_bid_p is not None
        pbp, pbq = self._bid_p[0], self._bid_q[0]
        pap, paq = self._ask_p[0], self._ask_q[0]
        # ---- bid ----
        if not had:
            self._bid_consumed = self._bid_cancelled = self._bid_replenished = 0.0
            self._bid_moved = 0
        elif bp[0] < pbp - 1e-9:                 # 最佳買價下移 → 買方支撐被抽/吃掉
            self._bid_moved = -1
            self._bid_consumed = min(self._traded_at_bid, pbq)
            self._bid_cancelled = max(0.0, pbq - self._bid_consumed)
            self._bid_replenished = 0.0
            self._bid_consumed_cum = 0.0
        elif bp[0] > pbp + 1e-9:                 # 最佳買價上移 → 買氣轉強（新高買）
            self._bid_moved = 1
            self._bid_consumed = self._bid_cancelled = self._bid_replenished = 0.0
            self._bid_consumed_cum = 0.0
        else:                                    # 同價
            self._bid_moved = 0
            self._bid_consumed = self._traded_at_bid
            expected = pbq - self._traded_at_bid
            self._bid_cancelled = max(0.0, expected - bq[0])
            self._bid_replenished = max(0.0, bq[0] - expected)
        # ---- ask ----
        if not had:
            self._ask_consumed = self._ask_cancelled = self._ask_replenished = 0.0
            self._ask_moved = 0
        elif ap[0] > pap + 1e-9:                 # 最佳賣價上移 → 賣壓被買方吃掉
            self._ask_moved = 1
            self._ask_consumed = min(self._traded_at_ask, paq)
            self._ask_cancelled = max(0.0, paq - self._ask_consumed)
            self._ask_replenished = 0.0
            self._ask_consumed_cum = 0.0
        elif ap[0] < pap - 1e-9:                 # 最佳賣價下移 → 賣壓轉強（新低賣）
            self._ask_moved = -1
            self._ask_consumed = self._ask_cancelled = self._ask_replenished = 0.0
            self._ask_consumed_cum = 0.0
        else:
            self._ask_moved = 0
            self._ask_consumed = self._traded_at_ask
            expected = paq - self._traded_at_ask
            self._ask_cancelled = max(0.0, expected - aq[0])
            self._ask_replenished = max(0.0, aq[0] - expected)
        self._bid_collapse_ratio = (self._bid_consumed + self._bid_cancelled) / max(pbq, 1.0)
        self._ask_collapse_ratio = (self._ask_consumed + self._ask_cancelled) / max(paq, 1.0)
        self._traded_at_bid = 0.0
        self._traded_at_ask = 0.0

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
        vwap = self._pv_sum / self._v_sum if self._v_sum > 0 else self.last_price
        if self._vel_hist:
            hi = max(p for _, p in self._vel_hist)
            lo = min(p for _, p in self._vel_hist)
            prange = (hi - lo) / ts if ts > 0 else 0.0
        else:
            prange = 0.0
        bid_repl_ratio = self._bid_consumed_cum / max(self._bid_q[0], 1.0)
        ask_repl_ratio = self._ask_consumed_cum / max(self._ask_q[0], 1.0)
        return FeatureSnapshot(
            t_ns=self._last_t_ns, seq=self._last_seq, code=self.code,
            last_price=self.last_price,
            bidask_available=self._has_ba, bidask_updates=self._ba_updates,
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
            vwap=round(vwap, 4), recent_high=self._recent_high,
            recent_low=self._recent_low,
            price_velocity=round(self._velocity, 4),
            price_accel=round(self._accel, 4),
            price_range_ticks=round(prange, 2),
            open_price=self._open_price,
            vwap_slope=round(self._vwap_slope, 5),
            swing_high=self._swing.swing_high or 0.0,
            swing_low=self._swing.swing_low or 0.0,
            prev_swing_high=self._swing.prev_high or 0.0,
            prev_swing_low=self._swing.prev_low or 0.0,
            bid_consumed=self._bid_consumed, bid_cancelled=self._bid_cancelled,
            bid_replenished=self._bid_replenished,
            ask_consumed=self._ask_consumed, ask_cancelled=self._ask_cancelled,
            ask_replenished=self._ask_replenished,
            bid_moved=self._bid_moved, ask_moved=self._ask_moved,
            bid_repl_ratio=round(bid_repl_ratio, 2),
            ask_repl_ratio=round(ask_repl_ratio, 2),
            bid_collapse_ratio=round(self._bid_collapse_ratio, 3),
            ask_collapse_ratio=round(self._ask_collapse_ratio, 3),
            time_windows={lab: w.features() for lab, w in self._twins.items()},
            event_windows={lab: w.features() for lab, w in self._ewins.items()},
        )
