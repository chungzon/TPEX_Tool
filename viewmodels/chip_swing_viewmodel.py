"""ViewModel for the 籌碼波段 tab (CHIP).

單股籌碼波段分析：載入逐日特徵 + chip_score + 波段訊號 + 多週期回測，
於背景執行緒完成後透過 ObservableProperty 通知 View。純計算在
`chip_swing_service`；此處僅負責輸入驗證、執行緒調度與資料轉遞。
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timedelta

from viewmodels.base_viewmodel import BaseViewModel, ObservableProperty
from services.chip_config import ChipConfig

log = logging.getLogger(__name__)


class ChipSwingViewModel(BaseViewModel):
    """籌碼波段分頁的 ViewModel。"""

    status_text = ObservableProperty("就緒")
    error_text = ObservableProperty("")
    is_running = ObservableProperty(False)
    result_data = ObservableProperty(None)   # dict | None（analyse() 輸出 + 序列）

    def __init__(self):
        super().__init__()
        self.cfg = ChipConfig()

    # ------------------------------------------------------------------

    def analyse(self, stock_code: str, start_date: str, end_date: str) -> None:
        if self.is_running:
            return
        code = (stock_code or "").strip()
        s = (start_date or "").strip()
        e = (end_date or "").strip()
        if not code:
            self.error_text = "請輸入股票代碼"
            return
        if not (re.match(r"^\d{4}-\d{2}-\d{2}$", s)
                and re.match(r"^\d{4}-\d{2}-\d{2}$", e)):
            self.error_text = "日期格式錯誤，請用 yyyy-mm-dd"
            return
        try:
            sdt = datetime.strptime(s, "%Y-%m-%d")
            edt = datetime.strptime(e, "%Y-%m-%d")
        except ValueError:
            self.error_text = "日期無效"
            return
        if sdt > edt:
            self.error_text = "起始日不能晚於結束日"
            return

        self.error_text = ""
        self.is_running = True
        self.result_data = None
        self.status_text = f"分析中 {code}..."
        threading.Thread(
            target=self._work, args=(code, s, e), daemon=True,
        ).start()

    # ------------------------------------------------------------------

    def _work(self, code: str, start_date: str, end_date: str) -> None:
        from services.db_service import DbService
        from services.chip_swing_service import (
            ChipSwingService, chip_signals_to_dicts, backtest_results_to_dicts,
        )

        db = DbService()
        try:
            db.connect()
            svc = ChipSwingService(db, self.cfg)
            res = svc.analyse(code, start_date, end_date)
            feats = res["features"]
            if not feats:
                self.error_text = (
                    f"查無 {code} 在 {start_date}~{end_date} 的資料"
                )
                self.status_text = "無資料"
                return

            # 序列化為 View 易用的結構（避免 View 依賴 dataclass）
            payload = {
                "stock_code": res["stock_code"],
                "stock_name": res["stock_name"],
                "features": [f.as_dict() for f in feats],
                "signals": chip_signals_to_dicts(res["signals"]),
                "backtest": backtest_results_to_dicts(res["backtest"]),
                "brokers": res["brokers"],
                "config": {
                    "buy_threshold": self.cfg.buy_threshold,
                    "exit_threshold": self.cfg.exit_threshold,
                },
            }
            self.result_data = payload
            self.status_text = (
                f"完成：{res['stock_code']} {res['stock_name']}　"
                f"{len(feats)} 個交易日、{len(res['signals'])} 筆訊號"
            )
        except Exception as ex:  # noqa: BLE001
            self.error_text = str(ex)
            self.status_text = f"錯誤：{ex}"
            log.exception("Chip swing analyse failed for %s", code)
        finally:
            try:
                db.close()
            except Exception:
                pass
            self.is_running = False

    def shutdown(self) -> None:
        pass


def default_date_range() -> tuple[str, str]:
    """預設範圍：近 1 年（波段持有最長 60 日，需足夠回看與前向資料）。"""
    end = datetime.now().date()
    start = end - timedelta(days=365)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
