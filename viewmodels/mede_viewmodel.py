"""ViewModel for the MEDE 「Tick 發動偵測」tab — Phase 2（錄製控制與狀態）。

只做原始資料錄製控制;偵測器/回測於後續階段加入。與大單追蹤共用 Shioaji 連線。
"""

from __future__ import annotations

import threading

from viewmodels.base_viewmodel import BaseViewModel, ObservableProperty
from services.config_service import ConfigService
from services.mede.config import MedeConfig
from services.mede.recorder_service import RecorderService

_MEDE_CFG_KEY = "mede"


class MedeViewModel(BaseViewModel):

    is_recording = ObservableProperty(False)
    status_data = ObservableProperty(None)   # dict（每秒刷新）
    status_msg = ObservableProperty("")

    REFRESH_INTERVAL = 1.0

    def __init__(self, config: ConfigService, shioaji_svc):
        super().__init__()
        self._config = config
        self._sj = shioaji_svc
        cfg = MedeConfig.from_dict(config.get(_MEDE_CFG_KEY))
        self._svc = RecorderService(shioaji_svc, cfg)
        self._stop = threading.Event()
        self._refresh_thread: threading.Thread | None = None

    @property
    def svc(self) -> RecorderService:
        return self._svc

    @property
    def saved_symbols(self) -> list[str]:
        return list(self._svc.config.tracked_symbols)

    def start(self, codes_text: str):
        codes = [c for c in codes_text.replace(",", " ").split() if c.strip()]

        def _work():
            ok = self._svc.start(codes)
            if ok:
                self._svc.config.tracked_symbols = self._svc.codes
                self._config.set(_MEDE_CFG_KEY, self._svc.config.to_dict())
                self.is_recording = True
                self.status_msg = f"✓ 錄製中：{'、'.join(self._svc.codes)}"
                self._start_refresh()
            else:
                self.status_msg = f"無法開始：{self._svc.last_error}"

        threading.Thread(target=_work, daemon=True).start()

    def stop(self):
        def _work():
            self._stop.set()
            self._svc.stop()
            self.is_recording = False
            self.status_data = self._svc.status()
            self.status_msg = "已停止錄製（資料與品質已保存）"

        threading.Thread(target=_work, daemon=True).start()

    def _start_refresh(self):
        self._stop.clear()
        self._refresh_thread = threading.Thread(
            target=self._refresh_loop, daemon=True)
        self._refresh_thread.start()

    def _refresh_loop(self):
        while not self._stop.is_set() and self._svc.is_recording:
            try:
                self.status_data = self._svc.status()
            except Exception:
                pass
            self._stop.wait(self.REFRESH_INTERVAL)

    def shutdown(self):
        self._stop.set()
        try:
            self._svc.shutdown()
        except Exception:
            pass
