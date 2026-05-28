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
    eval_kind = ObservableProperty("breakout") # "breakout" | "short"

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
        chip_filter: bool = False,
        chip_weeks: int | str | None = None,
        chip_big_gain: float | str | None = None,
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

        # Chip-concentration filter params (only validated when enabled)
        cw, big_gain = 4, 0.0
        if chip_filter:
            try:
                cw = self._parse_pos_int(chip_weeks, 4, "比較期週數", 52)
            except ValueError as ex:
                self.error_text = str(ex)
                return
            try:
                big_gain = self._parse_pct(chip_big_gain, 0.0, "大戶上升門檻")
            except ValueError as ex:
                self.error_text = str(ex)
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
        self.eval_kind = "breakout"

        threading.Thread(
            target=self._work,
            args=(otc_codes, s, e, sw, lw, hd, tn,
                   bool(chip_filter), cw, big_gain),
            daemon=True,
        ).start()

    # ------------------------------------------------------------------
    # 策略四：放空當沖 回測
    # ------------------------------------------------------------------

    def start_short_eval(
        self, start_date: str, end_date: str,
        hold_days=None,
        conc_max=None, band_min=None, slope_max=None,
        rank_window=None, bias_min=None, top_n=None,
        bias6_max=None, bias12_max=None,
        bias20_max=None, bias72_max=None,
        use_bias_min: bool = True,
        use_bias6: bool = True,
        use_bias12: bool = True,
        use_bias20: bool = True,
        use_bias72: bool = True,
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

        try:
            hd = self._parse_pos_int(hold_days, 1, "持有日數", self._MAX_HOLD)
            tn = self._parse_pos_int(top_n, 15, "主力家數", self._MAX_TOP_N)
            rw = self._parse_pos_int(rank_window, 60, "位階窗口", 500)
            cm = self._parse_signed_float(conc_max, 0.0, "主10 上限")
            bm = self._parse_signed_float(band_min, 20.0, "帶寬下限", 0.0)
            sm = self._parse_signed_float(slope_max, 0.0, "月斜率上限")
            bmin = self._parse_signed_float(bias_min, 10.0, "年線乖離下限")
            b6 = self._parse_signed_float(bias6_max, -3.0, "周乖離上限")
            b12 = self._parse_signed_float(bias12_max, -4.5, "雙週乖離上限")
            b20 = self._parse_signed_float(bias20_max, -7.0, "月乖離上限")
            b72 = self._parse_signed_float(bias72_max, -11.0, "季乖離上限")
        except ValueError as ex:
            self.error_text = str(ex)
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
        self.eval_kind = "short"

        threading.Thread(
            target=self._work_short,
            args=(otc_codes, s, e, hd, cm, bm, sm, rw, bmin, tn,
                  b6, b12, b20, b72,
                  bool(use_bias_min), bool(use_bias6), bool(use_bias12),
                  bool(use_bias20), bool(use_bias72)),
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

    @staticmethod
    def _parse_signed_float(v, default: float, name: str,
                             lower: float = -1e9, upper: float = 1e9) -> float:
        """空白/None → default；接受負號，範圍預設不卡。"""
        if v is None:
            return default
        raw = str(v).strip()
        if raw == "":
            return default
        try:
            n = float(raw)
        except ValueError:
            raise ValueError(f"{name} 必須是數字（你輸入：{raw}）")
        if n < lower or n > upper:
            raise ValueError(f"{name} 必須介於 {lower:g}–{upper:g}")
        return n

    @staticmethod
    def _parse_pct(v, default: float, name: str,
                    lower: float = 0.0, upper: float = 100.0) -> float:
        """空白/None → default；否則必須是 lower..upper 的浮點數。"""
        if v is None:
            return default
        raw = str(v).strip()
        if raw == "":
            return default
        try:
            n = float(raw)
        except ValueError:
            raise ValueError(f"{name} 必須是數字（你輸入：{raw}）")
        if n < lower or n > upper:
            raise ValueError(f"{name} 必須介於 {lower}–{upper}")
        return n

    def cancel(self) -> None:
        self._cancel = True

    # ------------------------------------------------------------------

    def _work(self, codes: list[str], start_date: str, end_date: str,
              short_window: int, long_window: int,
              hold_days: int, top_n: int,
              chip_filter: bool, chip_weeks: int,
              chip_big_gain: float) -> None:
        from services.db_service import DbService
        from services.strategy_eval_service import (
            detect_breakout_signals, summarise, signals_to_dicts,
            chip_change_at_date, chip_concentration_passes,
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
                f"（不限正負），持有 {hold_days} 個交易日，"
                f"主力取前 {top_n} 家\n"
            )
            if chip_filter:
                self._log(
                    f"籌碼過濾：大戶 ≥ +{chip_big_gain:g}%"
                    f"（比對 {chip_weeks} 週前）\n"
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

            # ---- 籌碼過濾（大戶減少 + 散戶增加） ----
            chip_skipped = 0
            if chip_filter and all_signals:
                uniq_codes = list({s.stock_code for s in all_signals})
                self._log(
                    f"取得 {len(uniq_codes)} 檔的 TDCC 週報以套用籌碼過濾...\n"
                )
                dist_map = db.get_distribution_summary_for_codes(uniq_codes)
                pre = len(all_signals)
                kept: list = []
                for s in all_signals:
                    hist = dist_map.get(s.stock_code, [])
                    info = chip_change_at_date(hist, s.signal_date, chip_weeks)
                    if info is None:
                        # 沒週報可比對 → 啟用過濾時排除
                        chip_skipped += 1
                        continue
                    if not chip_concentration_passes(info, chip_big_gain):
                        continue
                    s.chip_big_delta = info["big_delta"]
                    s.chip_retail_delta = info["retail_delta"]
                    s.chip_latest_date = info["latest_date"]
                    s.chip_earlier_date = info["earlier_date"]
                    kept.append(s)
                self._log(
                    f"籌碼過濾：{pre} → {len(kept)} 筆（其中 "
                    f"{chip_skipped} 筆缺週報資料）\n"
                )
                all_signals = kept

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

    def _work_short(self, codes: list[str], start_date: str, end_date: str,
                     hold_days: int, conc_max: float, band_min: float,
                     slope_max: float, rank_window: int, bias_min: float,
                     top_n: int,
                     bias6_max: float, bias12_max: float,
                     bias20_max: float, bias72_max: float,
                     use_bias_min: bool, use_bias6: bool, use_bias12: bool,
                     use_bias20: bool, use_bias72: bool) -> None:
        from collections import defaultdict
        from dataclasses import asdict
        from services.db_service import DbService
        from services.strategy_eval_service import (
            backtest_short_daytrade, summarise,
        )

        db = DbService()
        try:
            db.connect()
            self._log("策略四（放空當沖）回測\n")
            self._log(
                f"持有 {hold_days} 日；主10<{conc_max:g}、"
                f"帶寬>{band_min:g}、月斜率<{slope_max:g}\n"
            )
            bias_lines = []
            if use_bias_min:
                bias_lines.append(f"年線≥{bias_min:g}%")
            if use_bias6:
                bias_lines.append(f"周乖≤{bias6_max:g}%")
            if use_bias12:
                bias_lines.append(f"雙週乖≤{bias12_max:g}%")
            if use_bias20:
                bias_lines.append(f"月乖≤{bias20_max:g}%")
            if use_bias72:
                bias_lines.append(f"季乖≤{bias72_max:g}%")
            if bias_lines:
                self._log(f"乖離條件：{'、'.join(bias_lines)}\n")
            else:
                self._log("乖離條件：（未啟用）\n")
            self._log(
                f"範圍：{start_date} ~ {end_date}，共 {len(codes)} 檔上櫃股票\n"
            )
            self._log("─" * 44 + "\n")

            # 算回溯範圍
            sdt = datetime.strptime(start_date, "%Y-%m-%d")
            edt = datetime.strptime(end_date, "%Y-%m-%d")
            broker_start = (sdt - timedelta(days=20)).strftime("%Y-%m-%d")
            price_start = (sdt - timedelta(days=420)).strftime("%Y-%m-%d")
            # 出場端：end_date + 持有交易日 * 1.6 緩衝
            exit_end = (edt + timedelta(days=hold_days * 2 + 7)
                        ).strftime("%Y-%m-%d")

            codes_set = set(codes)
            name_map = db.get_stock_names(codes)

            self.status_text = "載入分點歷史..."
            broker_rows = db.get_broker_history_range(broker_start, end_date)
            if not broker_rows:
                self.error_text = "查無分點資料"
                return
            broker_grouped: dict[str, list[dict]] = defaultdict(list)
            for r in broker_rows:
                if r["stock_code"] in codes_set:
                    if not r.get("stock_name"):
                        r["stock_name"] = name_map.get(
                            r["stock_code"], r["stock_code"])
                    broker_grouped[r["stock_code"]].append(r)
            self._log(
                f"分點資料：{len(broker_rows):,} 筆 → "
                f"{len(broker_grouped)} 檔\n"
            )

            self.status_text = "載入價格歷史..."
            price_map_raw = db.get_all_prices_range(price_start, exit_end)
            price_map = {c: p for c, p in price_map_raw.items()
                         if c in codes_set}
            if not price_map:
                self.error_text = "查無價格資料"
                return
            n_price = sum(len(p) for p in price_map.values())
            self._log(f"價格資料：{n_price:,} 筆\n")

            # 算 [start, end] 內的交易日（從價格資料找）
            all_dates = set()
            for prices in price_map.values():
                for p in prices:
                    d = str(p["trade_date"])[:10]
                    if start_date <= d <= end_date:
                        all_dates.add(d)
            trading_dates = sorted(all_dates)
            self._log(f"交易日：{len(trading_dates)} 天\n")
            self._log("─" * 44 + "\n")

            if self._cancel:
                self._log("（已取消）\n")
                self.status_text = "已取消"
                return

            def _progress(done, total):
                self.progress = done / total if total else 1.0
                self.progress_text = f"{done} / {total}"
                self.status_text = f"回測中 {done}/{total} 個交易日"

            def _cancelled():
                return self._cancel

            results = backtest_short_daytrade(
                broker_grouped, price_map, trading_dates,
                hold_days=hold_days,
                top_n=top_n,
                conc_max=conc_max, band_min=band_min,
                slope_max=slope_max, rank_window=rank_window,
                bias_min=bias_min,
                bias6_max=bias6_max, bias12_max=bias12_max,
                bias20_max=bias20_max, bias72_max=bias72_max,
                use_bias_min=use_bias_min,
                use_bias6=use_bias6, use_bias12=use_bias12,
                use_bias20=use_bias20, use_bias72=use_bias72,
                cancel_flag=_cancelled, progress_cb=_progress,
            )

            results.sort(key=lambda s: s.signal_date, reverse=True)
            summary = summarise(results)

            self._log("─" * 44 + "\n")
            self._log(
                f"完成：訊號 {summary['count']} 筆，勝率 "
                f"{summary['win_rate']}%，平均報酬 "
                f"{summary['avg_return']:+.2f}%\n"
            )
            self._log(
                f"最佳 {summary['best']:+.2f}%　最差 "
                f"{summary['worst']:+.2f}%　期望值 "
                f"{summary['expectancy']:+.2f}%\n"
            )

            self.signals_data = [asdict(s) for s in results]
            self.summary_data = summary
            self.status_text = (
                "完成" if not self._cancel else "已取消"
            )
        except Exception as e:
            self.error_text = str(e)
            self.status_text = f"錯誤：{e}"
            self._log(f"\n致命錯誤：{e}\n")
            log.exception("Short eval failed")
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
