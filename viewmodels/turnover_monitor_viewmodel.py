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

    monitor_rows = ObservableProperty(None)     # list[dict] | None
    monitor_status = ObservableProperty("尚未載入")
    is_loading = ObservableProperty(False)

    MIN_LOTS = 1000
    TOP_N = 30

    def __init__(self):
        super().__init__()
        self._thread: threading.Thread | None = None

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
            date, rows = latest_monitor(db, min_lots=self.MIN_LOTS,
                                        top_n=self.TOP_N)
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
        self.monitor_rows = rows
        self.monitor_status = (
            f"資料日 {date}　量 > {self.MIN_LOTS:,} 張、依周轉率 Top {self.TOP_N}"
            f"　主力型態＝近15日分點淨買（隔日沖 vs 波段）；"
            f"MA斜率＝月線斜率%；布林位階 −10~+10（突破可超出）；"
            f"權證多空＝認購/牛證(多) vs 認售/熊證(空) 成交金額（多方佔比%）；"
            f"同類股（同市場同產業）＝平均漲跌幅% + 上漲家數/總家數"
        )

    def shutdown(self) -> None:
        pass
