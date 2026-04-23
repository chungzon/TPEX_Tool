from __future__ import annotations

import asyncio
import threading
from typing import Any

from viewmodels.base_viewmodel import BaseViewModel, ObservableProperty
from services.broker_data_service import BrokerDataService, BrokerDataResult


class BrokerDownloadViewModel(BaseViewModel):
    """ViewModel for 上櫃分點資料下載 page."""

    status_text = ObservableProperty("就緒")
    is_downloading = ObservableProperty(False)
    progress = ObservableProperty(0.0)
    result_data = ObservableProperty(None)  # BrokerDataResult | None
    error_text = ObservableProperty("")

    def __init__(self):
        super().__init__()
        self._service = BrokerDataService()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def _ensure_loop(self):
        """Create a dedicated asyncio event loop running in a background thread."""
        if self._loop is None or not self._loop.is_running():
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
            self._thread.start()

    def start_download(self, stock_code: str):
        """Trigger download from the UI thread — runs async work in background."""
        if self.is_downloading:
            return
        if not stock_code.strip():
            self.error_text = "請輸入股票代碼"
            return

        self.error_text = ""
        self.is_downloading = True
        self.progress = 0.0
        self.result_data = None

        self._ensure_loop()
        asyncio.run_coroutine_threadsafe(self._do_download(stock_code.strip()), self._loop)

    async def _do_download(self, stock_code: str):
        try:
            self.progress = 0.2

            def on_status(msg: str):
                self.status_text = msg

            result = await self._service.download(stock_code, on_status=on_status)
            self.progress = 1.0
            self.result_data = result
            self.status_text = f"完成！共 {len(result.records)} 筆分點資料"
        except Exception as e:
            self.error_text = str(e)
            self.status_text = f"錯誤：{e}"
        finally:
            self.is_downloading = False

    def shutdown(self):
        if self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(self._service.shutdown(), self._loop)
