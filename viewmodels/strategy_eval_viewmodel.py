"""ViewModel for the 效益評估 tab.

對 ConfigService 的上櫃股票清單跑「主力集中度突破策略」回測，彙總每檔
的訊號與後續報酬。
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timedelta

from viewmodels.base_viewmodel import BaseViewModel, ObservableProperty

log = logging.getLogger(__name__)


class StrategyEvalViewModel(BaseViewModel):
    """效益評估分頁的 ViewModel。"""

    status_text = ObservableProperty("就緒")
    log_text = ObservableProperty("")
    is_running = ObservableProperty(False)
    progress = ObservableProperty(0.0)
    progress_text = ObservableProperty("")
    error_text = ObservableProperty("")
    signals_data = ObservableProperty(None)    # list[dict] | None
    summary_data = ObservableProperty(None)    # dict | None

    # 策略參數預設值（也是 UI 預填值）
    DEFAULT_SHORT_WINDOW = 5
    DEFAULT_LONG_WINDOW = 15
    DEFAULT_HOLD_DAYS = 4
    DEFAULT_TOP_N = 15

    # 參數合理上限（避免不合理輸入造成效能問題）
    _MAX_WINDOW = 250
    _MAX_HOLD = 120
    _MAX_TOP_N = 100

    def __init__(self, config_svc):
        super().__init__()
        self._config = config_svc
        self._cancel = False

    # ------------------------------------------------------------------

    def start_eval(
        self, start_date: str, end_date: str,
        short_window: int | str | None = None,
        long_window: int | str | None = None,
        hold_days: int | str | None = None,
        top_n: int | str | None = None,
    ) -> None:
        if self.is_running:
            return
        s = (start_date or "").strip()
        e = (end_date or "").strip()
        if not (re.match(r"^\d{4}-\d{2}-\d{2}$", s)
                and re.match(r"^\d{4}-\d{2}-\d{2}$", e)):
            self.error_text = "日期格式錯誤，請用 yyyy-mm-dd"
            return
        try:
            sdt = datetime.strptime(s, "%Y-%m-%d")
            edt = datetime.strptime(e, "%Y-%m-%d")
        except ValueError:
            self.error_text = "日期無效"
            return
        if sdt > edt:
            self.error_text = "起始日不能晚於結束日"
            return

        # ---- Strategy params ----
        try:
            sw = self._parse_pos_int(short_window, self.DEFAULT_SHORT_WINDOW,
                                     "短期窗口", self._MAX_WINDOW)
            lw = self._parse_pos_int(long_window, self.DEFAULT_LONG_WINDOW,
                                     "長期窗口", self._MAX_WINDOW)
            hd = self._parse_pos_int(hold_days, self.DEFAULT_HOLD_DAYS,
                                     "持有日數", self._MAX_HOLD)
            tn = self._parse_pos_int(top_n, self.DEFAULT_TOP_N,
                                     "主力家數", self._MAX_TOP_N)
        except ValueError as ex:
            self.error_text = str(ex)
            return
        if sw >= lw:
            self.error_text = f"短期窗口（{sw}）必須小於長期窗口（{lw}）"
            return

        otc_codes = self._config.get("stock_codes") or []
        if not otc_codes:
            self.error_text = "尚未設定上櫃股票清單，請至「系統設定」按更新清單"
            return

        self.error_text = ""
        self.is_running = True
        self._cancel = False
        self.log_text = ""
        self.signals_data = None
        self.summary_data = None
        self.progress = 0.0
        self.progress_text = ""

        threading.Thread(
            target=self._work,
            args=(otc_codes, s, e, sw, lw, hd, tn),
            daemon=True,
        ).start()

    @staticmethod
    def _parse_pos_int(v, default: int, name: str, upper: int) -> int:
        """空白 / None → default；否則必須是 1..upper 的整數。"""
        if v is None:
            return default
        raw = str(v).strip()
        if raw == "":
            return default
        if not raw.lstrip("+").isdigit():
            raise ValueError(f"{name} 必須是正整數（你輸入：{raw}）")
        n = int(raw)
        if n < 1:
            raise ValueError(f"{name} 必須 ≥ 1")
        if n > upper:
            raise ValueError(f"{name} 不能超過 {upper}")
        return n

    def cancel(self) -> None:
        self._cancel = True

    # ------------------------------------------------------------------

    def _work(self, codes: list[str], start_date: str, end_date: str,
              short_window: int, long_window: int,
              hold_days: int, top_n: int) -> None:
        from services.db_service import DbService
        from services.strategy_eval_service import (
            detect_breakout_signals, summarise, signals_to_dicts,
        )

        db = DbService()
        all_signals = []
        scanned = 0
        with_data = 0
        with_signal = 0

        try:
            db.connect()
            total = len(codes)
            self._log(
                f"策略：{short_window}日集中度上穿 {long_window}日集中度"
                f"（雙正），持有 {hold_days} 個交易日，主力取前 {top_n} 家\n"
            )
            self._log(f"範圍：{start_date} ~ {end_date}，"
                      f"共 {total} 檔上櫃股票\n")
            self._log("─" * 44 + "\n")

            name_map = db.get_stock_names(codes)

            for idx, code in enumerate(codes, 1):
                if self._cancel:
                    self._log("（已取消）\n")
                    break

                self.status_text = f"分析中 {code}（{idx}/{total}）"
                try:
                    rows = db.get_all_brokers_daily(
                        code, start_date, end_date)
                    if not rows:
                        scanned += 1
                        self._update_progress(idx, total)
                        continue
                    with_data += 1
                    sigs = detect_breakout_signals(
                        rows, code, name_map.get(code, code),
                        short_window=short_window,
                        long_window=long_window,
                        hold_days=hold_days,
                        top_n=top_n,
                    )
                    if sigs:
                        with_signal += 1
                        all_signals.extend(sigs)
                except Exception as e:
                    self._log(f"  ✗ {code} 錯誤：{e}\n")
                    log.exception("Eval failed for %s", code)

                scanned += 1
                self._update_progress(idx, total)

            # 排序：最新訊號在前
            all_signals.sort(key=lambda s: s.signal_date, reverse=True)
            summary = summarise(all_signals)

            self._log("─" * 44 + "\n")
            self._log(
                f"完成：掃描 {scanned} 檔，有資料 {with_data} 檔，"
                f"出現訊號 {with_signal} 檔\n"
            )
            self._log(
                f"訊號數 {summary['count']}　勝率 "
                f"{summary['win_rate']}%　平均報酬 "
                f"{summary['avg_return']:+.2f}%\n"
            )
            self._log(
                f"最佳 {summary['best']:+.2f}%　最差 "
                f"{summary['worst']:+.2f}%　期望值 "
                f"{summary['expectancy']:+.2f}%\n"
            )

            self.signals_data = signals_to_dicts(all_signals)
            self.summary_data = summary
            self.status_text = (
                "完成" if not self._cancel else "已取消"
            )

        except Exception as e:
            self.error_text = str(e)
            self.status_text = f"錯誤：{e}"
            self._log(f"\n致命錯誤：{e}\n")
            log.exception("Strategy eval failed")
        finally:
            try:
                db.close()
            except Exception:
                pass
            self.is_running = False

    # ------------------------------------------------------------------

    def _update_progress(self, done: int, total: int) -> None:
        self.progress = done / total if total else 1.0
        self.progress_text = f"{done} / {total}"

    def _log(self, text: str) -> None:
        self.log_text = (self.log_text or "") + text

    def shutdown(self) -> None:
        self._cancel = True


def default_date_range() -> tuple[str, str]:
    """預設範圍：近 1 年。"""
    end = datetime.now().date()
    start = end - timedelta(days=365)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
