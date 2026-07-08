"""市場微觀結構 / 大單追蹤引擎 — pure-compute, thread-safe.

Feed it real-time Tick + BidAsk events (parsed dicts, source-agnostic) and it
maintains sliding-window state for three detectors:

1. OrderBookTracker   — 委託單失衡度 OBI（第一檔 & 前五檔），連續失衡蓄勢偵測。
2. TickVolumeBucket   — VPIN 簡化版，以固定成交量分桶，量測知情交易毒性 / 買方推進率。
3. LargeOrderMonitor  — 單筆大單、外盤連續掃貨起漲點、冰山訂單（隱藏吸貨/壓盤）偵測。

Design notes
------------
* 高頻資料流：所有滑動窗口都用 ``collections.deque`` 固定 maxlen，絕不 pandas.concat。
* 執行緒安全：Shioaji 的行情 callback 在自己的 socket thread 觸發，所有寫入都上鎖。
* 事件驅動：``on_tick`` / ``on_bidask`` 只更新內部狀態並推 alert；UI 端以固定
  頻率（~0.4s）呼叫 ``snapshot()`` 讀狀態，不會每 tick 都刷 UI。
* 買賣盤觸發判斷（Tick Classification）：優先採用 Shioaji 提供的 ``tick_type``
  （1=外盤/買方觸發, 2=內盤/賣方觸發, 0=無法判定）；為 0 時退回李查森校準法
  （Tick Test）：現價 > 前價→買方，< 前價→賣方，相等→沿用上一筆屬性。
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable


def _now_str() -> str:
    """目前本地時間（時:分:秒），用於訊號/大單/買賣點的時間戳。"""
    return datetime.now().strftime("%H:%M:%S")


# --------------------------------------------------------------------------- #
#  Thresholds / configuration
# --------------------------------------------------------------------------- #

@dataclass
class MicroConfig:
    """可調參數，全部有合理預設值。"""

    # OBI（委託單失衡度）
    obi_threshold: float = 0.6          # |OBI| 觸發蓄勢的門檻
    obi_sustain_ticks: int = 5          # 連續 N 筆 bidask 維持在門檻上才算「蓄勢」
    obi_window: int = 60                # OBI 滑動窗口長度（保留最近 N 筆）

    # VPIN（成交量分桶）
    bucket_size: float = 500.0          # 每桶固定成交量（張）
    vpin_buckets: int = 20              # VPIN 取最近 N 桶平均
    buy_push_ratio: float = 0.75        # 單桶買方推進率 ≥ 此值 → 動能點火

    # 大單 / 起漲點
    large_order_mult: float = 5.0       # 單筆量 ≥ 平均每筆 × 此倍數 → 大單
    large_order_floor: float = 10.0     # 大單最低張數（避免開盤平均過小誤判）
    trade_window: int = 200             # 計算「平均每筆成交量」的滑動窗口
    min_trades_for_avg: int = 20        # 至少累積這麼多筆才開始判大單
    attack_consecutive: int = 2         # 連續 N 筆外盤大單且價格跳檔 → 起漲點攻擊

    # 冰山訂單
    iceberg_mult: float = 3.0           # 某檔實際成交量 > 顯示掛量 × 此倍數且價未破 → 冰山
    iceberg_min_visible: float = 5.0    # 該檔顯示掛量至少要有這麼多張才判斷（濾雜訊）


# --------------------------------------------------------------------------- #
#  Alert record
# --------------------------------------------------------------------------- #

@dataclass
class Alert:
    time: str
    kind: str          # 'setup' | 'momentum' | 'attack' | 'large' | 'iceberg'
    level: str         # 'info' | 'warn' | 'strong'
    message: str
    price: float = 0.0
    side: str = ""     # 'buy' | 'sell' | ''（方向，供彙整買賣點用）

    def as_dict(self) -> dict:
        return {
            "time": self.time, "kind": self.kind, "level": self.level,
            "message": self.message, "price": self.price, "side": self.side,
        }


def _f(x: Any, default: float = 0.0) -> float:
    """Decimal / str / None → float（Shioaji 欄位常是 Decimal）。"""
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
#  1) OrderBookTracker — 委託單失衡度 OBI
# --------------------------------------------------------------------------- #

class OrderBookTracker:
    """維護最新五檔委買/委賣，計算即時 OBI 並偵測連續失衡蓄勢。"""

    def __init__(self, cfg: MicroConfig):
        self.cfg = cfg
        self.bid_price: list[float] = [0.0] * 5
        self.bid_volume: list[float] = [0.0] * 5
        self.ask_price: list[float] = [0.0] * 5
        self.ask_volume: list[float] = [0.0] * 5
        self.obi1 = 0.0        # 第一檔 OBI
        self.obi5 = 0.0        # 前五檔累積 OBI
        self._obi5_hist: deque[float] = deque(maxlen=cfg.obi_window)
        self.setup_active = False   # 是否處於蓄勢狀態
        self.setup_side = ""        # 'buy' | 'sell' | ''

    def update(self, ba: dict, now: str) -> Alert | None:
        """吃一筆 BidAsk。回傳 alert（若剛觸發買/賣蓄勢）否則 None。"""
        self.bid_price = [_f(p) for p in ba.get("bid_price", [])][:5] or self.bid_price
        self.bid_volume = [_f(v) for v in ba.get("bid_volume", [])][:5] or self.bid_volume
        self.ask_price = [_f(p) for p in ba.get("ask_price", [])][:5] or self.ask_price
        self.ask_volume = [_f(v) for v in ba.get("ask_volume", [])][:5] or self.ask_volume

        b1, a1 = (self.bid_volume[0], self.ask_volume[0])
        self.obi1 = (b1 - a1) / (b1 + a1) if (b1 + a1) > 0 else 0.0

        bsum, asum = sum(self.bid_volume), sum(self.ask_volume)
        self.obi5 = (bsum - asum) / (bsum + asum) if (bsum + asum) > 0 else 0.0
        self._obi5_hist.append(self.obi5)

        # 蓄勢偵測：最近 obi_sustain_ticks 筆的 OBI5 都同向壓過門檻
        n = self.cfg.obi_sustain_ticks
        thr = self.cfg.obi_threshold
        if len(self._obi5_hist) >= n:
            recent = list(self._obi5_hist)[-n:]
            strong_buy = all(x >= thr for x in recent)
            strong_sell = all(x <= -thr for x in recent)
            if strong_buy and self.setup_side != "buy":
                self.setup_active = True
                self.setup_side = "buy"
                return Alert(
                    time=now, kind="setup", level="warn", side="buy",
                    message=(f"買盤蓄勢：OBI5 連續 {n} 筆 ≥ {thr:.2f}"
                             f"（買{bsum:.0f}/賣{asum:.0f}），下方支撐轉強"),
                    price=self.bid_price[0],
                )
            if strong_sell and self.setup_side != "sell":
                self.setup_active = True
                self.setup_side = "sell"
                return Alert(
                    time=now, kind="setup", level="warn", side="sell",
                    message=(f"賣壓蓄勢：OBI5 連續 {n} 筆 ≤ -{thr:.2f}"
                             f"（買{bsum:.0f}/賣{asum:.0f}），上方賣壓轉強"),
                    price=self.ask_price[0],
                )
            if not strong_buy and not strong_sell:
                self.setup_active = False
                self.setup_side = ""
        return None


# --------------------------------------------------------------------------- #
#  2) TickVolumeBucket — VPIN 簡化版
# --------------------------------------------------------------------------- #

class TickVolumeBucket:
    """以固定成交量分桶，量測買賣失衡（VPIN）與買方推進率。"""

    def __init__(self, cfg: MicroConfig):
        self.cfg = cfg
        self._buy = 0.0            # 當前桶累積買方觸發量
        self._sell = 0.0           # 當前桶累積賣方觸發量
        self._filled = 0.0         # 當前桶已裝入量
        self._imb: deque[float] = deque(maxlen=cfg.vpin_buckets)  # 每桶 |B-S|/V
        self.vpin = 0.0
        self.cur_buy_ratio = 0.5   # 當前桶即時買方推進率

    def add(self, volume: float, side: int, price: float, time: str) -> Alert | None:
        """加入一筆成交量。side: +1 買方觸發 / -1 賣方觸發 / 0 無法判定。
        單筆量可能跨桶，依標準 VPIN 做量的切分。"""
        remaining = volume
        alert: Alert | None = None
        while remaining > 0:
            space = self.cfg.bucket_size - self._filled
            take = min(space, remaining)
            if side > 0:
                self._buy += take
            elif side < 0:
                self._sell += take
            else:
                # 無法判定：買賣各半，避免偏差
                self._buy += take / 2
                self._sell += take / 2
            self._filled += take
            remaining -= take

            if self._filled >= self.cfg.bucket_size:
                a = self._close_bucket(price, time)
                if a is not None:
                    alert = a  # 保留最後一次點火 alert

        tot = self._buy + self._sell
        self.cur_buy_ratio = (self._buy / tot) if tot > 0 else 0.5
        return alert

    def _close_bucket(self, price: float, time: str) -> Alert | None:
        v = self.cfg.bucket_size
        imb = abs(self._buy - self._sell) / v if v > 0 else 0.0
        self._imb.append(imb)
        self.vpin = sum(self._imb) / len(self._imb) if self._imb else 0.0
        buy_ratio = self._buy / (self._buy + self._sell) if (self._buy + self._sell) > 0 else 0.5

        alert: Alert | None = None
        # 動能點火：一整桶量幾乎全由單邊市價單貢獻
        if buy_ratio >= self.cfg.buy_push_ratio:
            alert = Alert(
                time=time, kind="momentum", level="strong", side="buy",
                price=price,
                message=(f"買方動能點火：滿 {v:.0f} 張桶內買方推進 {buy_ratio*100:.0f}%"
                         f"（VPIN {self.vpin:.2f}），知情買盤湧入"),
            )
        elif buy_ratio <= (1 - self.cfg.buy_push_ratio):
            sell_ratio = 1 - buy_ratio
            alert = Alert(
                time=time, kind="momentum", level="strong", side="sell",
                price=price,
                message=(f"賣方動能點火：滿 {v:.0f} 張桶內賣方推進 {sell_ratio*100:.0f}%"
                         f"（VPIN {self.vpin:.2f}），知情賣盤湧出"),
            )
        # reset bucket
        self._buy = self._sell = self._filled = 0.0
        return alert


# --------------------------------------------------------------------------- #
#  3) LargeOrderMonitor — 大單 / 起漲點 / 冰山
# --------------------------------------------------------------------------- #

class LargeOrderMonitor:
    """偵測單筆大單、外盤連續掃貨起漲點，以及冰山訂單。"""

    def __init__(self, cfg: MicroConfig):
        self.cfg = cfg
        self._trade_vols: deque[float] = deque(maxlen=cfg.trade_window)
        self.avg_trade_vol = 0.0
        self.recent_large: deque[dict] = deque(maxlen=30)  # 最近大單紀錄
        self._attack_streak = 0        # 連續外盤大單且價格上跳計數（起漲）
        self._down_streak = 0          # 連續內盤大單且價格下殺計數（起跌）
        self._last_price = 0.0

        # 冰山狀態：追蹤「當前最佳賣檔」上被吃掉的累積外盤量 vs 顯示掛量
        self._ice_ask_price = 0.0
        self._ice_ask_visible = 0.0    # 該檔第一次出現時的顯示量（取曾見過的最大）
        self._ice_ask_exec = 0.0       # 該檔累積被吃掉的外盤量
        self._ice_bid_price = 0.0
        self._ice_bid_visible = 0.0
        self._ice_bid_exec = 0.0

    # ---- BidAsk：更新冰山追蹤的「顯示掛量 / 檔位」基準 ----
    def on_bidask(self, ask1_price: float, ask1_vol: float,
                  bid1_price: float, bid1_vol: float) -> None:
        # 賣檔換價 → 重置賣方冰山追蹤
        if ask1_price != self._ice_ask_price:
            self._ice_ask_price = ask1_price
            self._ice_ask_visible = ask1_vol
            self._ice_ask_exec = 0.0
        else:
            self._ice_ask_visible = max(self._ice_ask_visible, ask1_vol)
        # 買檔換價 → 重置買方冰山追蹤
        if bid1_price != self._ice_bid_price:
            self._ice_bid_price = bid1_price
            self._ice_bid_visible = bid1_vol
            self._ice_bid_exec = 0.0
        else:
            self._ice_bid_visible = max(self._ice_bid_visible, bid1_vol)

    # ---- Tick：主判斷 ----
    def on_tick(self, price: float, volume: float, side: int, time: str) -> list[Alert]:
        alerts: list[Alert] = []
        cfg = self.cfg

        # --- 冰山累積：外盤成交打在賣檔 / 內盤成交打在買檔 ---
        if side > 0 and self._ice_ask_price and abs(price - self._ice_ask_price) < 1e-6:
            self._ice_ask_exec += volume
            if (self._ice_ask_visible >= cfg.iceberg_min_visible
                    and self._ice_ask_exec > self._ice_ask_visible * cfg.iceberg_mult):
                alerts.append(Alert(
                    time=time, kind="iceberg", level="warn", side="sell",
                    message=(f"賣方冰山：{self._ice_ask_price:.2f} 已成交 "
                             f"{self._ice_ask_exec:.0f} 張 > 顯示 {self._ice_ask_visible:.0f} 張，"
                             f"疑有隱藏賣壓/主力壓盤"),
                    price=self._ice_ask_price,
                ))
                self._ice_ask_exec = 0.0  # 觸發後重置避免洗頻
        if side < 0 and self._ice_bid_price and abs(price - self._ice_bid_price) < 1e-6:
            self._ice_bid_exec += volume
            if (self._ice_bid_visible >= cfg.iceberg_min_visible
                    and self._ice_bid_exec > self._ice_bid_visible * cfg.iceberg_mult):
                alerts.append(Alert(
                    time=time, kind="iceberg", level="strong", side="buy",
                    message=(f"買方冰山：{self._ice_bid_price:.2f} 已成交 "
                             f"{self._ice_bid_exec:.0f} 張 > 顯示 {self._ice_bid_visible:.0f} 張，"
                             f"疑有隱藏買單暗中吸貨"),
                    price=self._ice_bid_price,
                ))
                self._ice_bid_exec = 0.0

        # --- 大單偵測 ---
        is_large = False
        if len(self._trade_vols) >= cfg.min_trades_for_avg and self.avg_trade_vol > 0:
            threshold = max(cfg.large_order_floor,
                            self.avg_trade_vol * cfg.large_order_mult)
            if volume >= threshold:
                is_large = True
                rec = {
                    "time": time, "price": price, "volume": volume,
                    "side": side, "mult": volume / self.avg_trade_vol,
                }
                self.recent_large.append(rec)
                side_txt = "外盤" if side > 0 else ("內盤" if side < 0 else "平盤")
                alerts.append(Alert(
                    time=time, kind="large",
                    level="strong" if side != 0 else "info",
                    side="buy" if side > 0 else ("sell" if side < 0 else ""),
                    message=(f"大單成交（{side_txt}）：{volume:.0f} 張 @ {price:.2f} "
                             f"（{volume/self.avg_trade_vol:.1f}×均量）"),
                    price=price,
                ))

        # --- 起漲點：外盤大單 + 價格向上跳檔連續發生 ---
        if is_large and side > 0 and self._last_price > 0 and price > self._last_price:
            self._attack_streak += 1
            self._down_streak = 0
            if self._attack_streak >= cfg.attack_consecutive:
                alerts.append(Alert(
                    time=time, kind="attack", level="strong", side="buy",
                    message=(f"🚀 起漲點攻擊訊號：連續 {self._attack_streak} 筆外盤大單"
                             f"上攻掃貨，價格跳檔至 {price:.2f}"),
                    price=price,
                ))
        # --- 起跌點：內盤大單 + 價格向下跳檔連續發生 ---
        elif is_large and side < 0 and self._last_price > 0 and price < self._last_price:
            self._down_streak += 1
            self._attack_streak = 0
            if self._down_streak >= cfg.attack_consecutive:
                alerts.append(Alert(
                    time=time, kind="attack", level="strong", side="sell",
                    message=(f"⚠ 起跌點殺盤訊號：連續 {self._down_streak} 筆內盤大單"
                             f"下殺，價格跳檔至 {price:.2f}"),
                    price=price,
                ))
        elif (self._last_price > 0 and price != self._last_price):
            # 一般漲跌但非連續大單攻擊 → 中斷計數
            self._attack_streak = 0
            self._down_streak = 0

        # --- 更新滑動平均（放最後，門檻用的是「之前」的均量）---
        self._trade_vols.append(volume)
        self.avg_trade_vol = sum(self._trade_vols) / len(self._trade_vols)
        self._last_price = price
        return alerts


# --------------------------------------------------------------------------- #
#  Engine — 整合三大模塊
# --------------------------------------------------------------------------- #

class MicrostructureEngine:
    """整合 OBI / VPIN / 大單三大偵測器，統一吃 tick / bidask 事件。"""

    def __init__(self, cfg: MicroConfig | None = None,
                 on_alert: Callable[[Alert], None] | None = None,
                 on_point: Callable[[dict], None] | None = None):
        self.cfg = cfg or MicroConfig()
        self._lock = threading.Lock()
        self._on_alert = on_alert
        self._on_point = on_point   # 產生新買賣點時的 callback

        self.ob = OrderBookTracker(self.cfg)
        self.vpin = TickVolumeBucket(self.cfg)
        self.large = LargeOrderMonitor(self.cfg)

        # tick classification 狀態
        self._last_trade_price = 0.0
        self._last_side = 0

        # 即時統計
        self.code = ""
        self.last_price = 0.0
        self.avg_price = 0.0
        self.total_volume = 0.0
        self.tick_count = 0
        self.buy_vol_total = 0.0   # 累積外盤量
        self.sell_vol_total = 0.0  # 累積內盤量
        self._alerts: list[Alert] = []
        self.trade_points: deque[dict] = deque(maxlen=50)  # 彙整後的買/賣點

    # 會轉成「買賣點」的訊號類型（confluence 較高者）
    _POINT_KINDS = {"attack": "強", "momentum": "中", "iceberg": "中"}

    # ---- classification：優先 tick_type，退回 Tick Test ----
    def _classify(self, price: float, tick_type: int) -> int:
        if tick_type == 1:
            side = 1
        elif tick_type == 2:
            side = -1
        else:
            # 李查森校準法（Tick Test）
            if self._last_trade_price == 0.0:
                side = 0
            elif price > self._last_trade_price:
                side = 1
            elif price < self._last_trade_price:
                side = -1
            else:
                side = self._last_side  # 平盤沿用上一筆
        if side != 0:
            self._last_side = side
        return side

    # ---- 事件入口 ----
    def on_tick(self, tick: dict) -> None:
        with self._lock:
            price = _f(tick.get("close"))
            volume = _f(tick.get("volume"))
            tick_type = int(tick.get("tick_type", 0) or 0)
            now = _now_str()
            if price <= 0 or volume <= 0:
                return

            side = self._classify(price, tick_type)
            self._last_trade_price = price

            self.code = tick.get("code", self.code)
            self.last_price = price
            self.avg_price = _f(tick.get("avg_price"), self.avg_price)
            self.total_volume = _f(tick.get("total_volume"), self.total_volume)
            self.tick_count += 1
            if side > 0:
                self.buy_vol_total += volume
            elif side < 0:
                self.sell_vol_total += volume

            new_alerts: list[Alert] = []
            a = self.vpin.add(volume, side, price, now)
            if a:
                new_alerts.append(a)
            new_alerts.extend(self.large.on_tick(price, volume, side, now))
            self._emit(new_alerts)

    def on_bidask(self, ba: dict) -> None:
        with self._lock:
            a = self.ob.update(ba, _now_str())
            self.large.on_bidask(
                ask1_price=_f(ba.get("ask_price", [0])[0] if ba.get("ask_price") else 0),
                ask1_vol=_f(ba.get("ask_volume", [0])[0] if ba.get("ask_volume") else 0),
                bid1_price=_f(ba.get("bid_price", [0])[0] if ba.get("bid_price") else 0),
                bid1_vol=_f(ba.get("bid_volume", [0])[0] if ba.get("bid_volume") else 0),
            )
            if a:
                self._emit([a])

    def _emit(self, alerts: list[Alert]) -> None:
        for a in alerts:
            self._alerts.append(a)
            self._maybe_trade_point(a)
            if self._on_alert:
                try:
                    self._on_alert(a)
                except Exception:
                    pass

    def _maybe_trade_point(self, a: Alert) -> None:
        """把 confluence 較高的訊號彙整成一個離散「買點/賣點」。"""
        if a.kind not in self._POINT_KINDS or a.side not in ("buy", "sell"):
            return
        # 依據簡述（去掉冒號後段細節）
        reason = a.message.split("：", 1)[0]
        # OBI 同向蓄勢時提升信心強度
        strength = self._POINT_KINDS[a.kind]
        if self.ob.setup_side == a.side and strength == "中":
            strength = "強"
        point = {
            "time": a.time, "side": a.side, "price": a.price,
            "strength": strength, "kind": a.kind, "reason": reason,
        }
        # 去重：與上一個買賣點同向、同類、同價則略過（避免洗頻）
        if self.trade_points:
            last = self.trade_points[-1]
            if (last["side"] == point["side"] and last["kind"] == point["kind"]
                    and abs(last["price"] - point["price"]) < 1e-6):
                return
        self.trade_points.append(point)
        if self._on_point:
            try:
                self._on_point(point)
            except Exception:
                pass

    # ---- 快照供 UI 讀取 ----
    def snapshot(self) -> dict:
        with self._lock:
            return {
                "code": self.code,
                "last_price": self.last_price,
                "avg_price": self.avg_price,
                "above_avg": self.last_price >= self.avg_price if self.avg_price else False,
                "total_volume": self.total_volume,
                "tick_count": self.tick_count,
                "buy_vol_total": self.buy_vol_total,
                "sell_vol_total": self.sell_vol_total,
                "inner_outer_ratio": (
                    self.buy_vol_total / self.sell_vol_total
                    if self.sell_vol_total > 0 else 0.0),
                "obi1": self.ob.obi1,
                "obi5": self.ob.obi5,
                "setup_active": self.ob.setup_active,
                "setup_side": self.ob.setup_side,
                "vpin": self.vpin.vpin,
                "buy_push_ratio": self.vpin.cur_buy_ratio,
                "avg_trade_vol": self.large.avg_trade_vol,
                "bid_price": list(self.ob.bid_price),
                "bid_volume": list(self.ob.bid_volume),
                "ask_price": list(self.ob.ask_price),
                "ask_volume": list(self.ob.ask_volume),
                "recent_large": list(self.large.recent_large)[-12:],
                "trade_points": list(self.trade_points)[-15:],
            }

    def set_config(self, cfg: MicroConfig) -> None:
        """套用新參數。就地更新（保留 engine 物件本身與 lock），因此已註冊給
        Shioaji 的 on_tick / on_bidask 綁定方法仍然有效；偵測器與累積統計歸零。"""
        with self._lock:
            self.cfg = cfg
            self.ob = OrderBookTracker(cfg)
            self.vpin = TickVolumeBucket(cfg)
            self.large = LargeOrderMonitor(cfg)
            self._last_trade_price = 0.0
            self._last_side = 0
            self.tick_count = 0
            self.buy_vol_total = 0.0
            self.sell_vol_total = 0.0
            self.trade_points.clear()

    def reset(self) -> None:
        """清空所有狀態（換股票追蹤時呼叫），保留 lock 與 on_alert callback。"""
        with self._lock:
            self.ob = OrderBookTracker(self.cfg)
            self.vpin = TickVolumeBucket(self.cfg)
            self.large = LargeOrderMonitor(self.cfg)
            self._last_trade_price = 0.0
            self._last_side = 0
            self.code = ""
            self.last_price = 0.0
            self.avg_price = 0.0
            self.total_volume = 0.0
            self.tick_count = 0
            self.buy_vol_total = 0.0
            self.sell_vol_total = 0.0
            self._alerts = []
            self.trade_points.clear()
