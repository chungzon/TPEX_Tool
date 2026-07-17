"""ViewModel — 「空方發動偵測（BED）」分頁 · 即時觀察（Phase 7）。

單股即時：訂閱 Shioaji Tick+BidAsk → 餵與回測**完全相同**的 MedeEngine →
節流(≈0.4s)推送 UI：狀態卡、主圖序列、五檔、Detector 表、事件表。
與「大單追蹤」共用同一 Shioaji 連線；同一代碼單一消費者（見 BED 盤點限制）。
回測模式於 Phase 8 加入，共用同一 core（MedeEngine/Detector/Fusion/StateMachine）。
"""

from __future__ import annotations

import threading
from collections import deque

from viewmodels.base_viewmodel import BaseViewModel, ObservableProperty
from services.config_service import ConfigService
from services.mede.config import MedeConfig
from services.mede.engine import MedeEngine
from services.mede.enums import StateType
from services.mede.tick_size import tw_tick_size

_MEDE_CFG_KEY = "mede"

# Detector 名稱 → 中文（Detector 表）
DETECTOR_LABELS = {
    "trade_burst": "成交爆量(筆)", "volume_burst": "成交爆量(量)",
    "aggressive_flow": "主動流", "book_imbalance_shift": "委買賣失衡",
    "ofi_shock": "OFI 衝擊", "breakout": "突破", "sweep": "掃單",
    "absorption": "吸收", "queue_collapse": "掛單崩塌",
    "liquidity_vacuum": "流動性真空", "replenishment": "補量",
    "failed_breakout": "假突破", "exhaustion": "衰竭", "momentum_ignition": "動能點火",
    "rally_failure": "拉高失敗", "vwap_break": "跌破VWAP",
    "vwap_rejection": "反彈不過VWAP", "lower_high": "Lower High",
    "structure_break": "跌破微結構低", "directional_efficiency": "下跌有效反彈無效",
}
_MAXPTS = 900   # 主圖保留最近點數（1 秒聚合）


class BedViewModel(BaseViewModel):

    is_tracking = ObservableProperty(False)
    status_data = ObservableProperty(None)     # dict：狀態卡 tiles
    book_data = ObservableProperty(None)       # dict：五檔
    detector_rows = ObservableProperty(None)   # list[dict]
    event_rows = ObservableProperty(None)      # list[dict]
    chart_data = ObservableProperty(None)      # dict：序列 + 事件標記
    status_msg = ObservableProperty("")

    REFRESH_INTERVAL = 0.4

    def __init__(self, config: ConfigService, shioaji_svc):
        super().__init__()
        self._config = config
        self._sj = shioaji_svc
        self._cfg = MedeConfig.from_dict(config.get(_MEDE_CFG_KEY))
        self._code = ""
        self._eng: MedeEngine | None = None
        self._lock = threading.Lock()
        self._dirty = False
        self._stop = threading.Event()
        self._refresh: threading.Thread | None = None
        # 主圖 1 秒聚合序列
        self._series: deque = deque(maxlen=_MAXPTS)   # (sec, price, vwap, open, high)
        self._last_sec = -1
        self._events: list = []                       # (sec, price, type, direction)
        self._book = None
        self.last_error = ""

    # ---------------- 控制 ----------------
    def start(self, code: str):
        code = (code or "").strip()

        def _work():
            if not code:
                self.status_msg = "請輸入股票代碼"
                return
            if not self._sj.is_logged_in:
                self.status_msg = "尚未連線永豐（即時行情需正式環境登入）"
                return
            self.stop_silent()
            self._code = code
            self._eng = MedeEngine(code, self._cfg, tw_tick_size)
            self._series.clear(); self._events.clear(); self._last_sec = -1
            ok = self._sj.subscribe_quote(code, on_tick=self.on_tick,
                                          on_bidask=self.on_bidask)
            if not ok:
                self.status_msg = f"訂閱失敗：{code}（代碼錯誤或非交易時段）"
                return
            self.is_tracking = True
            self.status_msg = f"✓ 即時追蹤：{code}"
            self._start_refresh()

        threading.Thread(target=_work, daemon=True).start()

    def stop(self):
        def _work():
            self.stop_silent()
            self.is_tracking = False
            self.status_msg = "已停止追蹤"
        threading.Thread(target=_work, daemon=True).start()

    def stop_silent(self):
        self._stop.set()
        if self._refresh:
            self._refresh.join(timeout=2)
            self._refresh = None
        if self._code:
            try:
                self._sj.unsubscribe_quote(self._code)
            except Exception:
                pass

    # ---------------- 行情 callback（工作緒）----------------
    def on_bidask(self, ba: dict):
        if self._eng is None:
            return
        with self._lock:
            self._eng.on_bidask(ba)
            self._book = {"bid_price": list(ba.get("bid_price") or []),
                          "bid_volume": list(ba.get("bid_volume") or []),
                          "ask_price": list(ba.get("ask_price") or []),
                          "ask_volume": list(ba.get("ask_volume") or [])}
            self._dirty = True

    def on_tick(self, tick: dict):
        if self._eng is None:
            return
        with self._lock:
            snap, fusion, state, ev = self._eng.on_tick(tick)
            sec = snap.t_ns // 1_000_000_000
            row = (sec, snap.last_price, snap.vwap, snap.open_price,
                   snap.recent_high, fusion.final_bear_score)
            if sec != self._last_sec:
                self._series.append(row)
                self._last_sec = sec
            elif self._series:
                self._series[-1] = row     # 同秒覆蓋（1 秒聚合，取最後）
            if ev is not None:
                self._events.append((sec, ev.trigger_price, ev.event_type, ev.direction))
            self._dirty = True

    # ---------------- 節流刷新 ----------------
    def _start_refresh(self):
        self._stop.clear()
        self._refresh = threading.Thread(target=self._loop, daemon=True)
        self._refresh.start()

    def _loop(self):
        while not self._stop.is_set():
            if self._dirty:
                self._publish()
                self._dirty = False
            self._stop.wait(self.REFRESH_INTERVAL)

    def _publish(self):
        with self._lock:
            eng = self._eng
            snap = eng.last_snap if eng else None
            fusion = eng.last_fusion if eng else None
            results = dict(eng.last_results) if eng else {}
            series = list(self._series)
            events = list(self._events)
            book = self._book
            state = eng.state.value if eng else "IDLE"
        if snap is None or fusion is None:
            return
        ts = snap.tick_size or 1.0
        dist_vwap = round((snap.last_price - snap.vwap) / ts, 1) if ts else 0.0
        chg = ((snap.last_price - snap.open_price) / snap.open_price * 100
               if snap.open_price > 0 else 0.0)
        pw = snap.time_windows.get("1s", {})
        self.status_data = {
            "price": snap.last_price, "chg_pct": round(chg, 2), "vwap": round(snap.vwap, 2),
            "open": snap.open_price, "high": snap.recent_high, "low": snap.recent_low,
            "dist_vwap": dist_vwap, "bear_score": fusion.final_bear_score,
            "bull_flow": round(pw.get("buy_vol", 0.0), 0),
            "bear_flow": round(pw.get("sell_vol", 0.0), 0),
            "spread": snap.spread_ticks, "state": state,
            "structure": fusion.structure_score, "trade": fusion.trade_score,
            "orderbook": fusion.orderbook_score, "veto": fusion.veto_score,
        }
        self.book_data = book
        self.detector_rows = [
            {"name": DETECTOR_LABELS.get(n, n), "key": n, "dir": r.direction,
             "score": round(r.score, 0), "trig": r.is_triggered,
             "reason": (r.reasons[0] if r.reasons else "")}
            for n, r in results.items()]
        self.event_rows = [
            {"sec": s, "price": pr, "type": tp, "dir": d}
            for (s, pr, tp, d) in events[-50:]]
        # 主圖序列
        if series:
            base = series[0][0]
            self.chart_data = {
                "t": [s - base for (s, *_r) in series],
                "price": [r[1] for r in series],
                "vwap": [r[2] for r in series],
                "open": series[-1][3], "high": series[-1][4],
                "bear_series": [r[5] for r in series],
                "swing_high": snap.swing_high, "swing_low": snap.swing_low,
                "events": [(s - base, pr, d) for (s, pr, tp, d) in events],
                "bear_score": fusion.final_bear_score,
            }

    def shutdown(self):
        self._stop.set()
        try:
            self.stop_silent()
        except Exception:
            pass
