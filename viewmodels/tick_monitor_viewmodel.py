"""ViewModel for 即時神手（連次連量多檔監控）。

輸入多檔股票 → 訂閱 Shioaji 逐筆 → 各檔以 tick_streak_service 累計連次連量、
內外盤、大單，定時把快照推到 UI。需正式環境登入永豐才有即時逐筆；未登入
優雅降級（狀態提示，不崩潰）。tick callback 在 Shioaji socket 執行緒觸發，
狀態更新加鎖；UI 由 view 以 self.after marshal。
"""

from __future__ import annotations

import logging
import re
import threading

from viewmodels.base_viewmodel import BaseViewModel, ObservableProperty
from services.tick_streak_service import StreakState, update
from services import shenshou_record_service as rec

log = logging.getLogger(__name__)

_BIG_LOTS = 100          # 單筆 >= 此張數視為大單（可調）
_AUTOSAVE_EVERY = 75     # refresh 每 N 次自動存檔一次（≈ 800ms × 75 ≈ 60s，防當機遺失）


class TickMonitorViewModel(BaseViewModel):
    """連次連量多檔即時監控 ViewModel。"""

    rows = ObservableProperty(None)          # list[dict] | None（各檔快照）
    status = ObservableProperty("尚未開始監控")
    is_running = ObservableProperty(False)
    records = ObservableProperty(None)       # list[dict] | None（可回放的錄製檔清單）

    def __init__(self, config=None, shioaji_svc=None):
        super().__init__()
        self._config = config
        self._sj = shioaji_svc
        self._states: dict[str, StreakState] = {}
        self._order: list[str] = []          # 維持輸入順序
        self._lock = threading.Lock()
        self._big_lots = _BIG_LOTS
        self._pending_exhaust: dict[str, int] = {}   # 鎖存竭盡供下次 emit 閃燈
        self._recorder: rec.SessionRecorder | None = None   # 目前 session 錄製器
        self._refresh_ticks = 0              # refresh 計數（自動存檔用）
        self.refresh_records()               # 開頁即載入既有錄製檔清單

    # ------------------------------------------------------------------
    def start(self, codes_text: str, big_lots: int = _BIG_LOTS) -> None:
        """解析代碼清單、訂閱逐筆、開始監控。"""
        codes = self._parse_codes(codes_text)
        if not codes:
            self.status = "請輸入至少一個股票代碼"
            return
        if not self._sj or not self._sj.is_logged_in:
            self.status = "需登入永豐（正式環境）才有即時逐筆；請先於「下單」分頁登入"
            return
        self.stop()      # 清掉舊訂閱
        self._big_lots = big_lots
        with self._lock:
            self._states = {}
            self._order = []
        self._recorder = rec.SessionRecorder(big_lots=big_lots)   # 開新錄製 session
        self._refresh_ticks = 0
        subbed, failed = [], []
        for code in codes:
            st = StreakState(code)
            try:
                snap = self._sj.get_snapshot(code)
                if snap:
                    st.open_price = snap.get("open") or 0
                    cl = snap.get("close") or 0
                    chg = snap.get("change_price") or 0
                    # snapshot 無昨收，用 收盤 − 漲跌額 反推
                    st.prev_close = (cl - chg) if cl else 0
                    if cl:
                        st.last_price = cl
                    st.name = snap.get("name") or st.name
            except Exception:  # noqa: BLE001
                pass
            with self._lock:
                self._states[code] = st
                self._order.append(code)
            self._recorder.register(code, name=st.name, prev_close=st.prev_close,
                                    open_price=st.open_price)
            ok = False
            try:
                ok = self._sj.subscribe_quote(
                    code, on_tick=lambda t, c=code: self._on_tick(c, t))
            except Exception as e:  # noqa: BLE001
                log.warning("subscribe %s failed: %s", code, e)
            (subbed if ok else failed).append(code)
        self.is_running = True
        msg = f"監控中：{len(subbed)} 檔"
        if failed:
            msg += f"（訂閱失敗 {len(failed)}：{'、'.join(failed)}）"
        self.status = msg
        self._emit()

    def stop(self) -> None:
        if self._sj and self._sj.is_logged_in:
            for code in list(self._order):
                try:
                    self._sj.unsubscribe_quote(code)
                except Exception:  # noqa: BLE001
                    pass
        self.is_running = False
        # 收盤/停止：把本 session 錄製落地成 JSON，並刷新回放清單
        if self._recorder is not None:
            try:
                paths = self._recorder.save()
                if paths:
                    log.info("神手錄製已存檔：%d 檔", len(paths))
            except Exception as e:  # noqa: BLE001
                log.warning("神手錄製存檔失敗：%s", e)
            self.refresh_records()

    def clear_totals(self) -> None:
        """歸零當日累計（重新統計）。"""
        with self._lock:
            for st in self._states.values():
                st.outer_vol = st.inner_vol = st.total_vol = 0
                st.big_orders = st.last_big = 0
                st.streak_dir = st.streak_count = st.streak_vol = 0
        self._emit()

    # ---- tick 消費（Shioaji socket 執行緒）----
    def _on_tick(self, code: str, tick: dict) -> None:
        try:
            with self._lock:
                st = self._states.get(code)
                if st is None:
                    return
                update(st, tick, big_lots=self._big_lots)
                # 竭盡只在觸發那一筆為非零；節流刷新恐錯過，故鎖存到下次 emit
                if st.exhaust:
                    self._pending_exhaust[code] = st.exhaust
                # 錄製：逐筆軌跡 + 事件（供同頁下方回放江波圖）
                if self._recorder is not None:
                    self._recorder.on_tick(code, tick, st)
        except Exception:  # noqa: BLE001
            log.exception("tick update failed %s", code)

    def _emit(self) -> None:
        """把各檔狀態快照成 list[dict] 推給 UI。"""
        with self._lock:
            out = []
            for code in self._order:
                st = self._states.get(code)
                if st is None:
                    continue
                out.append({
                    "code": st.code, "name": st.name,
                    "price": st.last_price, "change": st.change,
                    "change_pct": round(st.change_pct, 2),
                    "streak_dir": st.streak_dir,
                    "streak_count": st.streak_count,
                    "streak_vol": st.streak_vol,
                    "outer_vol": st.outer_vol, "inner_vol": st.inner_vol,
                    "outer_ratio": round(st.outer_ratio, 1),
                    "total_vol": st.total_vol,
                    "big_orders": st.big_orders, "last_big": st.last_big,
                    "last_side": st.last_side,
                    # 竭盡：取鎖存值（本次刷新閃一下後清除）
                    "exhaust": self._pending_exhaust.pop(st.code, 0),
                })
        self.rows = out

    def refresh(self) -> None:
        """定時由 view 呼叫，把最新狀態推到 UI（節流刷新）。"""
        if self.is_running:
            self._emit()
            # 定期自動存檔（背景執行緒，避免阻塞 UI）
            self._refresh_ticks += 1
            if (self._recorder is not None
                    and self._refresh_ticks % _AUTOSAVE_EVERY == 0):
                threading.Thread(target=self._recorder.save, daemon=True).start()

    # ---- 回放（錄製檔清單 / 讀取）----
    def refresh_records(self) -> None:
        """重新掃描錄製資料夾，更新可回放清單。"""
        try:
            self.records = rec.list_records()
        except Exception as e:  # noqa: BLE001
            log.warning("list shenshou records failed: %s", e)
            self.records = []

    def load_record(self, path: str) -> dict | None:
        """讀取單一錄製檔（供 view 繪製江波圖）。"""
        return rec.load_record(path)

    def live_snapshot(self) -> list[dict]:
        """監控中各檔即時快照（逐筆+事件），供即時江波圖。未在錄製回空清單。"""
        r = self._recorder
        return r.snapshot_all() if r is not None else []

    @staticmethod
    def _parse_codes(text: str) -> list[str]:
        raw = re.split(r"[,\s;、，]+", (text or "").strip())
        seen, out = set(), []
        for c in raw:
            c = c.strip()
            if c and c not in seen:
                seen.add(c)
                out.append(c)
        return out

    def shutdown(self) -> None:
        self.stop()
