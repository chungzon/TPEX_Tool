"""ViewModel for the 高周轉率監控 tab.

沿用周轉率排行，逐檔補主力型態（波段/隔日沖）、均線斜率、布林位階。
重運算（每檔 DB 價格+分點查詢）於背景執行緒，完成後以 ObservableProperty 通知。
"""

from __future__ import annotations

import logging
import threading

from viewmodels.base_viewmodel import BaseViewModel, ObservableProperty

log = logging.getLogger(__name__)


class TurnoverMonitorViewModel(BaseViewModel):
    """高周轉率監控分頁 ViewModel。"""

    monitor_rows = ObservableProperty(None)     # list[dict] | None（未篩選）
    filtered_rows = ObservableProperty(None)    # list[dict] | None（套篩選後）
    active_filters = ObservableProperty(())     # tuple[str, ...] 已勾選的條件
    filter_params = ObservableProperty({})      # {key: 門檻值}（含未勾選者）
    filter_summary = ObservableProperty("")
    monitor_status = ObservableProperty("尚未載入")
    is_loading = ObservableProperty(False)
    monitor_mode = ObservableProperty(False)    # 監控模式（點列開彈窗）
    top_n = ObservableProperty(30)              # 取周轉率前 N 檔

    MIN_LOTS = 1000
    TOP_N = 30                    # 預設值
    TOP_N_CHOICES = (30, 50, 100)

    def __init__(self, shioaji_svc=None):
        super().__init__()
        self._thread: threading.Thread | None = None
        self._sj = shioaji_svc
        # 供監控彈窗使用（載入時快取，避免重抓行情視窗）
        self.data_date: str = ""
        self.ctx: dict = {}

    def toggle_monitor(self) -> None:
        self.monitor_mode = not self.monitor_mode

    def set_top_n(self, n: int) -> None:
        """改變取樣檔數並重新載入（載入中則忽略，避免疊起多條執行緒）。"""
        if n == self.top_n or (self._thread and self._thread.is_alive()):
            return
        self.top_n = n
        self.load()

    # ---- 篩選 ---------------------------------------------------------
    def toggle_filter(self, key: str) -> None:
        """切換單一篩選條件（多條件取交集）。"""
        cur = set(self.active_filters or ())
        cur.symmetric_difference_update({key})
        self.active_filters = tuple(sorted(cur))
        self._apply_filters()

    def set_filter_param(self, key: str, value: float) -> None:
        """設定帶參數條件的門檻值。條件未勾選時僅記住，勾選後即生效。"""
        cur = dict(self.filter_params or {})
        if cur.get(key) == value:
            return
        cur[key] = value
        self.filter_params = cur          # 指派新 dict 才會觸發通知
        if key in (self.active_filters or ()):
            self._apply_filters()

    def filter_param(self, key: str):
        """目前生效的參數值（未設定過則回註冊表預設值）。"""
        from services.turnover_monitor_service import filter_default

        cur = self.filter_params or {}
        return cur[key] if key in cur else filter_default(key)

    def clear_filters(self) -> None:
        if not self.active_filters:
            return
        self.active_filters = ()
        self._apply_filters()

    def _apply_filters(self) -> None:
        """依 active_filters + filter_params 重算 filtered_rows + 計數摘要。"""
        from services.turnover_monitor_service import apply_filters

        rows = self.monitor_rows
        if rows is None:
            self.filtered_rows = None
            self.filter_summary = ""
            return
        out = apply_filters(rows, self.active_filters, self.filter_params)
        self.filtered_rows = out
        self.filter_summary = (
            f"篩選後 {len(out)} / {len(rows)} 檔"
            if self.active_filters else f"共 {len(rows)} 檔")

    def load(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._work, daemon=True)
        self._thread.start()

    refresh = load

    def _work(self) -> None:
        from services.db_service import DbService
        from services.turnover_monitor_service import latest_monitor

        self.is_loading = True
        self.monitor_status = "計算高周轉率監控（周轉率 + 主力型態 + 技術面）..."
        db = DbService()
        try:
            db.connect()
            date, rows, ctx = latest_monitor(db, min_lots=self.MIN_LOTS,
                                             top_n=self.top_n, return_ctx=True)
            self.data_date = date
            self.ctx = ctx
        except Exception as e:  # noqa: BLE001
            log.exception("monitor load failed")
            self.monitor_status = f"載入失敗：{e}"
            return
        finally:
            try:
                db.close()
            except Exception:
                pass
            self.is_loading = False
        if not rows:
            self.monitor_status = "查無資料"
            return
        # 原始周轉率名次：篩選後仍顯示原名次，避免重編號失去排行意義
        for i, r in enumerate(rows, 1):
            r["rank"] = i
        self.monitor_rows = rows
        self._apply_filters()
        self.monitor_status = (
            f"資料日 {date}　量 > {self.MIN_LOTS:,} 張、依周轉率 Top {self.top_n}"
            f"　主力型態＝近15日分點淨買（隔日沖 vs 波段）；"
            f"MA斜率＝月線斜率%；布林位階 −10~+10（突破可超出）；"
            f"主力均價＝當日買超前15家分點、以各家買進量加權的買進均價"
            f"（集中度為負即賣超主導時，改顯示賣超前15家的加權賣出均價）；"
            f"權證多空＝認購/牛證(多) vs 認售/熊證(空) 成交金額（多方佔比%）；"
            f"同類股（同市場同產業）＝平均漲跌幅% + 上漲家數/總家數"
        )

    def shutdown(self) -> None:
        pass
