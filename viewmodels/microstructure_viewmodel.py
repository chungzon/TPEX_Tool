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
from services.adaptive_params_service import AdaptiveParameterManager

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
    computed_params = ObservableProperty(None)  # 自動推導後的參數 dict（供 UI 回填）

    # Backtest
    is_backtesting = ObservableProperty(False)
    backtest_status = ObservableProperty("")
    backtest_result = ObservableProperty(None)   # dict | None（summary + trades + report）

    # 自動回測（反手輪詢 SAR）：只輸入代碼/日期，其餘全用監控參數，多空由訊號強弱自動決定
    is_auto_backtesting = ObservableProperty(False)
    auto_backtest_status = ObservableProperty("")
    auto_backtest_result = ObservableProperty(None)

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
        # 逐筆歷史（供圖表：價格、成交量、買賣超）與結構化訊號紀錄（供 CSV）
        self._tick_lock = threading.Lock()
        self._tick_history: list[dict] = []   # {time, price, vol, side(+1/-1/0)}
        self._alert_records: list[dict] = []  # {time, kind, level, side, price, message}
        self._last_chart_price = 0.0
        self._last_bt: BacktestResult | None = None  # 最近一次回測結果（供匯出）
        self._last_auto_bt: BacktestResult | None = None  # 最近一次自動回測結果
        # 回測用逐筆（供圖表）：(code, date, ticks)
        self._last_bt_ctx: tuple | None = None
        self._last_auto_bt_ctx: tuple | None = None

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

    # ---------------------------------------------------------- Adaptive params

    def auto_params(self, stock_code: str):
        """依股價 / 量能自動推導並即時套用參數（背景執行緒，含網路抓取）。

        參數隨股價（市場深度）與日均量（流動性）縮放；不寫入 config.json，因為
        這組值是「該檔股票專屬」的，不宜污染全域預設。UI 端以 computed_params 回填。
        """
        code = stock_code.strip()
        if not code:
            self.params_status = "請先輸入股票代碼"
            return
        self.params_status = f"計算 {code} 的動態參數中..."

        def _work():
            try:
                price, adv_lots, tick_sizes, note = self._gather_stock_profile(code)
                if price <= 0:
                    self.params_status = (
                        "查無股價資料：請先下載日線／補資料，或連線永豐後再試")
                    return
                mgr = AdaptiveParameterManager(price, adv_lots, tick_sizes)
                cfg, basis = mgr.build()
                self._engine.set_config(cfg)
                self.computed_params = dataclasses.asdict(cfg)
                self.params_status = "✓ 已依股價自動設定：" + mgr.describe(basis)
                self._append_alert(
                    f"── 依股價自動設定參數（{mgr.describe(basis)}）──")
            except Exception as e:
                self.params_status = f"自動設定失敗：{e}"

        threading.Thread(target=_work, daemon=True).start()

    def _gather_stock_profile(
        self, code: str,
    ) -> tuple[float, float, list[float], str]:
        """收集推導參數所需資料 → (股價, 日均量張數, 歷史每筆量, 來源說明)。

        股價：優先 DB 最新收盤（免連線）；量能與每筆量分布：需連線永豐，抓最近一個
        「完整交易日」的逐筆（單位為張，與即時引擎一致）。DB 的 total_volume 因來源
        不同（爬蟲=張且雙邊重複計、補資料=股）單位不一致，故量能不採 DB。
        """
        price = 0.0
        adv_lots = 0.0
        tick_sizes: list[float] = []
        note = ""

        # 1) 股價：DB 最新收盤（NVARCHAR → float）
        try:
            from services.db_service import DbService
            rows = DbService().get_recent_volume(code, 5)
            if rows:
                price = self._to_float(rows[0].get("close_price"))
        except Exception:
            pass

        # 2) 量能 + 每筆量分布：永豐最近完整交易日逐筆
        if self._sj.is_logged_in:
            for d in self._recent_trading_dates(5):
                try:
                    ticks = self._sj.get_historical_ticks(code, d)
                except Exception:
                    ticks = []
                if ticks:
                    tick_sizes = [self._to_float(t.get("volume"))
                                  for t in ticks if self._to_float(t.get("volume")) > 0]
                    adv_lots = float(sum(tick_sizes))
                    last_close = self._to_float(ticks[-1].get("close"))
                    if last_close > 0:
                        price = last_close  # 逐筆收盤較 DB 即時
                    note = f"{d} 逐筆"
                    break
        return price, adv_lots, tick_sizes, note

    @staticmethod
    def _recent_trading_dates(n: int) -> list[str]:
        """最近 n 個工作日（跳過六日），由前一個交易日往回，確保是完整交易日。"""
        from datetime import datetime, timedelta
        out: list[str] = []
        d = datetime.now() - timedelta(days=1)
        while len(out) < n:
            if d.weekday() < 5:
                out.append(d.strftime("%Y-%m-%d"))
            d -= timedelta(days=1)
        return out

    @staticmethod
    def _to_float(x) -> float:
        try:
            return float(str(x).replace(",", "").strip())
        except (TypeError, ValueError):
            return 0.0

    # ================================================================ Backtest

    def default_backtest_params(self) -> dict:
        """讀 config 內存過的回測參數（套在預設上）供 UI 預填。"""
        saved = self._config.get(_BT_CONFIG_KEY) or {}
        clean = {k: v for k, v in saved.items() if k in _BT_FIELDS}
        try:
            return dataclasses.asdict(BacktestParams(**clean))
        except (TypeError, ValueError):
            return dataclasses.asdict(BacktestParams())

    def save_backtest_params(self, params: dict) -> BacktestParams | None:
        """驗證回測參數並存入 config.json，回傳 BacktestParams（失敗回 None）。

        獨立於「執行回測」：即使尚未連線或缺代碼/日期，使用者設定（做多/做空、
        需大單、需突破、K 值、連續桶數…）也會被保存，下次開啟自動預填。
        """
        clean = {k: v for k, v in params.items() if k in _BT_FIELDS}
        try:
            bt_params = BacktestParams(**clean)
        except (TypeError, ValueError) as e:
            self.backtest_status = f"回測參數錯誤：{e}"
            return None
        self._config.set(_BT_CONFIG_KEY, dataclasses.asdict(bt_params))
        return bt_params

    def run_backtest(self, stock_code: str, date: str, params: dict):
        """抓永豐歷史逐筆 → 用目前 MicroConfig 跑回測（背景執行緒）。"""
        if self.is_backtesting:
            return
        # 先存設定（即使後面因未連線/缺欄位而中止，選項也已持久化）
        bt_params = self.save_backtest_params(params)
        if bt_params is None:
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
                daily_closes = (self._fetch_daily_closes(stock_code, date)
                                if bt_params.daily_trend_filter else [])
                bt = MicrostructureBacktester(
                    self._engine.cfg, bt_params, daily_closes=daily_closes)
                res = bt.run(ticks, code=stock_code, date=date)
                self._last_bt = res
                self._last_bt_ctx = (stock_code, date, ticks)
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

    def _fetch_daily_closes(self, code: str, date: str) -> list[float]:
        """抓回測日『之前』約 60 個日曆日的日收盤（最舊在前），供日線趨勢濾網。

        嚴格排除回測日當天，避免未來函數。DB close_price 為 NVARCHAR，需解析。
        """
        from datetime import datetime, timedelta
        try:
            end = datetime.strptime(date.strip(), "%Y-%m-%d")
        except ValueError:
            return []
        start = (end - timedelta(days=90)).strftime("%Y-%m-%d")
        prev = (end - timedelta(days=1)).strftime("%Y-%m-%d")
        try:
            from services.db_service import DbService
            rows = DbService().get_stock_prices(code, start, prev)
        except Exception:
            return []
        closes = [self._to_float(r.get("close_price")) for r in rows]
        return [c for c in closes if c > 0]

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

    # ============================================================ Auto backtest

    def run_auto_backtest(self, stock_code: str, date: str):
        """全自動回測：只需代碼/日期，其餘全用『監控』當前的偵測參數(MicroConfig)。

        策略固定為反手輪詢(SAR)：系統依訊號強弱自行決定做多/做空——
        賣方轉強→沖賣、買方轉強→補回反手做多，如此輪詢。無需手動勾多空。
        """
        if self.is_auto_backtesting:
            return
        stock_code = stock_code.strip()
        if not stock_code:
            self.auto_backtest_status = "請輸入股票代碼"
            return
        if not date.strip():
            self.auto_backtest_status = "請輸入回測日期 (YYYY-MM-DD)"
            return
        if not self._sj.is_logged_in:
            self.auto_backtest_status = "尚未連線永豐，請先按『連線』"
            return

        # 反手輪詢 + 多空全開，方向交由訊號強弱自動判斷；其餘沿用預設交易成本
        bt_params = BacktestParams(
            strategy="sar_flip", allow_long=True, allow_short=True)

        self.is_auto_backtesting = True
        self.auto_backtest_status = f"下載 {stock_code} {date} 逐筆資料中..."
        self.auto_backtest_result = None

        def _work():
            try:
                ticks = self._sj.get_historical_ticks(stock_code, date)
                if not ticks:
                    self.auto_backtest_status = "查無逐筆資料（代碼/日期錯誤、非交易日或無權限）"
                    return
                self.auto_backtest_status = f"重放 {len(ticks):,} 筆 tick，自動回測中..."
                # 直接用監控引擎當前的 MicroConfig（與即時偵測完全一致）
                bt = MicrostructureBacktester(self._engine.cfg, bt_params)
                res = bt.run(ticks, code=stock_code, date=date)
                self._last_auto_bt = res
                self._last_auto_bt_ctx = (stock_code, date, ticks)
                self.auto_backtest_result = {
                    "summary": res.summary_dict(),
                    "trades": [t.as_dict() for t in res.trades],
                    "events": list(res.sar_events),
                    "report": bt.report_text(res),
                    "equity_curve": res.equity_curve,
                }
                if res.error:
                    self.auto_backtest_status = f"回測失敗：{res.error}"
                else:
                    self.auto_backtest_status = (
                        f"完成：{res.total_trades} 筆交易，勝率 {res.win_rate:.1f}%，"
                        f"總報酬 {res.total_return_pct:+.2f}%")
            except Exception as e:
                self.auto_backtest_status = f"回測發生錯誤：{e}"
            finally:
                self.is_auto_backtesting = False

        threading.Thread(target=_work, daemon=True).start()

    def export_auto_backtest_csv(self, path: str):
        """把最近一次『自動回測』的每筆交易明細匯出成 CSV。"""
        import csv
        res = self._last_auto_bt
        if not res or not res.trades:
            self.export_status = "目前沒有自動回測交易可匯出"
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
            self.export_status = f"✓ 已匯出 {len(res.trades)} 筆自動回測交易 → {path}"
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

    def start_tracking(self, stock_code: str, auto: bool = False):
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

        # 追蹤前依股價／量能自動調參（背景抓取，不阻塞；set_config 為最終權威）
        if auto:
            self.auto_params(stock_code)

        self._engine.reset()
        # 依已存的回測濾網設定，套用同一套趨勢濾網到即時買賣點
        self._apply_live_trend_filter(stock_code)
        with self._point_lock:
            self._point_history.clear()
        with self._tick_lock:
            self._tick_history.clear()
            self._alert_records.clear()
        self._last_chart_price = 0.0
        self.state_data = None
        ok = self._sj.subscribe_quote(
            stock_code, on_tick=self._on_tick,
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

    def _apply_live_trend_filter(self, code: str):
        """讀回測卡的濾網設定，套用到即時引擎（買賣點過濾）。兩層皆關則不濾。"""
        from datetime import datetime
        from services.trend_filter_service import daily_trend_bias, IntradayTrendFilter
        bp = self.default_backtest_params()
        use_daily = bool(bp.get("daily_trend_filter"))
        intraday_mode = bp.get("intraday_filter", "off")
        if not use_daily and intraday_mode not in ("ma", "squeeze"):
            self._engine.set_trend_filter(True, True, None)  # 不濾
            return

        daily_long, daily_short, note = True, True, ""
        if use_daily:
            today = datetime.now().strftime("%Y-%m-%d")
            closes = self._fetch_daily_closes(code, today)
            daily_long, daily_short, note = daily_trend_bias(
                closes, int(bp.get("daily_ma_period", 20)))

        itf = None
        if intraday_mode in ("ma", "squeeze"):
            itf = IntradayTrendFilter(
                mode=intraday_mode, bar_seconds=int(bp.get("bar_seconds", 300)),
                ma_period=int(bp.get("intraday_ma_period", 10)),
                bb_period=int(bp.get("bb_period", 20)), bb_k=float(bp.get("bb_k", 2.0)),
                squeeze_factor=float(bp.get("squeeze_factor", 0.6)),
                squeeze_lookback=int(bp.get("squeeze_lookback", 20)))

        self._engine.set_trend_filter(daily_long, daily_short, itf, daily_note=note)
        parts = []
        if use_daily:
            parts.append(note or "日線濾網")
        if itf is not None:
            parts.append(f"分線閘門={intraday_mode}")
        self._append_alert("── 趨勢濾網啟用：" + "；".join(parts) + " ──")

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

    def _on_tick(self, tick: dict):
        """行情 tick：先存進逐筆歷史（供圖表），再餵給引擎。socket thread 高頻呼叫。"""
        try:
            price = float(tick.get("close") or 0)
            vol = float(tick.get("volume") or 0)
            tt = int(tick.get("tick_type", 0) or 0)
            if price > 0 and vol > 0:
                if tt == 1:
                    side = 1                       # 外盤（買方）
                elif tt == 2:
                    side = -1                      # 內盤（賣方）
                elif self._last_chart_price and price > self._last_chart_price:
                    side = 1
                elif self._last_chart_price and price < self._last_chart_price:
                    side = -1
                else:
                    side = 0
                self._last_chart_price = price
                with self._tick_lock:
                    self._tick_history.append({
                        "time": _time.strftime("%H:%M:%S"),
                        "price": price, "vol": vol, "side": side})
        except Exception:
            pass
        self._engine.on_tick(tick)

    def _on_alert(self, alert: Alert):
        """engine callback（socket thread）→ 暫存，交給刷新迴圈統一 append。"""
        icon = {"strong": "🔴", "warn": "🟡", "info": "⚪"}.get(alert.level, "•")
        t = (alert.time or "")[-12:]  # 只取時分秒
        self._append_alert(f"{icon} [{t}] {alert.message}")
        with self._tick_lock:
            self._alert_records.append({
                "time": alert.time, "kind": alert.kind, "level": alert.level,
                "side": alert.side, "price": alert.price, "message": alert.message,
                "_x": len(self._tick_history)})   # 對齊圖表 x 軸

    def _on_point(self, point: dict):
        """engine callback：產生新買/賣點 → 存入歷史 + 寫一行到訊號紀錄。"""
        rec = dict(point)
        rec["_x"] = len(self._tick_history)   # 對齊圖表 x 軸（目前逐筆索引）
        with self._point_lock:
            self._point_history.append(rec)
        side_txt = "買點" if point.get("side") == "buy" else "賣點"
        kind_txt = {"attack": "起漲/起跌", "momentum": "動能點火",
                    "iceberg": "冰山"}.get(point.get("kind", ""), point.get("kind", ""))
        if point.get("filtered"):
            fr = point.get("filter_reason", "") or "逆勢"
            self._append_alert(
                f"☆ [{point.get('time','')}] {side_txt}（濾網擋下·未亮燈：{fr}） "
                f"@{point.get('price',0):.2f} {point.get('strength','')}｜"
                f"{kind_txt}：{point.get('reason','')}")
        else:
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
            n_filtered = 0
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["時間", "方向", "價格", "強度", "類型", "依據", "濾網"])
                kmap = {"attack": "起漲/起跌", "momentum": "動能點火", "iceberg": "冰山"}
                for p in rows:
                    if p.get("filtered"):
                        n_filtered += 1
                        fr = p.get("filter_reason", "")
                        gate = f"被濾網擋下（{fr}）" if fr else "被濾網擋下"
                    else:
                        gate = "通過"
                    w.writerow([
                        p.get("time", ""),
                        "買點" if p.get("side") == "buy" else "賣點",
                        p.get("price", 0),
                        p.get("strength", ""),
                        kmap.get(p.get("kind", ""), p.get("kind", "")),
                        p.get("reason", ""),
                        gate,
                    ])
            tail = f"（含 {n_filtered} 筆被濾網擋下）" if n_filtered else ""
            self.export_status = f"✓ 已匯出 {len(rows)} 筆買賣點{tail} → {path}"
        except Exception as e:
            self.export_status = f"匯出失敗：{e}"

    def export_alerts_csv(self, path: str):
        """把本次追蹤的『訊號紀錄』（所有偵測到的訊號）匯出成 CSV。"""
        import csv
        with self._tick_lock:
            rows = list(self._alert_records)
        if not rows:
            self.export_status = "目前沒有訊號紀錄可匯出"
            return
        kmap = {"setup": "OBI蓄勢", "momentum": "動能點火", "attack": "起漲/起跌點",
                "large": "大單", "iceberg": "冰山"}
        lmap = {"strong": "強", "warn": "中", "info": "弱"}
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["時間", "類型", "強度", "方向", "價格", "訊息"])
                for a in rows:
                    side = a.get("side", "")
                    w.writerow([
                        a.get("time", ""),
                        kmap.get(a.get("kind", ""), a.get("kind", "")),
                        lmap.get(a.get("level", ""), a.get("level", "")),
                        "買" if side == "buy" else "賣" if side == "sell" else "",
                        a.get("price", 0),
                        a.get("message", ""),
                    ])
            self.export_status = f"✓ 已匯出 {len(rows)} 筆訊號紀錄 → {path}"
        except Exception as e:
            self.export_status = f"匯出失敗：{e}"

    def chart_data(self) -> dict:
        """回傳供圖表用的資料：逐筆(價格/量/買賣超) + 買賣點。"""
        with self._tick_lock:
            ticks = list(self._tick_history)
        with self._point_lock:
            points = list(self._point_history)
        return {"ticks": ticks, "points": points, "code": self.tracked_code}

    def alerts_chart_data(self) -> dict:
        """訊號紀錄的圖表資料：逐筆 + 所有訊號(含起漲/起跌/點火/冰山)。"""
        with self._tick_lock:
            ticks = list(self._tick_history)
            alerts = list(self._alert_records)
        return {"ticks": ticks, "alerts": alerts, "code": self.tracked_code}

    def backtest_chart_data(self) -> dict | None:
        """手動回測的圖表資料：逐筆 + 交易(進出場) + 訊號歷程(若有)。"""
        if not self._last_bt_ctx or self._last_bt is None:
            return None
        code, date, ticks = self._last_bt_ctx
        return {"code": code, "date": date, "ticks": ticks,
                "trades": [t.as_dict() for t in self._last_bt.trades],
                "events": list(self._last_bt.sar_events)}

    def auto_backtest_chart_data(self) -> dict | None:
        """自動回測的圖表資料：逐筆 + 交易 + 訊號歷程(起漲/起跌/點火)。"""
        if not self._last_auto_bt_ctx or self._last_auto_bt is None:
            return None
        code, date, ticks = self._last_auto_bt_ctx
        return {"code": code, "date": date, "ticks": ticks,
                "trades": [t.as_dict() for t in self._last_auto_bt.trades],
                "events": list(self._last_auto_bt.sar_events)}

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
