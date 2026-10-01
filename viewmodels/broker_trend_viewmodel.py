"""ViewModel for the 分點買賣超趨勢視窗（由監控彈窗的買賣超排行點列開啟）。

單一分點在單一個股的日線：股價 K 線 + 每日買賣超長條 + 累計買賣超。
重查詢在背景執行緒，完成後以 ObservableProperty 通知；View 以 after(0, ...)
marshal 回 UI 執行緒。
"""

from __future__ import annotations

import logging
import threading

from viewmodels.base_viewmodel import BaseViewModel, ObservableProperty

log = logging.getLogger(__name__)


class BrokerTrendViewModel(BaseViewModel):
    """單一分點 × 單一個股的買賣超日線趨勢。"""

    series = ObservableProperty(None)       # dict | None
    status = ObservableProperty("載入中…")

    def __init__(self, stock_code: str, stock_name: str, broker_code: str,
                 broker_name: str, start: str, end: str):
        super().__init__()
        self.code = stock_code
        self.stock_name = stock_name
        self.broker_code = broker_code
        self.broker_name = broker_name
        self.start = start
        self.end = end
        self._thread: threading.Thread | None = None

    def load(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._work, daemon=True)
        self._thread.start()

    def set_range(self, start: str, end: str) -> None:
        if not start or not end or start > end:
            self.status = "區間不正確（起始日需早於或等於結束日）"
            return
        self.start = start
        self.end = end
        self.load()

    def _work(self) -> None:
        from services.db_service import DbService
        from services.turnover_monitor_service import broker_daily_series

        self.status = "載入分點日線…"
        db = DbService()
        try:
            db.connect()
            s = broker_daily_series(db, self.code, self.broker_code,
                                    self.broker_name, self.start, self.end)
        except Exception as e:  # noqa: BLE001
            log.warning("broker trend load failed %s/%s: %s", self.code,
                        self.broker_code, e)
            self.series = {"error": str(e)}
            self.status = f"載入失敗：{e}"
            return
        finally:
            try:
                db.close()
            except Exception:
                pass
        self.series = s
        if s.get("error"):
            self.status = s["error"]
            return
        n = len(s.get("dates") or [])
        pd = s.get("price_days", 0)
        act = sum(1 for v in s.get("net_lots") or [] if v != 0)
        note = ""
        if pd < n:
            # 股價缺漏是 StockDailySummary 的已知問題（分點爬蟲建列時不帶價）
            note = (f"　⚠ 僅 {pd} 日有股價（其餘日 K 線略過，買賣超照常顯示）；"
                    f"可至「補資料」分頁補當日每日行情")
        self.series = s
        self.status = (f"{self.start} ~ {self.end}　共 {n} 個交易日、"
                       f"其中 {act} 日有進出{note}")

    def shutdown(self) -> None:
        pass
