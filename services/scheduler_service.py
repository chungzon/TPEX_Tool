"""In-app scheduler — runs batch download at a configured daily time.

Reuses BatchDownloadViewModel for the actual download so all pacing,
anti-detection, DB writes, and skip-existing logic stay in one place.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Callable

from services.config_service import ConfigService

log = logging.getLogger(__name__)


class SchedulerService:
    """Timer-based daily scheduler that downloads broker data."""

    def __init__(
        self,
        config: ConfigService,
        on_status: Callable[[str], None] | None = None,
    ):
        self._config = config
        self._on_status = on_status or (lambda _: None)
        self._timer: threading.Timer | None = None
        self._running = False
        self._cancel_event = threading.Event()
        self.next_run: datetime | None = None
        self.last_result: str = ""
        self.last_run_time: str = ""
        self.active_vm = None  # BatchDownloadViewModel while running

    # -- Public API --

    def start(self) -> None:
        """Schedule next run based on config time."""
        self._cancel_timer()
        self._cancel_event.clear()
        time_str = self._config.get("scheduler_time") or "18:00"
        delay = self._seconds_until(time_str)
        self.next_run = datetime.now() + timedelta(seconds=delay)
        self._status(f"排程已啟動，下次執行：{self.next_run.strftime('%Y-%m-%d %H:%M')}")
        log.info("Scheduler armed: next run in %.0f seconds at %s",
                 delay, self.next_run)
        self._timer = threading.Timer(delay, self._on_trigger)
        self._timer.daemon = True
        self._timer.start()

    def stop(self) -> None:
        """Cancel pending timer and signal running download to stop."""
        self._cancel_event.set()
        self._cancel_timer()
        self.next_run = None

    def reschedule(self) -> None:
        """Restart with current config."""
        self.stop()
        if self._config.get("scheduler_enabled"):
            self.start()
        else:
            self._status("排程已停用")

    def run_now(self) -> None:
        """Trigger an immediate download in a background thread."""
        if self._running:
            self._status("下載正在執行中，請稍候")
            return
        self._cancel_event.clear()

        def _manual_run():
            self._running = True
            self._status("手動下載開始...")
            log.info("Manual download triggered")
            try:
                self._run_download()
            except Exception as e:
                self.last_result = f"錯誤：{e}"
                self._status(f"下載失敗：{e}")
                log.exception("Manual download failed")
            finally:
                self._running = False
                self.last_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        threading.Thread(target=_manual_run, daemon=True).start()

    @property
    def is_running(self) -> bool:
        return self._running

    # -- Internal --

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    @staticmethod
    def _seconds_until(time_str: str) -> float:
        """Seconds from now until next occurrence of HH:MM today/tomorrow."""
        now = datetime.now()
        parts = time_str.split(":")
        target_h = int(parts[0]) if len(parts) > 0 else 18
        target_m = int(parts[1]) if len(parts) > 1 else 0
        target = now.replace(hour=target_h, minute=target_m, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds()

    def _on_trigger(self) -> None:
        """Called by Timer when scheduled time arrives."""
        if self._cancel_event.is_set():
            return
        self._running = True
        self._status("排程下載開始...")
        log.info("Scheduler triggered, starting download")
        try:
            self._run_download()
        except Exception as e:
            self.last_result = f"錯誤：{e}"
            self._status(f"排程執行失敗：{e}")
            log.exception("Scheduler download failed")
        finally:
            self._running = False
            self.last_run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # Schedule next day
            if self._config.get("scheduler_enabled") and not self._cancel_event.is_set():
                self.start()

    def _run_download(self) -> None:
        """Download broker data, then verify."""
        codes = self._config.get("stock_codes") or []
        if not codes:
            self.last_result = "尚未設定股票清單，請先按「更新清單」"
            self._status(self.last_result)
            log.warning("No stock_codes in config, skipping download")
            return

        self._status(f"分點資料下載（共 {len(codes)} 檔）...")
        log.info("Broker data download: %d stocks", len(codes))

        from viewmodels.batch_download_viewmodel import BatchDownloadViewModel

        vm = BatchDownloadViewModel()
        self.active_vm = vm
        vm.start_batch(", ".join(codes), skip_existing=True)
        time.sleep(1)

        while vm.is_downloading:
            if self._cancel_event.is_set():
                vm.cancel()
                self._status("排程已取消")
                for _ in range(30):
                    if not vm.is_downloading:
                        break
                    time.sleep(1)
                self.active_vm = None
                vm.shutdown()
                return
            time.sleep(3)

        broker_log = vm.log_text or ""
        self.active_vm = None
        vm.shutdown()

        # Verify and auto-retry failures
        if not self._cancel_event.is_set():
            self._status("驗證下載結果...")
            verify_result = self._verify_download(codes, broker_log)
        else:
            verify_result = ""

        # Summary
        broker_summary = ""
        for line in reversed(broker_log.splitlines()):
            line = line.strip()
            if line and ("成功" in line or "結束" in line):
                broker_summary = line
                break

        parts = [p for p in [broker_summary, verify_result] if p]
        self.last_result = "　".join(parts) or "已完成"
        self._status(f"排程完成：{self.last_result}")
        log.info("Scheduler result: %s", self.last_result)

    def _verify_download(self, codes: list[str], broker_log: str) -> str:
        """Check which stocks failed or were missing from today's download."""
        # Parse the log to find error lines
        error_codes = []
        for line in broker_log.splitlines():
            if "錯誤" in line:
                # Extract stock code from lines like "[W1][6180] 錯誤：..."
                for code in codes:
                    if f"[{code}]" in line:
                        error_codes.append(code)
                        break

        # Deduplicate
        error_codes = list(dict.fromkeys(error_codes))

        if not error_codes:
            self._status("驗證通過：所有股票下載成功")
            return "驗證通過"
        else:
            msg = f"驗證：{len(error_codes)} 檔下載失敗"
            self._status(f"{msg}（{', '.join(error_codes[:10])}...）")
            log.warning("Failed stocks: %s", error_codes)

            # Auto-retry failed stocks
            if len(error_codes) <= 30 and not self._cancel_event.is_set():
                self._status(f"自動重試 {len(error_codes)} 檔失敗的股票...")
                log.info("Auto-retrying %d failed stocks", len(error_codes))

                from viewmodels.batch_download_viewmodel import BatchDownloadViewModel
                vm2 = BatchDownloadViewModel(num_workers=1)  # single worker for retry
                self.active_vm = vm2
                vm2.start_batch(", ".join(error_codes), skip_existing=True)
                time.sleep(1)

                while vm2.is_downloading:
                    if self._cancel_event.is_set():
                        vm2.cancel()
                        break
                    time.sleep(3)

                self.active_vm = None
                vm2.shutdown()
                return f"重試 {len(error_codes)} 檔"
            else:
                return msg

    def _status(self, msg: str) -> None:
        self._on_status(msg)
