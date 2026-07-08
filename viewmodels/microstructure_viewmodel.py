"""ViewModel for the 大單追蹤 (market microstructure) tab.

即時追蹤單一股票的逐筆成交與五檔委託，透過 MicrostructureEngine 計算
OBI / VPIN / 大單 / 冰山 訊號。行情 callback 在 Shioaji socket thread 高頻觸發，
故本 VM 只在 engine 內累積狀態，另開一條 ~0.4s 的刷新執行緒把 snapshot 推給 UI，
強訊號則即時 append 到 alert_log。
"""

from __future__ import annotations

import dataclasses
import threading
import time as _time

from viewmodels.base_viewmodel import BaseViewModel, ObservableProperty
from services.shioaji_service import ShioajiService
from services.config_service import ConfigService
from services.microstructure_service import MicrostructureEngine, MicroConfig, Alert
from services.microstructure_backtest import (
    MicrostructureBacktester, BacktestParams, BacktestResult)

_CONFIG_KEY = "micro_params"
_VALID_FIELDS = {f.name for f in dataclasses.fields(MicroConfig)}
_BT_CONFIG_KEY = "micro_backtest_params"
_BT_FIELDS = {f.name for f in dataclasses.fields(BacktestParams)}


class MicrostructureViewModel(BaseViewModel):

    # Connection
    conn_status = ObservableProperty("")
    is_connected = ObservableProperty(False)
    is_connecting = ObservableProperty(False)

    # Tracking
    is_tracking = ObservableProperty(False)
    tracked_code = ObservableProperty("")

    # Live state snapshot (dict) + alerts
    state_data = ObservableProperty(None)   # dict | None
    alert_log = ObservableProperty("")
    error = ObservableProperty("")
    params_status = ObservableProperty("")  # 參數套用結果訊息
    export_status = ObservableProperty("")  # CSV 匯出結果訊息

    # Backtest
    is_backtesting = ObservableProperty(False)
    backtest_status = ObservableProperty("")
    backtest_result = ObservableProperty(None)   # dict | None（summary + trades + report）

    REFRESH_INTERVAL = 0.4  # 秒；UI 刷新頻率

    def __init__(self, config: ConfigService, shioaji_svc: ShioajiService | None = None):
        super().__init__()
        self._config = config
        # 與「下單」分頁共用同一個 Shioaji 連線；若未提供則自建
        self._sj = shioaji_svc or ShioajiService()
        cfg = self._load_config()
        self._engine = MicrostructureEngine(
            cfg, on_alert=self._on_alert, on_point=self._on_point)
        self._refresh_thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._alert_lock = threading.Lock()
        self._pending_alerts: list[str] = []
        # 完整買賣點歷史（供 CSV 匯出，不受畫面 deque maxlen 限制）
        self._point_lock = threading.Lock()
        self._point_history: list[dict] = []
        self._last_bt: BacktestResult | None = None  # 最近一次回測結果（供匯出）

    # ================================================================ Parameters

    def _load_config(self) -> MicroConfig:
        """從 config.json 讀取已儲存參數，套在預設值上；未存則用預設。"""
        saved = self._config.get(_CONFIG_KEY) or {}
        clean = {k: v for k, v in saved.items() if k in _VALID_FIELDS}
        try:
            return MicroConfig(**clean)
        except (TypeError, ValueError):
            return MicroConfig()

    def default_params(self) -> dict:
        """引擎目前生效中的參數（供 UI 預填）。"""
        return dataclasses.asdict(self._engine.cfg)

    def apply_params(self, params: dict):
        """套用 UI 傳入的參數：驗證 → 更新引擎（即時生效）→ 存檔。"""
        clean = {k: v for k, v in params.items() if k in _VALID_FIELDS}
        try:
            cfg = MicroConfig(**clean)
        except (TypeError, ValueError) as e:
            self.params_status = f"參數錯誤：{e}"
            return
        # 基本合理性檢查
        if cfg.bucket_size <= 0 or cfg.obi_sustain_ticks < 1 or cfg.attack_consecutive < 1:
            self.params_status = "參數需為正數（桶量>0、連續筆數≥1）"
            return
        self._engine.set_config(cfg)
        self._config.set(_CONFIG_KEY, dataclasses.asdict(cfg))
        self.params_status = "✓ 已套用並儲存參數（即時生效，統計已歸零）"
        self._append_alert("── 已套用新參數 ──")

    def reset_params(self):
        """回復預設值並套用。"""
        cfg = MicroConfig()
        self._engine.set_config(cfg)
        self._config.set(_CONFIG_KEY, dataclasses.asdict(cfg))
        self.params_status = "✓ 已回復預設參數"

    # ================================================================ Backtest

    def default_backtest_params(self) -> dict:
        """讀 config 內存過的回測參數（套在預設上）供 UI 預填。"""
        saved = self._config.get(_BT_CONFIG_KEY) or {}
        clean = {k: v for k, v in saved.items() if k in _BT_FIELDS}
        try:
            return dataclasses.asdict(BacktestParams(**clean))
        except (TypeError, ValueError):
            return dataclasses.asdict(BacktestParams())

    def run_backtest(self, stock_code: str, date: str, params: dict):
        """抓永豐歷史逐筆 → 用目前 MicroConfig 跑回測（背景執行緒）。"""
        if self.is_backtesting:
            return
        stock_code = stock_code.strip()
        if not stock_code:
            self.backtest_status = "請輸入股票代碼"
            return
        if not date.strip():
            self.backtest_status = "請輸入回測日期 (YYYY-MM-DD)"
            return
        if not self._sj.is_logged_in:
            self.backtest_status = "尚未連線永豐，請先按『連線』"
            return

        clean = {k: v for k, v in params.items() if k in _BT_FIELDS}
        try:
            bt_params = BacktestParams(**clean)
        except (TypeError, ValueError) as e:
            self.backtest_status = f"回測參數錯誤：{e}"
            return
        self._config.set(_BT_CONFIG_KEY, dataclasses.asdict(bt_params))

        self.is_backtesting = True
        self.backtest_status = f"下載 {stock_code} {date} 逐筆資料中..."
        self.backtest_result = None

        def _work():
            try:
                ticks = self._sj.get_historical_ticks(stock_code, date)
                if not ticks:
                    self.backtest_status = "查無逐筆資料（代碼/日期錯誤、非交易日或無權限）"
                    return
                self.backtest_status = f"重放 {len(ticks):,} 筆 tick，回測中..."
                bt = MicrostructureBacktester(self._engine.cfg, bt_params)
                res = bt.run(ticks, code=stock_code, date=date)
                self._last_bt = res
                self.backtest_result = {
                    "summary": res.summary_dict(),
                    "trades": [t.as_dict() for t in res.trades],
                    "report": bt.report_text(res),
                    "equity_curve": res.equity_curve,
                }
                if res.error:
                    self.backtest_status = f"回測失敗：{res.error}"
                else:
                    self.backtest_status = (
                        f"完成：{res.total_trades} 筆交易，勝率 {res.win_rate:.1f}%，"
                        f"總報酬 {res.total_return_pct:+.2f}%")
            except Exception as e:
                self.backtest_status = f"回測發生錯誤：{e}"
            finally:
                self.is_backtesting = False

        threading.Thread(target=_work, daemon=True).start()

    def export_backtest_csv(self, path: str):
        """把最近一次回測的每筆交易明細匯出成 CSV。"""
        import csv
        res = self._last_bt
        if not res or not res.trades:
            self.export_status = "目前沒有回測交易可匯出"
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["方向", "進場時間", "進場價", "出場時間", "出場價",
                            "報酬率%", "出場原因", "持有筆數"])
                for t in res.trades:
                    w.writerow([
                        "做多" if t.direction == "long" else "做空",
                        t.entry_time, t.entry_price, t.exit_time, t.exit_price,
                        t.ret_pct, t.exit_reason, t.hold_ticks])
            self.export_status = f"✓ 已匯出 {len(res.trades)} 筆交易 → {path}"
        except Exception as e:
            self.export_status = f"匯出失敗：{e}"

    # ================================================================ Connection

    @property
    def shioaji(self) -> ShioajiService:
        return self._sj

    def refresh_conn_state(self):
        """由 UI 進入分頁時呼叫，同步共用連線的登入狀態。"""
        self.is_connected = self._sj.is_logged_in
        if self._sj.is_logged_in:
            self.conn_status = "已連線（永豐行情）"

    def connect(self, simulation: bool = False):
        """使用 config 內已存的金鑰連線。行情串流建議用正式環境。"""
        if self.is_connecting:
            return
        if self._sj.is_logged_in:
            self.is_connected = True
            self.conn_status = "已連線（永豐行情）"
            return
        api_key = self._config.get("shioaji_api_key") or ""
        secret_key = self._config.get("shioaji_secret_key") or ""
        person_id = self._config.get("shioaji_person_id") or ""
        if not api_key or not secret_key:
            self.conn_status = "請先到『下單』或『系統設定』分頁儲存 API 金鑰"
            return

        self.is_connecting = True
        self.conn_status = "連線中..."

        def _status(msg: str):
            self.conn_status = msg

        def _work():
            try:
                ok = self._sj.login(
                    api_key, secret_key, person_id=person_id, ca_passwd="",
                    simulation=simulation, on_status=_status)
                self.is_connected = ok
                if ok and simulation:
                    self.conn_status = "已連線（測試環境；注意：模擬環境不供即時行情）"
            except Exception as e:
                self.conn_status = f"連線失敗：{e}"
                self.is_connected = False
            finally:
                self.is_connecting = False

        threading.Thread(target=_work, daemon=True).start()

    # ================================================================ Tracking

    def start_tracking(self, stock_code: str):
        stock_code = stock_code.strip()
        self.error = ""
        if not stock_code:
            self.error = "請輸入股票代碼"
            return
        if not self._sj.is_logged_in:
            self.error = "尚未連線永豐行情，請先按『連線』"
            return
        if self.is_tracking:
            self.stop_tracking()

        self._engine.reset()
        with self._point_lock:
            self._point_history.clear()
        self.state_data = None
        ok = self._sj.subscribe_quote(
            stock_code, on_tick=self._engine.on_tick,
            on_bidask=self._engine.on_bidask)
        if not ok:
            self.error = f"訂閱 {stock_code} 行情失敗（代碼錯誤或非交易時段）"
            return

        self.tracked_code = stock_code
        self.is_tracking = True
        self._append_alert(f"── 開始追蹤 {stock_code} ──")

        # 啟動 UI 刷新執行緒
        self._stop.clear()
        self._refresh_thread = threading.Thread(target=self._refresh_loop, daemon=True)
        self._refresh_thread.start()

    def stop_tracking(self):
        if not self.is_tracking:
            return
        self._stop.set()
        code = self.tracked_code
        try:
            self._sj.unsubscribe_quote(code)
        except Exception:
            pass
        self.is_tracking = False
        self._append_alert(f"── 停止追蹤 {code} ──")

    # ================================================================ Refresh loop

    def _refresh_loop(self):
        """固定頻率把 engine snapshot 推給 UI，並沖出待處理 alert。"""
        while not self._stop.is_set():
            try:
                self.state_data = self._engine.snapshot()
                self._flush_alerts()
            except Exception:
                pass
            self._stop.wait(self.REFRESH_INTERVAL)

    # ================================================================ Alerts

    def _on_alert(self, alert: Alert):
        """engine callback（socket thread）→ 暫存，交給刷新迴圈統一 append。"""
        icon = {"strong": "🔴", "warn": "🟡", "info": "⚪"}.get(alert.level, "•")
        t = (alert.time or "")[-12:]  # 只取時分秒
        self._append_alert(f"{icon} [{t}] {alert.message}")

    def _on_point(self, point: dict):
        """engine callback：產生新買/賣點 → 存入歷史 + 寫一行到訊號紀錄。"""
        with self._point_lock:
            self._point_history.append(dict(point))
        side_txt = "買點" if point.get("side") == "buy" else "賣點"
        kind_txt = {"attack": "起漲/起跌", "momentum": "動能點火",
                    "iceberg": "冰山"}.get(point.get("kind", ""), point.get("kind", ""))
        self._append_alert(
            f"★ [{point.get('time','')}] {side_txt} @{point.get('price',0):.2f} "
            f"{point.get('strength','')}｜{kind_txt}：{point.get('reason','')}")

    def export_csv(self, path: str):
        """把本次追蹤累積的所有買賣點匯出成 CSV（Excel 可直接開）。"""
        import csv
        with self._point_lock:
            rows = list(self._point_history)
        if not rows:
            self.export_status = "目前沒有買賣點可匯出"
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["時間", "方向", "價格", "強度", "類型", "依據"])
                kmap = {"attack": "起漲/起跌", "momentum": "動能點火", "iceberg": "冰山"}
                for p in rows:
                    w.writerow([
                        p.get("time", ""),
                        "買點" if p.get("side") == "buy" else "賣點",
                        p.get("price", 0),
                        p.get("strength", ""),
                        kmap.get(p.get("kind", ""), p.get("kind", "")),
                        p.get("reason", ""),
                    ])
            self.export_status = f"✓ 已匯出 {len(rows)} 筆買賣點 → {path}"
        except Exception as e:
            self.export_status = f"匯出失敗：{e}"

    def _append_alert(self, line: str):
        with self._alert_lock:
            self._pending_alerts.append(line)

    def _flush_alerts(self):
        with self._alert_lock:
            if not self._pending_alerts:
                return
            new = "\n".join(self._pending_alerts) + "\n"
            self._pending_alerts.clear()
        self.alert_log = (self.alert_log or "") + new

    def clear_alerts(self):
        with self._alert_lock:
            self._pending_alerts.clear()
        self.alert_log = ""

    # ================================================================ Shutdown

    def shutdown(self):
        self._stop.set()
        try:
            if self.is_tracking and self._sj.is_logged_in:
                self._sj.unsubscribe_quote(self.tracked_code)
        except Exception:
            pass
