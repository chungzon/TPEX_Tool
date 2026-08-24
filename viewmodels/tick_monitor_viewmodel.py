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

log = logging.getLogger(__name__)

_BIG_LOTS = 100          # 單筆 >= 此張數視為大單（可調）


class TickMonitorViewModel(BaseViewModel):
    """連次連量多檔即時監控 ViewModel。"""

    rows = ObservableProperty(None)          # list[dict] | None（各檔快照）
    status = ObservableProperty("尚未開始監控")
    is_running = ObservableProperty(False)

    def __init__(self, config=None, shioaji_svc=None):
        super().__init__()
        self._config = config
        self._sj = shioaji_svc
        self._states: dict[str, StreakState] = {}
        self._order: list[str] = []          # 維持輸入順序
        self._lock = threading.Lock()
        self._big_lots = _BIG_LOTS

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
            except Exception:  # noqa: BLE001
                pass
            with self._lock:
                self._states[code] = st
                self._order.append(code)
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
                })
        self.rows = out

    def refresh(self) -> None:
        """定時由 view 呼叫，把最新狀態推到 UI（節流刷新）。"""
        if self.is_running:
            self._emit()

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
