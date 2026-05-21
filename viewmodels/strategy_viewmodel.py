"""ViewModel for strategy screening (策略篩選)."""

from __future__ import annotations

import threading
from collections import defaultdict

from viewmodels.base_viewmodel import BaseViewModel, ObservableProperty
from services.db_service import DbService
from services.broker_tags import get_broker_tags, is_dealer_hq, TAG_NEXT


class StrategyViewModel(BaseViewModel):

    results = ObservableProperty(None)       # list[dict] | None
    loading = ObservableProperty(False)
    error_text = ObservableProperty("")
    status_text = ObservableProperty("")

    def __init__(self):
        super().__init__()
        self._db = DbService()

    def run_dealer_hedge_strategy(
        self, trade_date: str,
        hedge_pct_min: float = 10.0,
        buy_amount_min: float = 10_000_000,
        next_day_pct_min: float = 10.0,
    ):
        """Screen stocks: dealer hedge ratio >= X% AND buy amount >= Y
        AND next-day-flip broker buy ratio >= Z%."""
        trade_date = trade_date.strip()
        if not trade_date:
            self.error_text = "請輸入日期"
            return
        if self.loading:
            return
        self.loading = True
        self.error_text = ""
        self.results = None
        self.status_text = ""

        def _work():
            try:
                self._db.connect()
                self._db.ensure_tables()

                # Get all broker data for the date
                broker_rows = self._db.get_all_broker_buys_by_date(trade_date)
                if not broker_rows:
                    self.error_text = f"{trade_date} 無分點資料（可能非交易日或尚未下載）"
                    self.results = []
                    return

                # Compute per-stock: dealer HQ ratio, next-day ratio, amounts
                stock_stats = self._calc_stock_stats(broker_rows)

                # Filter by all conditions
                filtered = []
                for code, s in stock_stats.items():
                    if (s["dealer_pct"] >= hedge_pct_min
                            and s["dealer_buy_amount"] >= buy_amount_min
                            and s["next_day_pct"] >= next_day_pct_min):
                        filtered.append(s)

                filtered.sort(key=lambda x: x["dealer_pct"], reverse=True)

                if not filtered:
                    self.error_text = (
                        f"{trade_date} 無符合所有條件的標的"
                        f"（自營比≥{hedge_pct_min}% + "
                        f"買超≥{buy_amount_min/10000:.0f}萬 + "
                        f"隔沖≥{next_day_pct_min}%）"
                    )
                else:
                    self.status_text = f"找到 {len(filtered)} 檔符合條件"
                self.results = filtered
            except Exception as e:
                self.error_text = f"查詢錯誤：{e}"
            finally:
                self.loading = False

        threading.Thread(target=_work, daemon=True).start()

    @staticmethod
    def _calc_stock_stats(broker_rows: list[dict]) -> dict[str, dict]:
        """Compute dealer HQ ratio and next-day-flip ratio per stock."""

        def _pp(v) -> float:
            try:
                return float(str(v).replace(",", ""))
            except (ValueError, TypeError):
                return 0.0

        stocks: dict[str, dict] = {}
        for r in broker_rows:
            code = r["stock_code"]
            if code not in stocks:
                stocks[code] = {
                    "stock_code": code,
                    "stock_name": r["stock_name"],
                    "close_price": _pp(r["close_price"]),
                    "total_vol": 0,        # all broker buy+sell
                    "dealer_net": 0,       # dealer HQ net buy
                    "dealer_buy": 0,       # dealer HQ buy
                    "next_day_net": 0,     # next-day-flip net buy
                }
            s = stocks[code]
            bv = r["buy_volume"] or 0
            sv = r["sell_volume"] or 0
            s["total_vol"] += bv + sv
            net = bv - sv
            name = r["broker_name"]

            # Dealer HQ (自營商總部)
            if is_dealer_hq(name):
                s["dealer_buy"] += bv
                if net > 0:
                    s["dealer_net"] += net

            # Next-day flip (隔日沖)
            if net > 0 and TAG_NEXT in get_broker_tags(name):
                s["next_day_net"] += net

        # Compute ratios
        result: dict[str, dict] = {}
        for code, s in stocks.items():
            tv = s["total_vol"]
            if tv <= 0 or s["close_price"] <= 0:
                continue
            result[code] = {
                "stock_code": code,
                "stock_name": s["stock_name"],
                "close_price": s["close_price"],
                "dealer_pct": round(s["dealer_net"] / tv * 100, 2),
                "dealer_buy": s["dealer_buy"],
                "dealer_buy_amount": s["dealer_buy"] * s["close_price"],
                "dealer_net": s["dealer_net"],
                "next_day_pct": round(s["next_day_net"] / tv * 100, 2),
            }
        return result

    def run_bollinger_strategy(
        self, trade_date: str,
        bb_period: int = 20,
        bb_k: float = 2.0,
        dealer_buy_min: int = 0,
    ):
        """Screen stocks: close > BB upper AND dealer HQ net buy > 0."""
        trade_date = trade_date.strip()
        if not trade_date:
            self.error_text = "請輸入日期"
            return
        if self.loading:
            return
        self.loading = True
        self.error_text = ""
        self.results = None
        self.status_text = ""

        def _work():
            try:
                import numpy as np

                self._db.connect()
                self._db.ensure_tables()

                # 1. Get recent prices for BB calculation
                price_map = self._db.get_all_stocks_recent_prices(
                    trade_date, lookback=bb_period + 5)

                # 2. Get broker data for dealer HQ check
                broker_rows = self._db.get_all_broker_buys_by_date(trade_date)
                if not broker_rows:
                    self.error_text = f"{trade_date} 無分點資料"
                    self.results = []
                    return

                stock_stats = self._calc_stock_stats(broker_rows)

                # 3. Filter: close > BB upper AND dealer net buy > threshold
                filtered = []
                for code, s in stock_stats.items():
                    prices = price_map.get(code, [])
                    if len(prices) < bb_period:
                        continue
                    close = s["close_price"]
                    window = np.array(prices[-bb_period:])
                    ma = float(np.mean(window))
                    sd = float(np.std(window))
                    upper = ma + bb_k * sd
                    lower = ma - bb_k * sd

                    if close > upper and s["dealer_net"] > dealer_buy_min:
                        s["bb_upper"] = round(upper, 2)
                        s["bb_mid"] = round(ma, 2)
                        s["bb_lower"] = round(lower, 2)
                        s["bb_diff_pct"] = round(
                            (close - upper) / upper * 100, 2)
                        filtered.append(s)

                filtered.sort(key=lambda x: x["bb_diff_pct"], reverse=True)

                if not filtered:
                    self.error_text = (
                        f"{trade_date} 無符合條件的標的"
                        f"（收盤突破布林上軌 + 自營商買超）"
                    )
                else:
                    self.status_text = f"找到 {len(filtered)} 檔符合條件"
                self.results = filtered
            except Exception as e:
                self.error_text = f"查詢錯誤：{e}"
            finally:
                self.loading = False

        threading.Thread(target=_work, daemon=True).start()

    def run_imminent_cross_strategy(
        self, trade_date: str,
        short_window: int = 5,
        long_window: int = 15,
        top_n: int = 15,
        max_gap_pct: float = 2.0,
        require_narrowing: bool = True,
        insti_types: set[str] | None = None,
        insti_min_days: int = 3,
        chip_filter: bool = False,
        chip_weeks: int = 4,
        chip_big_gain: float = 0.0,
    ):
        """篩選「主力短期集中度即將上穿長期集中度」的個股。

        若 ``insti_types`` 非空（{'foreign','trust','dealer'} 子集），
        進一步要求被勾選的每個法人在訊號日前連續 ``insti_min_days`` 天
        淨買 > 0（建倉）。三項皆空則略過法人過濾。

        若 ``chip_filter`` 為 True，比對 trade_date 當週 TDCC 週報與
        ``chip_weeks`` 週前，要求大戶% 上升 ≥ ``chip_big_gain``。沒週報
        資料的候選會被排除。

        條件、推估天數定義詳見 strategy_eval_service.find_imminent_crossovers。
        """
        trade_date = trade_date.strip()
        if not trade_date:
            self.error_text = "請輸入日期"
            return
        if short_window >= long_window:
            self.error_text = (
                f"短期窗口（{short_window}）必須小於長期窗口（{long_window}）"
            )
            return
        if chip_filter:
            try:
                chip_weeks = int(chip_weeks)
                if chip_weeks < 1 or chip_weeks > 52:
                    raise ValueError
            except (ValueError, TypeError):
                self.error_text = "籌碼比較期週數需為 1–52 的整數"
                return
            try:
                chip_big_gain = float(chip_big_gain)
                if chip_big_gain < 0 or chip_big_gain > 100:
                    raise ValueError
            except (ValueError, TypeError):
                self.error_text = "大戶上升門檻需為 0–100 的數字"
                return
        if self.loading:
            return
        self.loading = True
        self.error_text = ""
        self.results = None
        self.status_text = ""

        def _work():
            try:
                from dataclasses import asdict
                from datetime import datetime as _dt, timedelta
                from services.strategy_eval_service import (
                    find_imminent_crossovers, INSTI_TYPES, insti_buy_streak,
                    chip_change_at_date, chip_concentration_passes,
                )

                self._db.connect()
                self._db.ensure_tables()

                # 需要 long_window + 1 個交易日才能算前後兩個窗口；
                # 取日曆 long_window * 2 天保險（含週末/休市）
                try:
                    end_dt = _dt.strptime(trade_date, "%Y-%m-%d")
                except ValueError:
                    self.error_text = "日期格式錯誤，請用 yyyy-mm-dd"
                    return
                start = (end_dt - timedelta(days=long_window * 2 + 10)
                         ).strftime("%Y-%m-%d")

                rows = self._db.get_broker_history_range(start, trade_date)
                if not rows:
                    self.error_text = (
                        f"{trade_date} 之前無分點資料"
                    )
                    self.results = []
                    return

                # 群組 by stock_code
                grouped: dict[str, list[dict]] = defaultdict(list)
                for r in rows:
                    grouped[r["stock_code"]].append(r)

                cands = find_imminent_crossovers(
                    grouped, trade_date,
                    short_window=short_window,
                    long_window=long_window,
                    top_n=top_n,
                    max_gap_pct=max_gap_pct,
                    require_narrowing=require_narrowing,
                )

                # --- 三大法人 streak（一律計算供顯示；勾選的才作為過濾） ---
                sel = insti_types or set()
                min_n = max(1, int(insti_min_days))
                # 抓夠長的法人歷史以便算 streak
                insti_start = (end_dt - timedelta(days=max(min_n, 1) * 3 + 14)
                               ).strftime("%Y-%m-%d")
                insti_rows = self._db.get_insti_history_range(
                    insti_start, trade_date)
                insti_grouped: dict[str, list[dict]] = defaultdict(list)
                for r in insti_rows:
                    insti_grouped[r["stock_code"]].append(r)

                # --- 籌碼過濾資料（啟用時才撈） ---
                dist_map: dict[str, list[dict]] = {}
                if chip_filter and cands:
                    cand_codes = list({c.stock_code for c in cands})
                    dist_map = self._db.get_distribution_summary_for_codes(
                        cand_codes)

                chip_skipped = 0
                result_dicts = []
                for c in cands:
                    history = insti_grouped.get(c.stock_code, [])
                    streaks = {
                        t: insti_buy_streak(history, trade_date, t)
                        for t in INSTI_TYPES
                    }
                    if sel and not all(streaks[t] >= min_n for t in sel):
                        continue

                    # 籌碼過濾：大戶持股增加
                    chip_info = None
                    if chip_filter:
                        dist_history = dist_map.get(c.stock_code, [])
                        chip_info = chip_change_at_date(
                            dist_history, trade_date, chip_weeks)
                        if chip_info is None:
                            chip_skipped += 1
                            continue
                        if not chip_concentration_passes(
                                chip_info, chip_big_gain):
                            continue

                    d = asdict(c)
                    d["foreign_streak"] = streaks["foreign"]
                    d["trust_streak"] = streaks["trust"]
                    d["dealer_streak"] = streaks["dealer"]
                    # 一律附帶籌碼資料供顯示（沒撈或無資料 → None）
                    if chip_info is not None:
                        d["chip_big_delta"] = chip_info["big_delta"]
                        d["chip_retail_delta"] = chip_info["retail_delta"]
                        d["chip_latest_date"] = chip_info["latest_date"]
                    else:
                        d["chip_big_delta"] = None
                        d["chip_retail_delta"] = None
                        d["chip_latest_date"] = None
                    result_dicts.append(d)

                if not result_dicts:
                    parts = [f"gap≤{max_gap_pct}%"]
                    if require_narrowing:
                        parts.append("gap收窄中")
                    if sel:
                        names = {"foreign": "外資", "trust": "投信",
                                 "dealer": "自營"}
                        labels = " + ".join(names[t] for t in INSTI_TYPES
                                            if t in sel)
                        parts.append(f"{labels} 連續買超≥{min_n}天")
                    if chip_filter:
                        parts.append(
                            f"大戶≥+{chip_big_gain:g}%（比對 {chip_weeks} 週前）"
                        )
                    extra_note = ""
                    if chip_filter and chip_skipped:
                        extra_note = f"；其中 {chip_skipped} 檔缺週報"
                    self.error_text = (
                        f"{trade_date} 無符合條件的標的"
                        f"（{'、'.join(parts)}）{extra_note}"
                    )
                else:
                    extra = ""
                    if sel:
                        names = {"foreign": "外資", "trust": "投信",
                                 "dealer": "自營"}
                        labels = "+".join(names[t] for t in INSTI_TYPES
                                          if t in sel)
                        extra += f"，{labels} 連續{min_n}天+"
                    if chip_filter:
                        extra += f"，大戶≥+{chip_big_gain:g}%"
                    self.status_text = (
                        f"找到 {len(result_dicts)} 檔即將黃金交叉{extra}"
                    )
                self.results = result_dicts
            except Exception as e:
                self.error_text = f"查詢錯誤：{e}"
            finally:
                self.loading = False

        threading.Thread(target=_work, daemon=True).start()

    def shutdown(self):
        try:
            self._db.close()
        except Exception:
            pass
