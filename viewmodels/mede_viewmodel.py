"""ViewModel for the MEDE 「Tick 發動偵測」tab — Phase 2（錄製控制與狀態）。

只做原始資料錄製控制;偵測器/回測於後續階段加入。與大單追蹤共用 Shioaji 連線。
"""

from __future__ import annotations

import threading

from viewmodels.base_viewmodel import BaseViewModel, ObservableProperty
from services.config_service import ConfigService
from services.mede.config import MedeConfig
from services.mede.recorder_service import RecorderService
from services.mede.detection_service import DetectionService

_MEDE_CFG_KEY = "mede"


def _fmt_ns_time(ns: int | None) -> str:
    """event_time_ns（當日 0 點起算 ns）→ HH:MM:SS.mmm。"""
    if not ns:
        return "—"
    total_ms = ns // 1_000_000
    ms = total_ms % 1000
    s = total_ms // 1000
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}.{ms:03d}"


class MedeViewModel(BaseViewModel):

    is_recording = ObservableProperty(False)
    status_data = ObservableProperty(None)   # dict（每秒刷新）
    status_msg = ObservableProperty("")

    # --- 偵測結果 ---
    detect_dates = ObservableProperty(None)     # list[str]（可選交易日）
    detect_codes = ObservableProperty(None)     # list[str]（該日已錄代碼）
    detect_events = ObservableProperty(None)     # list[dict]（事件列，供表格）
    detect_msg = ObservableProperty("")
    detect_running = ObservableProperty(False)

    REFRESH_INTERVAL = 1.0

    def __init__(self, config: ConfigService, shioaji_svc):
        super().__init__()
        self._config = config
        self._sj = shioaji_svc
        cfg = MedeConfig.from_dict(config.get(_MEDE_CFG_KEY))
        self._svc = RecorderService(shioaji_svc, cfg)
        self._detect = DetectionService(cfg)
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

    # ---------------- 偵測結果 ----------------
    def refresh_dates(self):
        """掃描已錄製交易日；若正在錄製，也把今日納入。"""
        def _work():
            dates = self._detect.list_dates()
            self.detect_dates = dates
            if dates:
                self.load_codes(dates[0])
            else:
                self.detect_codes = []
                self.detect_msg = "尚無錄製資料（先於盤中錄製，或確認 storage_dir）"

        threading.Thread(target=_work, daemon=True).start()

    def load_codes(self, trade_date: str):
        def _work():
            try:
                codes = self._detect.list_recorded(trade_date)
            except Exception as exc:
                self.detect_msg = f"讀取代碼失敗：{exc}"
                self.detect_codes = []
                return
            self.detect_codes = codes
            self.detect_msg = (f"{trade_date}：{len(codes)} 檔可偵測"
                               if codes else f"{trade_date}：無資料")

        threading.Thread(target=_work, daemon=True).start()

    def run_detection(self, trade_date: str, code: str):
        """對單股跑偵測並落地；結果填入 detect_events。"""
        if not trade_date or not code or "—" in (trade_date, code):
            self.detect_msg = "請先選擇交易日與股票代碼"
            return

        def _work():
            self.detect_running = True
            self.detect_msg = f"偵測中：{code} @ {trade_date} …"
            try:
                run = self._detect.run(code, trade_date, persist=True)
            except Exception as exc:
                self.detect_msg = f"偵測失敗：{exc}"
                self.detect_running = False
                return
            self.detect_events = [self._event_row(e) for e in run.events]
            self.detect_msg = (f"✓ {run.summary()}"
                               if run.event_count else f"完成，無觸發事件（{run.tick_count} ticks）")
            self.detect_running = False

        threading.Thread(target=_work, daemon=True).start()

    @staticmethod
    def _event_row(e) -> dict:
        return {"time": _fmt_ns_time(e.event_time_ns), "seq": e.seq,
                "type": e.event_type, "dir": e.direction, "score": e.score,
                "conf": e.confidence, "price": e.trigger_price,
                "patterns": "、".join(e.matched_patterns),
                "reason": (e.reasons[0] if e.reasons else "")}

    def shutdown(self):
        self._stop.set()
        try:
            self._svc.shutdown()
        except Exception:
            pass
