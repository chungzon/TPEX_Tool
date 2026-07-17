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
from services.mede.detection_service import DetectionService
from services.mede.backtest import BedBacktestParams

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

    # --- 回測（Phase 8）---
    bt_dates = ObservableProperty(None)        # list[str]
    bt_codes = ObservableProperty(None)        # list[str]
    bt_summary = ObservableProperty(None)      # dict：績效卡
    bt_trades = ObservableProperty(None)       # list[dict]：交易明細
    bt_chart = ObservableProperty(None)        # dict：淨值曲線 + 分數-勝率
    bt_patterns = ObservableProperty(None)     # list[dict]：Pattern 比較
    bt_msg = ObservableProperty("")
    bt_running = ObservableProperty(False)

    REFRESH_INTERVAL = 0.4

    def __init__(self, config: ConfigService, shioaji_svc):
        super().__init__()
        self._config = config
        self._sj = shioaji_svc
        self._cfg = MedeConfig.from_dict(config.get(_MEDE_CFG_KEY))
        self._detect = DetectionService(self._cfg)
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

    # ---------------- 回測（Phase 8）----------------
    def refresh_bt_dates(self):
        def _work():
            dates = self._detect.list_dates()
            self.bt_dates = dates
            if dates:
                self.load_bt_codes(dates[0])
            else:
                self.bt_codes = []
                self.bt_msg = "尚無錄製資料（回測需先於盤中錄製）"
        threading.Thread(target=_work, daemon=True).start()

    def load_bt_codes(self, trade_date: str):
        def _work():
            try:
                self.bt_codes = self._detect.list_recorded(trade_date)
            except Exception as exc:
                self.bt_codes = []
                self.bt_msg = f"讀取代碼失敗：{exc}"
        threading.Thread(target=_work, daemon=True).start()

    def run_backtest(self, trade_date: str, code: str, params: dict):
        if not trade_date or not code or "—" in (trade_date, code):
            self.bt_msg = "請先選擇交易日與代碼"
            return

        def _work():
            self.bt_running = True
            self.bt_msg = f"回測中：{code} @ {trade_date} …"
            try:
                bp = BedBacktestParams(
                    direction=-1,
                    min_final_score=float(params.get("min_final_score", 75)),
                    take_profit_ticks=float(params.get("take_profit_ticks", 6)),
                    stop_loss_ticks=float(params.get("stop_loss_ticks", 4)),
                    max_holding_ms=int(params.get("max_holding_ms", 60000)),
                    slippage_ticks=float(params.get("slippage_ticks", 1)))
                res = self._detect.backtest(code, trade_date, bp)
            except Exception as exc:
                self.bt_msg = f"回測失敗：{exc}"
                self.bt_running = False
                return
            self._publish_bt(res)
            self.bt_msg = (f"✓ {code} {trade_date}｜事件 {res.event_count}、"
                           f"成交 {res.total_trades}（{res.data_mode}）")
            self.bt_running = False

        threading.Thread(target=_work, daemon=True).start()

    def _publish_bt(self, res):
        pf = res.profit_factor
        self.bt_summary = {
            "event_count": res.event_count, "tradable": res.tradable,
            "total_trades": res.total_trades, "win_rate": res.win_rate,
            "expectancy": res.expectancy_pct, "profit_factor": pf,
            "total_return": res.total_return_pct, "mdd": res.max_drawdown_pct,
            "avg_win": res.avg_win_pct, "avg_loss": res.avg_loss_pct,
            "avg_mfe": res.avg_mfe_pct, "avg_mae": res.avg_mae_pct,
            "avg_holding_ms": res.avg_holding_ms, "max_consec_losses": res.max_consec_losses,
            "data_mode": res.data_mode,
        }
        self.bt_trades = [t.as_dict() for t in res.trades]
        # 分數-勝率分桶
        buckets = {}
        for t in res.trades:
            b = int(t.final_score // 5 * 5)
            buckets.setdefault(b, []).append(1 if t.net_pnl_pct > 0 else 0)
        score_wr = sorted(
            [{"bucket": b, "win_rate": sum(v) / len(v) * 100, "n": len(v)}
             for b, v in buckets.items()], key=lambda x: x["bucket"])
        self.bt_chart = {
            "equity": list(res.equity_curve),
            "score_wr": score_wr,
        }
        # Pattern 比較
        pat = {}
        for t in res.trades:
            key = t.pattern or "—"
            pat.setdefault(key, []).append(t)
        rows = []
        for key, ts in pat.items():
            rets = [x.net_pnl_pct for x in ts]
            wins = [r for r in rets if r > 0]
            rows.append({"pattern": key, "n": len(ts),
                         "win_rate": len(wins) / len(ts) * 100,
                         "avg_ret": sum(rets) / len(rets),
                         "expectancy": sum(rets) / len(rets)})
        self.bt_patterns = sorted(rows, key=lambda x: -x["n"])

    def shutdown(self):
        self._stop.set()
        try:
            self.stop_silent()
        except Exception:
            pass
