"""ViewModel for 隔日沖監控。

Phase 1（本檔）：載入前一交易日「自營避險買入」前 N 名候選標的（含名稱／三大法人／
主力成本／漲跌幅／權證多空），供使用者勾選要盤中即時監控的標的。

Phase 2（後續）：對勾選標的以 Shioaji 訂閱標的 + 其權證逐筆，即時彙總權證買賣壓／
漲跌，提早偵測自營避險部位鬆動（準備賣股）。事件錄製供回放。

長工作跑背景執行緒；view 以 self.after marshal 回 UI 執行緒。
"""

from __future__ import annotations

import logging
import threading

from viewmodels.base_viewmodel import BaseViewModel, ObservableProperty

log = logging.getLogger(__name__)

_K_PER_STOCK = 6          # 每檔標的訂閱最活躍前 N 檔權證（受 Shioaji 訂閱數限制）
_AUTOSAVE_EVERY = 60      # refresh 每 N 次自動存檔（≈ 1s × 60 ≈ 60s）


class NextDayMonitorViewModel(BaseViewModel):
    """隔日沖監控 ViewModel。"""

    candidates = ObservableProperty(None)     # dict | None（{date, prev_date, rows}）
    status = ObservableProperty("按「載入候選」帶入前一交易日自營避險買入排行")
    monitor_rows = ObservableProperty(None)   # list[dict] | None（各標的權證即時彙總）
    monitor_status = ObservableProperty("")
    monitoring = ObservableProperty(False)
    records = ObservableProperty(None)        # list[dict] | None（可回放的錄製檔）

    def __init__(self, shioaji_svc=None):
        super().__init__()
        self._sj = shioaji_svc
        self._selected: set[str] = set()      # 勾選監控的代碼
        self._lock = threading.Lock()
        self._aggs: dict = {}                 # code -> WarrantAgg
        self._route: dict = {}                # 權證代號 -> (標的代碼, side)
        self._und_price: dict = {}            # 標的代碼 -> 現價
        self._und_ref: dict = {}              # 標的代碼 -> 參考價(昨收)
        self._mon_order: list[str] = []       # 監控標的顯示順序
        self._subscribed: list[str] = []      # 已訂閱代碼（權證+標的）
        self._recorder = None                 # NextDayRecorder（本 session 錄製）
        self._refresh_ticks = 0
        self.refresh_records()

    # ------------------------------------------------------------------
    def load(self, date: str | None = None) -> None:
        threading.Thread(target=self._load, args=(date,), daemon=True).start()

    def _load(self, date: str | None) -> None:
        from services.db_service import DbService
        from services import nextday_hedge_service as nh
        self.status = "載入候選中…"
        db = DbService()
        try:
            db.connect()
            res = nh.load_candidates(db, date=date, top_n=40)
        except Exception as e:  # noqa: BLE001
            log.warning("load candidates failed: %s", e)
            self.status = f"載入失敗：{e}"
            return
        finally:
            try:
                db.close()
            except Exception:
                pass
        self.candidates = res
        d = res.get("date")
        rows = res.get("rows") or []
        if rows:
            self.status = (f"前一交易日 {d}：自營避險買入前 {len(rows)} 名"
                           f"（勾選標的後即可盤中即時監控權證買賣壓）")
        else:
            self.status = f"查無資料（{d or '—'}）；請先於系統設定/補資料下載三大法人資料"

    # ---- 選取管理 ----
    def toggle(self, code: str, on: bool) -> None:
        if on:
            self._selected.add(code)
        else:
            self._selected.discard(code)

    @property
    def selected(self) -> list[str]:
        return sorted(self._selected)

    # ---- 盤中即時權證監控 ----
    def start_monitor(self) -> None:
        threading.Thread(target=self._start_monitor, daemon=True).start()

    def _start_monitor(self) -> None:
        from services import warrant_monitor_service as wm
        codes = self.selected
        if not codes:
            self.monitor_status = "請先勾選至少一檔標的"
            return
        if not self._sj or not self._sj.is_logged_in:
            self.monitor_status = "需登入永豐（正式環境）才有權證即時逐筆；請先於「下單」分頁登入"
            return
        self.stop_monitor()
        self.monitor_status = "解析權證清單並訂閱中…"
        cand = {r["stock_code"]: r for r in (self.candidates or {}).get("rows", [])}
        date = (self.candidates or {}).get("date")
        date_c = date.replace("-", "") if date else None

        aggs: dict = {}
        route: dict = {}
        picks: list[tuple] = []               # (權證代號, side, 標的代碼)
        for code in codes:
            name = cand.get(code, {}).get("name", "")
            aggs[code] = wm.WarrantAgg(code, name)
            try:
                chosen = wm.pick_warrants(code, name, date_c, k=_K_PER_STOCK)
            except Exception as e:  # noqa: BLE001
                log.warning("pick_warrants %s failed: %s", code, e)
                chosen = []
            for wc, side in chosen:
                route[wc] = (code, side)
                picks.append((wc, side, code))

        # 批次快照：權證參考價 + 標的參考價
        all_codes = [wc for wc, _, _ in picks] + list(codes)
        snaps = {}
        try:
            snaps = self._sj.get_snapshots(all_codes)
        except Exception as e:  # noqa: BLE001
            log.warning("snapshots failed: %s", e)
        for wc, side, code in picks:
            s = snaps.get(wc, {})
            close = s.get("close") or 0.0
            chg = s.get("change_price") or 0.0
            aggs[code].add_warrant(wc, side, ref=(close - chg) if close else 0.0,
                                   last=close)
        und_price, und_ref = {}, {}
        for code in codes:
            s = snaps.get(code, {})
            close = s.get("close") or 0.0
            chg = s.get("change_price") or 0.0
            und_price[code] = close
            und_ref[code] = (close - chg) if close else 0.0

        # 事件錄製器：註冊各標的（含主力成本、昨收）
        from services import nextday_record_service as nr
        recorder = nr.NextDayRecorder(date=date)
        for code in codes:
            recorder.register(code, name=cand.get(code, {}).get("name", ""),
                              main_cost=cand.get(code, {}).get("main_buy_cost")
                              or 0.0, prev_close=und_ref.get(code, 0.0))

        with self._lock:
            self._aggs = aggs
            self._route = route
            self._und_price = und_price
            self._und_ref = und_ref
            self._mon_order = list(codes)
            self._recorder = recorder
            self._refresh_ticks = 0

        # 訂閱權證 + 標的逐筆
        subbed: list[str] = []
        for wc, _, _ in picks:
            try:
                if self._sj.subscribe_quote(
                        wc, on_tick=lambda t, c=wc: self._on_warrant_tick(c, t)):
                    subbed.append(wc)
            except Exception as e:  # noqa: BLE001
                log.warning("subscribe warrant %s failed: %s", wc, e)
        for code in codes:
            try:
                if self._sj.subscribe_quote(
                        code, on_tick=lambda t, c=code: self._on_underlying_tick(c, t)):
                    subbed.append(code)
            except Exception as e:  # noqa: BLE001
                log.warning("subscribe underlying %s failed: %s", code, e)
        self._subscribed = subbed
        self.monitoring = True
        self.monitor_status = (f"即時監控中：{len(codes)} 檔標的 / {len(picks)} 檔權證"
                               f"（自營賣壓＝認售淨買−認購淨買，正值偏賣股）")
        self._emit_monitor()

    def stop_monitor(self) -> None:
        if self._sj and self._sj.is_logged_in:
            for c in list(self._subscribed):
                try:
                    self._sj.unsubscribe_quote(c)
                except Exception:  # noqa: BLE001
                    pass
        self._subscribed = []
        self.monitoring = False
        # 收盤/停止：事件錄製落地並刷新回放清單
        if self._recorder is not None:
            try:
                paths = self._recorder.save()
                if paths:
                    log.info("隔日沖錄製已存檔：%d 檔", len(paths))
            except Exception as e:  # noqa: BLE001
                log.warning("隔日沖錄製存檔失敗：%s", e)
            self.refresh_records()

    def _on_warrant_tick(self, wcode: str, tick: dict) -> None:
        try:
            with self._lock:
                r = self._route.get(wcode)
                if not r:
                    return
                agg = self._aggs.get(r[0])
                if agg is not None:
                    agg.on_tick(wcode, tick)
        except Exception:  # noqa: BLE001
            log.exception("warrant tick failed %s", wcode)

    def _on_underlying_tick(self, code: str, tick: dict) -> None:
        p = tick.get("close") or tick.get("price")
        if p:
            self._und_price[code] = float(p)

    def refresh_monitor(self) -> None:
        if not self.monitoring:
            return
        self._emit_monitor()
        # 定期自動存檔（背景執行緒，避免阻塞 UI）
        self._refresh_ticks += 1
        if (self._recorder is not None
                and self._refresh_ticks % _AUTOSAVE_EVERY == 0):
            threading.Thread(target=self._recorder.save, daemon=True).start()

    def _emit_monitor(self) -> None:
        with self._lock:
            out = []
            for code in self._mon_order:
                agg = self._aggs.get(code)
                if agg is None:
                    continue
                s = agg.snapshot()
                p = self._und_price.get(code, 0.0)
                ref = self._und_ref.get(code, 0.0)
                s["price"] = p
                s["change_pct"] = (round((p - ref) / ref * 100, 2)
                                   if (p and ref) else None)
                out.append(s)
        # 錄製（於鎖外呼叫，recorder 自帶鎖）
        if self._recorder is not None:
            for s in out:
                self._recorder.on_update(s["code"], s)
        self.monitor_rows = out

    # ---- 回放 ----
    def refresh_records(self) -> None:
        from services import nextday_record_service as nr
        try:
            self.records = nr.list_records()
        except Exception as e:  # noqa: BLE001
            log.warning("list nextday records failed: %s", e)
            self.records = []

    def load_record(self, path: str) -> dict | None:
        from services import nextday_record_service as nr
        return nr.load_record(path)

    def shutdown(self) -> None:
        self.stop_monitor()
