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
        """Download broker data using the saved stock list from config."""
        codes = self._config.get("stock_codes") or []
        if not codes:
            self.last_result = "尚未設定股票清單，請先按「更新清單」"
            self._status(self.last_result)
            log.warning("No stock_codes in config, skipping download")
            return

        codes_str = ", ".join(codes)
        self._status(f"共 {len(codes)} 檔，開始批次下載...")
        log.info("Starting batch download: %d stocks", len(codes))

        # Reuse BatchDownloadViewModel — all pacing / anti-detection /
        # DB write / skip-existing logic lives there, no duplication.
        from viewmodels.batch_download_viewmodel import BatchDownloadViewModel

        vm = BatchDownloadViewModel()
        self.active_vm = vm

        # start_batch sets is_downloading=True synchronously,
        # then kicks off the async coroutine in a background thread.
        vm.start_batch(codes_str, skip_existing=True)

        # Give the async loop a moment to spin up and begin _do_batch
        time.sleep(1)

        # Wait for download to finish
        while vm.is_downloading:
            if self._cancel_event.is_set():
                vm.cancel()
                self._status("排程已取消")
                log.info("Scheduler cancelled by user")
                # Wait for VM to actually stop
                for _ in range(30):
                    if not vm.is_downloading:
                        break
                    time.sleep(1)
                break
            time.sleep(3)

        # Extract result from VM log
        log_text = vm.log_text or ""
        log.info("Batch download finished. Log length: %d chars", len(log_text))

        summary = "已完成"
        for line in reversed(log_text.splitlines()):
            line = line.strip()
            if line and ("成功" in line or "完成" in line or "結束" in line):
                summary = line
                break
        self.last_result = summary
        self._status(f"排程完成：{summary}")
        log.info("Scheduler result: %s", summary)

        # Cleanup
        self.active_vm = None
        vm.shutdown()

    def _status(self, msg: str) -> None:
        self._on_status(msg)
