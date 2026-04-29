"""ViewModel for the system settings tab."""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime

from viewmodels.base_viewmodel import BaseViewModel, ObservableProperty
from services.config_service import ConfigService
from services.scheduler_service import SchedulerService

log = logging.getLogger(__name__)


class SettingsViewModel(BaseViewModel):

    scheduler_enabled = ObservableProperty(False)
    scheduler_time = ObservableProperty("18:00")
    scheduler_top_n = ObservableProperty(300)
    status_text = ObservableProperty("")
    next_run_text = ObservableProperty("")
    last_result_text = ObservableProperty("")
    # Stock list info
    stock_list_info = ObservableProperty("")   # e.g. "300 檔（2026-04-22 更新）"
    stock_list_loading = ObservableProperty(False)
    # TDCC batch download
    tdcc_status = ObservableProperty("")
    tdcc_loading = ObservableProperty(False)

    # Live download progress
    progress = ObservableProperty(0.0)
    progress_text = ObservableProperty("")
    log_text = ObservableProperty("")
    is_downloading = ObservableProperty(False)

    def __init__(self, config: ConfigService, scheduler: SchedulerService):
        super().__init__()
        self._config = config
        self._scheduler = scheduler

        # Load persisted values
        self.scheduler_enabled = config.get("scheduler_enabled")
        self.scheduler_time = config.get("scheduler_time")
        self.scheduler_top_n = config.get("scheduler_top_n")
        self._update_list_info()
        self._refresh_status()

        # Auto-start if enabled
        if self.scheduler_enabled:
            self._scheduler.start()
            self._refresh_status()

    def toggle_scheduler(self, enabled: bool) -> None:
        self.scheduler_enabled = enabled
        self._config.set("scheduler_enabled", enabled)
        self._scheduler.reschedule()
        self._refresh_status()

    def save_time(self, time_str: str) -> None:
        time_str = time_str.strip()
        if not re.match(r"^\d{1,2}:\d{2}$", time_str):
            self.status_text = "時間格式錯誤，請使用 HH:MM"
            return
        self.scheduler_time = time_str
        self._config.set("scheduler_time", time_str)
        self.status_text = f"排程時間已更新為 {time_str}"
        if self.scheduler_enabled:
            self._scheduler.reschedule()
        self._refresh_status()

    def save_top_n(self, n_str: str) -> None:
        n_str = n_str.strip()
        if not n_str.isdigit() or int(n_str) < 1:
            self.status_text = "請輸入正整數"
            return
        n = int(n_str)
        self.scheduler_top_n = n
        self._config.set("scheduler_top_n", n)
        self.status_text = f"下載數量已更新為 {n} 檔"

    # ---- Stock list management ----

    def refresh_stock_list(self) -> None:
        """Fetch today's top-N stocks by volume and save to config."""
        if self.stock_list_loading:
            return
        self.stock_list_loading = True
        self.status_text = "正在從 TPEX 取得股票清單..."

        def _work():
            try:
                from services.tpex_api_service import fetch_top_volume_stocks
                top_n = self._config.get("scheduler_top_n") or 300
                stocks = fetch_top_volume_stocks(top_n)
                if not stocks:
                    self.status_text = "無法取得股票清單，請確認網路連線"
                    return
                codes = [s.stock_code for s in stocks]
                today = datetime.now().strftime("%Y-%m-%d")
                self._config.set("stock_codes", codes)
                self._config.set("stock_list_date", today)
                self._update_list_info()
                self.status_text = f"已更新清單：{len(codes)} 檔"
            except Exception as e:
                self.status_text = f"更新清單失敗：{e}"
            finally:
                self.stock_list_loading = False

        threading.Thread(target=_work, daemon=True).start()

    def _update_list_info(self) -> None:
        codes = self._config.get("stock_codes") or []
        date = self._config.get("stock_list_date") or ""
        if codes:
            self.stock_list_info = f"{len(codes)} 檔（{date} 更新）"
        else:
            self.stock_list_info = "尚未設定"

    def download_tdcc(self) -> None:
        """Download TDCC holder distribution for ALL stocks."""
        if self.tdcc_loading:
            return
        self.tdcc_loading = True
        self.tdcc_status = "從 TDCC 下載集保資料..."

        def _work():
            try:
                from services.tdcc_service import fetch_all_distributions
                from services.db_service import DbService

                self.tdcc_status = "正在從 TDCC 取得全部股票資料..."
                results = fetch_all_distributions()
                total = len(results)
                self.tdcc_status = f"取得 {total} 檔，寫入資料庫..."

                db = DbService()
                ok = 0
                try:
                    db.ensure_tables()
                    for i, (code, dist) in enumerate(results.items()):
                        if dist.levels:
                            levels = [
                                {"level": lv.level, "label": lv.label,
                                 "holders": lv.holders, "shares": lv.shares,
                                 "pct": lv.pct}
                                for lv in dist.levels
                            ]
                            db.save_distribution(code, dist.report_date, levels)
                            ok += 1
                        if (i + 1) % 200 == 0:
                            self.tdcc_status = (
                                f"寫入資料庫 {i + 1}/{total}..."
                            )
                finally:
                    db.close()

                self.tdcc_status = f"完成！共寫入 {ok} 檔集保資料"
                log.info("TDCC all stocks: %d saved", ok)
            except Exception as e:
                self.tdcc_status = f"失敗：{e}"
                log.exception("TDCC batch download failed")
            finally:
                self.tdcc_loading = False

        threading.Thread(target=_work, daemon=True).start()

    def run_now(self) -> None:
        """Trigger immediate download using saved stock list."""
        codes = self._config.get("stock_codes") or []
        if not codes:
            self.status_text = "請先按「更新清單」取得股票清單"
            return
        self._scheduler.run_now()

    # ---- Status refresh ----

    def _refresh_status(self) -> None:
        sched = self._scheduler
        if sched.next_run:
            self.next_run_text = sched.next_run.strftime("%Y-%m-%d %H:%M")
        else:
            self.next_run_text = "—"
        self.last_result_text = sched.last_result or "—"

    def refresh(self) -> None:
        """Called periodically from UI to update live status."""
        self._refresh_status()
        sched = self._scheduler
        self.is_downloading = sched.is_running
        if sched.is_running and sched.active_vm is not None:
            vm = sched.active_vm
            self.progress = vm.progress
            self.progress_text = vm.progress_text
            self.log_text = vm.log_text or ""
            self.status_text = vm.status_text
        elif not sched.is_running:
            self.progress = 0.0
            self.progress_text = ""

    def shutdown(self) -> None:
        self._scheduler.stop()
