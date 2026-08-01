"""ViewModel for the 監控 detail popup（單檔即時監控視窗）。

一次載入四塊資料，各自背景執行緒、以 ObservableProperty 通知：
- daily_trend：DB 日線 → 月線(MA20) + 布林通道 + 量（趨勢圖）。
- peers：同市場同產業「成交量前五」同類股（最新價 + 當日漲跌幅%）。
- warrant：權證多空（最新可得日；認購/牛=多、認售/熊=空）。
- 即時：盤中輪詢 Shioaji 快照(報價) + 分 K(即時走勢+量)。未登入正式環境時
  即時區塊優雅降級（回錯誤訊息 dict），其餘三塊照常。

days/industry_map 由高周轉率頁載入時快取後傳入，避免重抓行情視窗。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime

from viewmodels.base_viewmodel import BaseViewModel, ObservableProperty

log = logging.getLogger(__name__)

_MARKET_OPEN = (9, 0)
_MARKET_CLOSE = (13, 35)
_POLL_SEC = 12          # 盤中即時輪詢間隔
_TREND_DAYS = 160       # 日線回溯天數（足夠算 MA20 + 顯示約 90 根）


def _is_market_open(now: datetime | None = None) -> bool:
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    o = now.replace(hour=_MARKET_OPEN[0], minute=_MARKET_OPEN[1],
                    second=0, microsecond=0)
    c = now.replace(hour=_MARKET_CLOSE[0], minute=_MARKET_CLOSE[1],
                    second=0, microsecond=0)
    return o <= now <= c


class MonitorDetailViewModel(BaseViewModel):
    """單檔監控彈窗 ViewModel。"""

    daily_trend = ObservableProperty(None)      # dict | None
    intraday = ObservableProperty(None)         # dict | None（含 error 鍵表降級）
    peers = ObservableProperty(None)            # list[dict] | None（即時排行）
    warrant = ObservableProperty(None)          # dict | None
    quote = ObservableProperty(None)            # dict | None（即時個股報價 KPI）
    status = ObservableProperty("載入中…")

    def __init__(self, stock_code: str, stock_name: str, market: str,
                 date: str, ctx: dict | None, shioaji_svc=None):
        super().__init__()
        self.code = stock_code
        self.name = stock_name
        self.market = market
        self.date = date or datetime.now().strftime("%Y%m%d")
        self._ctx = ctx or {}
        self._sj = shioaji_svc
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    # ------------------------------------------------------------------
    def start(self) -> None:
        # 一次性：日線趨勢、權證多空（EOD）
        # 輪詢：即時報價 + 同類股即時排行（MIS 免登入）、即時分 K（Shioaji，需登入）
        for fn in (self._load_daily, self._load_warrant,
                   self._poll_realtime, self._poll_intraday):
            t = threading.Thread(target=fn, daemon=True)
            t.start()
            self._threads.append(t)

    # ---- 日線趨勢（月線 + 布林 + 量） ----
    def _load_daily(self) -> None:
        from services.db_service import DbService
        from services.turnover_monitor_service import daily_trend
        from datetime import timedelta
        try:
            end_dt = datetime.strptime(self.date, "%Y%m%d")
        except (ValueError, TypeError):
            end_dt = datetime.now()
        start = (end_dt - timedelta(days=_TREND_DAYS)).strftime("%Y-%m-%d")
        end = end_dt.strftime("%Y-%m-%d")
        db = DbService()
        try:
            db.connect()
            prices = db.get_stock_prices(self.code, start, end)
        except Exception as e:  # noqa: BLE001
            log.warning("daily trend load failed %s: %s", self.code, e)
            self.daily_trend = {"error": f"日線載入失敗：{e}"}
            return
        finally:
            try:
                db.close()
            except Exception:
                pass
        self.daily_trend = daily_trend(prices)

    # ---- 即時：個股報價 + 同類股即時成交量排行（MIS 免登入） ----
    def _poll_realtime(self) -> None:
        from services.realtime_quote_service import fetch_quotes
        from services.turnover_monitor_service import industry_members
        days = self._ctx.get("days")
        industry_map = self._ctx.get("industry_map") or {}
        members = industry_members(days, industry_map, self.code, self.market)
        # 一批同時抓：本身 + 同產業成員（同市場 → 同前綴）
        items = [(self.code, self.market)] + [(c, self.market) for c in members]
        first = True
        while not self._stop.is_set():
            try:
                quotes = fetch_quotes(items)
                own = quotes.get(self.code)
                if own:
                    self.quote = own
                peer_q = [q for c, q in quotes.items() if c != self.code]
                peer_q.sort(key=lambda q: q.get("volume_lots") or 0, reverse=True)
                self.peers = [{
                    "code": q["code"], "name": q["name"],
                    "close": q["last"] or 0.0,
                    "volume_lots": q["volume_lots"],
                    "change_pct": (round(q["change_pct"], 2)
                                   if q.get("change_pct") is not None else None),
                } for q in peer_q[:5]]
            except Exception as e:  # noqa: BLE001
                log.warning("realtime poll failed %s: %s", self.code, e)
            if first:
                first = False
                self.status = ("盤中即時更新中…" if _is_market_open()
                               else "非交易時段，顯示最新可得報價；日線/權證為當日資料")
            if not _is_market_open():
                break                 # 收盤：抓一次即停
            if self._stop.wait(_POLL_SEC):
                break

    # ---- 權證多空 ----
    def _load_warrant(self) -> None:
        from services.turnover_monitor_service import warrant_for
        try:
            self.warrant = warrant_for(self.date, self.code, self.name,
                                       self.market)
        except Exception as e:  # noqa: BLE001
            log.warning("warrant load failed %s: %s", self.code, e)
            self.warrant = None

    # ---- 即時分 K 走勢（Shioaji，需登入正式環境） ----
    def _poll_intraday(self) -> None:
        if not self._sj or not self._sj.is_logged_in:
            self.intraday = {"error": "需登入永豐（正式環境）以顯示即時分 K 走勢"}
            return
        try:
            date_dash = datetime.strptime(self.date, "%Y%m%d").strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            date_dash = datetime.now().strftime("%Y-%m-%d")
        while not self._stop.is_set():
            try:
                bars = self._sj.get_intraday_kbars(self.code, date_dash)
                if bars:
                    self.intraday = {"bars": bars}
            except Exception as e:  # noqa: BLE001
                log.warning("intraday poll failed %s: %s", self.code, e)
            if not _is_market_open():
                break                 # 收盤：抓一次即停
            if self._stop.wait(_POLL_SEC):
                break

    def shutdown(self) -> None:
        self._stop.set()
