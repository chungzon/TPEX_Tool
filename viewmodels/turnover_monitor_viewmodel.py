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
    search_rows = ObservableProperty(())        # 搜尋加入的個股（置頂顯示）
    search_status = ObservableProperty("")
    search_loading = ObservableProperty(False)

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

    # ---- 搜尋個股 -----------------------------------------------------
    def search(self, query: str) -> None:
        """依代碼或名稱搜尋個股；不在 Top N 內則自行運算後置頂顯示。"""
        q = (query or "").strip()
        if not q:
            return
        if self.search_loading:
            return
        rows = self.monitor_rows or []
        # 已在排行內 → 不重算，直接提示（該列本來就看得到）
        for r in rows:
            if r.get("stock_code") == q or r.get("stock_name") == q:
                self.search_status = (
                    f"{r['stock_code']} {r['stock_name']} 已在 Top "
                    f"{self.top_n} 排行中（第 {r.get('rank', '?')} 名）")
                return
        for r in self.search_rows or ():
            if r.get("stock_code") == q or r.get("stock_name") == q:
                self.search_status = f"{q} 已在搜尋結果中"
                return
        self.search_loading = True
        self.search_status = f"搜尋 {q}…"

        def _work():
            from services.db_service import DbService
            from services.turnover_monitor_service import lookup_stock

            db = DbService()
            try:
                db.connect()
                days = (self.ctx or {}).get("days") or []
                imap = (self.ctx or {}).get("industry_map") or {}
                if not days:
                    self.search_status = "尚未載入行情視窗，請先重新整理"
                    return
                row = lookup_stock(db, q, self.data_date, days, imap)
                if row is None:
                    self.search_status = f"查無「{q}」或當日無成交"
                    return
                # 已在排行內的代碼就不重複置頂（名稱搜尋可能命中排行內個股）
                if any(x.get("stock_code") == row["stock_code"]
                       for x in (self.monitor_rows or [])):
                    self.search_status = (
                        f"{row['stock_code']} {row['stock_name']} "
                        f"已在 Top {self.top_n} 排行中")
                    return
                self.search_rows = tuple(self.search_rows or ()) + (row,)
                self.search_status = (
                    f"已加入 {row['stock_code']} {row['stock_name']}"
                    f"（不在 Top {self.top_n} 內，單獨運算）")
                self._apply_filters()
            except Exception as e:  # noqa: BLE001
                log.exception("search failed")
                self.search_status = f"搜尋失敗：{e}"
            finally:
                try:
                    db.close()
                except Exception:
                    pass
                self.search_loading = False

        threading.Thread(target=_work, daemon=True).start()

    def clear_search(self) -> None:
        if not self.search_rows:
            return
        self.search_rows = ()
        self.search_status = ""
        self._apply_filters()

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
        # 搜尋結果一律置頂且不受篩選影響 —— 使用者指名要看的標的，
        # 被篩掉會讓人以為搜尋失敗。
        extra = list(self.search_rows or ())
        self.filtered_rows = extra + out
        base = (f"篩選後 {len(out)} / {len(rows)} 檔"
                if self.active_filters else f"共 {len(rows)} 檔")
        self.filter_summary = (f"{base}　+ 搜尋 {len(extra)} 檔"
                               if extra else base)

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
        # 重新載入可能換資料日，舊的搜尋結果會變成不同日期的列，先清掉
        self.search_rows = ()
        self.search_status = ""
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
            f"　短沖/隔日沖買入%＝該風格分點當日買入量佔成交量比"
            f"（風格取自系統設定計算的分點風格表，未計算則顯示 —）；"
            f"MA斜率＝月線斜率%；布林位階 −10~+10（突破可超出）；"
            f"主力均價＝當日買超前15家分點、以各家買進量加權的買進均價"
            f"（集中度為負即賣超主導時，改顯示賣超前15家的加權賣出均價）；"
            f"權證多空＝認購/牛證(多) vs 認售/熊證(空) 成交金額（多方佔比%）；"
            f"同類股（同市場同產業）＝平均漲跌幅% + 上漲家數/總家數"
        )

    def shutdown(self) -> None:
        pass
