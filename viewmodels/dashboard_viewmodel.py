"""ViewModel for the Dashboard tab.

第一格：加權指數即時 + 當日分時走勢，背景輪詢更新。之後會逐排擴充其他
拼圖面板。純 I/O 在 index_service；此處負責輪詢排程與 observable 通知。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime

from viewmodels.base_viewmodel import BaseViewModel, ObservableProperty

log = logging.getLogger(__name__)

# 台股盤中時間（含試撮/尾盤緩衝）
_MARKET_OPEN = (9, 0)
_MARKET_CLOSE = (13, 35)
_POLL_OPEN_SEC = 10       # 盤中輪詢間隔
_POLL_CLOSED_SEC = 0      # 收盤後不重複輪詢（0 = 載入一次即停）


def _is_market_open(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    if now.weekday() >= 5:               # 週六日
        return False
    o = now.replace(hour=_MARKET_OPEN[0], minute=_MARKET_OPEN[1],
                    second=0, microsecond=0)
    c = now.replace(hour=_MARKET_CLOSE[0], minute=_MARKET_CLOSE[1],
                    second=0, microsecond=0)
    return o <= now <= c


class DashboardViewModel(BaseViewModel):
    """Dashboard 分頁 ViewModel（第一格：加權指數）。"""

    index_quote = ObservableProperty(None)      # dict | None
    intraday_series = ObservableProperty(None)   # list[(time, value)] | None
    index_status = ObservableProperty("尚未載入")
    is_polling = ObservableProperty(False)
    # 三大法人資金流（全市場總計）
    insti_today = ObservableProperty(None)       # dict | None
    insti_trend = ObservableProperty(None)       # list[dict] | None
    insti_status = ObservableProperty("尚未載入")
    # 高周轉率排行
    turnover_rows = ObservableProperty(None)     # list[dict] | None
    turnover_status = ObservableProperty("尚未載入")

    _INSTI_TREND_DAYS = 10
    _TURNOVER_MIN_LOTS = 1000
    _TURNOVER_TOP_N = 20

    def __init__(self):
        super().__init__()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._series: list[tuple[str, float]] = []
        self._insti_thread: threading.Thread | None = None
        self._turnover_thread: threading.Thread | None = None

    # ------------------------------------------------------------------

    def start(self) -> None:
        """啟動加權指數載入 + 盤中輪詢。重複呼叫安全（已在跑則忽略）。"""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self._start_insti()
        self._start_turnover()

    def refresh(self) -> None:
        """手動刷新一次（不影響輪詢執行緒）。"""
        threading.Thread(target=self._fetch_once, args=(True,),
                         daemon=True).start()
        self._start_insti()
        self._start_turnover()

    # ------------------------------------------------------------------
    # 三大法人資金流
    # ------------------------------------------------------------------

    def _start_insti(self) -> None:
        if self._insti_thread and self._insti_thread.is_alive():
            return
        self._insti_thread = threading.Thread(target=self._load_insti,
                                              daemon=True)
        self._insti_thread.start()

    def _load_insti(self) -> None:
        from services.insti_flow_service import fetch_recent_market_insti

        self.insti_status = "載入三大法人資金流..."
        anchor = datetime.now().strftime("%Y%m%d")
        try:
            trend = fetch_recent_market_insti(anchor, days=self._INSTI_TREND_DAYS)
        except Exception as e:  # noqa: BLE001
            log.warning("load insti failed: %s", e)
            trend = []
        if not trend:
            self.insti_status = "三大法人資金流載入失敗（稍後重試）"
            return
        self.insti_trend = trend
        self.insti_today = trend[-1]
        self.insti_status = f"三大法人（全市場總計）　資料日 {trend[-1]['date']}"

    # ------------------------------------------------------------------
    # 高周轉率排行
    # ------------------------------------------------------------------

    def _start_turnover(self) -> None:
        if self._turnover_thread and self._turnover_thread.is_alive():
            return
        self._turnover_thread = threading.Thread(target=self._load_turnover,
                                                 daemon=True)
        self._turnover_thread.start()

    def _load_turnover(self) -> None:
        from services.db_service import DbService
        from services.turnover_service import latest_top_turnover

        self.turnover_status = "計算高周轉率排行..."
        db = DbService()
        try:
            db.connect()
            date, rows = latest_top_turnover(
                db, min_lots=self._TURNOVER_MIN_LOTS,
                top_n=self._TURNOVER_TOP_N)
        except Exception as e:  # noqa: BLE001
            log.warning("load turnover failed: %s", e)
            self.turnover_status = "高周轉率載入失敗（稍後重試）"
            return
        finally:
            try:
                db.close()
            except Exception:
                pass
        if not rows:
            self.turnover_status = "查無高周轉率資料"
            return
        self.turnover_rows = rows
        self.turnover_status = (
            f"資料日 {date}　量 > {self._TURNOVER_MIN_LOTS:,} 張、"
            f"依周轉率排序 Top {self._TURNOVER_TOP_N}　"
            f"（收盤色為當日漲跌；振幅=近5日平均）"
        )

    # ------------------------------------------------------------------

    def _loop(self) -> None:
        from services.index_service import fetch_intraday_series

        # 先抓即時快照以取得交易日期，再 seed 當日分時
        quote = self._fetch_once(seed_check=False)
        date = (quote or {}).get("date") or datetime.now().strftime("%Y%m%d")
        try:
            series = fetch_intraday_series(date)
        except Exception as e:  # noqa: BLE001
            series = []
            log.warning("seed intraday failed: %s", e)
        if series:
            self._series = series
            self.intraday_series = list(series)

        # 輪詢
        while not self._stop.is_set():
            if _is_market_open():
                self.is_polling = True
                if self._stop.wait(_POLL_OPEN_SEC):
                    break
                self._fetch_once(seed_check=True)
            else:
                self.is_polling = False
                self.index_status = self._closed_status()
                break   # 收盤：載入一次後停止輪詢，靠手動刷新
        self.is_polling = False

    def _closed_status(self) -> str:
        q = self.index_quote or {}
        t = q.get("time") or ""
        d = q.get("date") or ""
        return f"非交易時段，顯示最後行情（{d} {t}）"

    def _fetch_once(self, seed_check: bool) -> dict | None:
        """抓一次即時快照；若時間較新則附加到分時序列。"""
        from services.index_service import fetch_realtime_index

        quote = fetch_realtime_index()
        if not quote:
            self.index_status = "加權指數載入失敗（稍後重試）"
            return None
        self.index_quote = quote
        if _is_market_open():
            self.index_status = f"盤中更新中　{quote.get('time', '')}"
        else:
            self.index_status = self._closed_status()

        # 即時點附加到分時尾端（時間需前進、且有現值）
        t, last = quote.get("time"), quote.get("last")
        if seed_check and t and last is not None:
            if not self._series or t > self._series[-1][0]:
                self._series.append((t, last))
                self.intraday_series = list(self._series)
        return quote

    def shutdown(self) -> None:
        self._stop.set()
