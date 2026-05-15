"""ViewModel for the 補資料 (back-fill) tab.

補抓指定日期的上市/上櫃「每日行情」與「三大法人」資料，用於排程
漏跑某天時事後補齊。分點明細無法補抓（資料源僅提供最新交易日）。
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime

from viewmodels.base_viewmodel import BaseViewModel, ObservableProperty

log = logging.getLogger(__name__)


class BackfillViewModel(BaseViewModel):
    """補資料分頁的 ViewModel。"""

    status_text = ObservableProperty("就緒")
    log_text = ObservableProperty("")
    is_running = ObservableProperty(False)
    progress = ObservableProperty(0.0)
    progress_text = ObservableProperty("")
    error_text = ObservableProperty("")

    def start_backfill(self, date_str: str, do_otc: bool, do_twse: bool,
                       do_market: bool, do_insti: bool) -> None:
        """驗證輸入後在背景執行緒補抓資料。"""
        if self.is_running:
            return

        date_str = (date_str or "").strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
            self.error_text = "日期格式錯誤，請用 yyyy-mm-dd"
            return
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            self.error_text = "日期無效"
            return
        if dt.date() > datetime.now().date():
            self.error_text = "不能補未來的日期"
            return
        if not (do_otc or do_twse):
            self.error_text = "請至少勾選一個市場（上市／上櫃）"
            return
        if not (do_market or do_insti):
            self.error_text = "請至少勾選一種資料（每日行情／三大法人）"
            return

        self.error_text = ""
        self.is_running = True
        self.log_text = ""
        self.progress = 0.0
        self.progress_text = ""
        if dt.weekday() >= 5:
            self._log(f"⚠ {date_str} 是週末，通常無交易資料\n")

        threading.Thread(
            target=self._work,
            args=(date_str, do_otc, do_twse, do_market, do_insti),
            daemon=True,
        ).start()

    # ------------------------------------------------------------------

    def _work(self, date_str: str, do_otc: bool, do_twse: bool,
              do_market: bool, do_insti: bool) -> None:
        from services.db_service import DbService

        tasks: list[tuple[str, callable]] = []
        if do_market and do_otc:
            tasks.append(("上櫃每日行情", self._fill_otc_market))
        if do_market and do_twse:
            tasks.append(("上市每日行情", self._fill_twse_market))
        if do_insti and do_otc:
            tasks.append(("上櫃三大法人", self._fill_otc_insti))
        if do_insti and do_twse:
            tasks.append(("上市三大法人", self._fill_twse_insti))

        db = DbService()
        total = len(tasks)
        done = 0
        try:
            db.connect()
            db.ensure_tables()
            self._log(f"目標日期：{date_str}　共 {total} 項作業\n")
            self._log("─" * 44 + "\n")

            for label, fn in tasks:
                self.status_text = f"{label} 下載中..."
                self._log(f"▶ {label} ...\n")
                try:
                    saved, fetched, note = fn(db, date_str)
                    extra = f"（{note}）" if note else ""
                    self._log(
                        f"  ✓ {label}：抓取 {fetched} 檔，"
                        f"寫入 {saved} 筆{extra}\n"
                    )
                except Exception as e:
                    self._log(f"  ✗ {label} 失敗：{e}\n")
                    log.exception("Backfill task failed: %s", label)
                done += 1
                self.progress = done / total if total else 1.0
                self.progress_text = f"{done} / {total}"

            self._log("─" * 44 + "\n")
            self._log("補資料完成\n")
            self.status_text = "完成"
        except Exception as e:
            self.error_text = str(e)
            self.status_text = f"錯誤：{e}"
            self._log(f"\n致命錯誤：{e}\n")
            log.exception("Backfill failed")
        finally:
            try:
                db.close()
            except Exception:
                pass
            self.is_running = False

    # -- individual tasks: return (saved, fetched, note) ----------------

    @staticmethod
    def _date_note(actual: str, requested: str) -> str:
        """若 API 實際回傳日期與要求不同（補到非交易日）則提示。"""
        actual = str(actual)[:10]
        if actual and actual != requested:
            return f"API 實際回傳 {actual}，非該日資料"
        return ""

    def _fill_otc_market(self, db, date_str: str):
        from services.backfill_service import fetch_otc_daily
        rows = fetch_otc_daily(date_str)
        if not rows:
            return 0, 0, "無資料（可能非交易日）"
        note = self._date_note(rows[0]["trade_date"], date_str)
        saved = db.save_daily_summary_batch(rows)
        return saved, len(rows), note

    def _fill_twse_market(self, db, date_str: str):
        from services.backfill_service import fetch_twse_daily
        rows = fetch_twse_daily(date_str)
        if not rows:
            return 0, 0, "無資料（可能非交易日）"
        note = self._date_note(rows[0]["trade_date"], date_str)
        saved = db.save_daily_summary_batch(rows)
        return saved, len(rows), note

    def _fill_otc_insti(self, db, date_str: str):
        from services.insti_service import fetch_insti_daily
        rows = fetch_insti_daily(date_str)
        if not rows:
            return 0, 0, "無資料（可能非交易日）"
        note = self._date_note(rows[0].trade_date, date_str)
        saved = db.save_insti_daily_batch(rows)
        return saved, len(rows), note

    def _fill_twse_insti(self, db, date_str: str):
        from services.twse_api_service import fetch_twse_insti_daily
        from services.insti_service import InstiDaily
        raw = fetch_twse_insti_daily(date_str)
        if not raw:
            return 0, 0, "無資料（可能非交易日）"
        objs = [InstiDaily(**r) for r in raw]
        note = self._date_note(objs[0].trade_date, date_str)
        saved = db.save_insti_daily_batch(objs)
        return saved, len(objs), note

    # ------------------------------------------------------------------

    def _log(self, text: str) -> None:
        self.log_text = (self.log_text or "") + text

    def shutdown(self) -> None:
        pass
