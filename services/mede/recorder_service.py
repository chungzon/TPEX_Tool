"""RecorderService — MEDE Phase 2 對外門面。

負責：驗證標的、訂閱 Shioaji Tick+BidAsk、啟停 RawRecorder、看門狗重連、狀態查詢。
第一版只錄製，不產生下單。與「大單追蹤」共用同一 ShioajiService（依代碼分派並存）。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

from services.mede.config import MedeConfig
from services.mede.raw_recorder import RawRecorder
from services.mede.storage import SqliteStorage

log = logging.getLogger(__name__)


class RecorderService:
    def __init__(self, shioaji_svc, config: MedeConfig | None = None):
        self._sj = shioaji_svc
        self.config = config or MedeConfig()
        self._recorder: RawRecorder | None = None
        self._codes: list[str] = []
        self._date: str = ""
        self._is_recording = False
        self._wd_stop = threading.Event()
        self._wd_thread: threading.Thread | None = None
        self.last_error = ""

    @property
    def is_recording(self) -> bool:
        return self._is_recording

    @property
    def codes(self) -> list[str]:
        return list(self._codes)

    def start(self, codes: list[str]) -> bool:
        codes = [c.strip() for c in codes if c and c.strip()]
        if not codes:
            self.last_error = "請輸入至少一檔股票代碼"
            return False
        if len(codes) > self.config.max_symbols:
            self.last_error = f"最多同時追蹤 {self.config.max_symbols} 檔（受訂閱限制）"
            return False
        if not self._sj.is_logged_in:
            self.last_error = "尚未連線永豐（即時行情需正式環境登入）"
            return False
        if self._is_recording:
            self.stop()

        self._date = datetime.now().strftime("%Y-%m-%d")
        storage = SqliteStorage(self.config.storage_dir)
        self._recorder = RawRecorder(storage, self.config)
        self._recorder.start(self._date)

        ok_codes = []
        for code in codes:
            if self._sj.subscribe_quote(code, on_tick=self._recorder.on_tick,
                                        on_bidask=self._recorder.on_bidask):
                ok_codes.append(code)
            else:
                log.warning("MEDE subscribe failed: %s", code)
        if not ok_codes:
            self.last_error = "全部訂閱失敗（代碼錯誤或非交易時段）"
            self._recorder.stop()
            self._recorder = None
            return False

        self._codes = ok_codes
        self._is_recording = True
        self.last_error = ""
        self._start_watchdog()
        log.info("MEDE recording started: %s (%s)", ok_codes, self._date)
        return True

    def stop(self) -> None:
        self._wd_stop.set()
        if self._wd_thread:
            self._wd_thread.join(timeout=6)
            self._wd_thread = None
        for code in self._codes:
            try:
                self._sj.unsubscribe_quote(code)
            except Exception:
                log.warning("unsubscribe %s failed", code)
        if self._recorder:
            self._recorder.stop()      # flush + 保存品質 + 關檔
        self._codes = []
        self._is_recording = False

    # ---- 看門狗：盤中資料停滯 → 嘗試重新訂閱 ----
    def _start_watchdog(self) -> None:
        self._wd_stop.clear()
        self._wd_thread = threading.Thread(target=self._watchdog, name="mede-watchdog",
                                           daemon=True)
        self._wd_thread.start()

    def _in_market_hours(self) -> bool:
        now = datetime.now().strftime("%H:%M:%S")
        return self.config.market_open <= now <= self.config.market_close

    def _watchdog(self) -> None:
        cfg = self.config
        while not self._wd_stop.wait(cfg.watchdog_interval_s):
            if not self._is_recording or not self._recorder:
                continue
            if not self._in_market_hours():
                continue
            st = self._recorder.status()
            lag = st.get("tick_lag_ms")
            # 有訂閱、盤中、但資料停滯過久 → 重新訂閱
            if lag is not None and lag > cfg.stale_seconds * 1000:
                if not self._sj.is_logged_in:
                    log.warning("MEDE watchdog: not logged in, cannot resubscribe")
                    continue
                log.warning("MEDE watchdog: stale %.0fms → resubscribing %s",
                            lag, self._codes)
                for code in self._codes:
                    try:
                        self._sj.subscribe_quote(
                            code, on_tick=self._recorder.on_tick,
                            on_bidask=self._recorder.on_bidask)
                        self._recorder.note_reconnect(code)
                    except Exception:
                        log.exception("resubscribe %s failed", code)

    def status(self) -> dict:
        base = {"is_recording": self._is_recording, "codes": list(self._codes),
                "trade_date": self._date, "last_error": self.last_error,
                "in_market_hours": self._in_market_hours()}
        if self._recorder:
            base.update(self._recorder.status())
        return base

    def shutdown(self) -> None:
        try:
            self.stop()
        except Exception:
            log.exception("RecorderService shutdown failed")
