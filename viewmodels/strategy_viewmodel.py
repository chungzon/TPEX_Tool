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

    def run_short_daytrade_strategy(
        self, trade_date: str,
        conc_max: float = 0.0,
        band_min: float = 20.0,
        slope_max: float = 0.0,
        rank_window: int = 60,
        bias_min: float = 10.0,
        bias6_max: float = -3.0,
        bias12_max: float = -4.5,
        bias20_max: float = -7.0,
        bias72_max: float = -11.0,
        use_bias_min: bool = True,
        use_bias6: bool = True,
        use_bias12: bool = True,
        use_bias20: bool = True,
        use_bias72: bool = True,
        main_window: int = 10,
        top_n: int = 15,
        # 助空/警訊 icon 門檻
        sig_next_flip_pct: float = 2.0,
        sig_foreign_streak: int = 3,
        sig_dealer_dump_lots: int = 200,
        sig_margin_chase_pct: float = 5.0,
        sig_short_ratio: float = 30.0,
    ):
        """放空當沖標的篩選：
        主10 < conc_max、帶寬 > band_min、月線斜率 < slope_max；
        勾選的乖離條件才套用（年線 ≥ / 周/雙週/月/季 ≤ 對應弱勢門檻），
        依「位階」desc 排序
        （高位階 + 主力出貨 + 月線下彎 + 帶寬大 + 短中期偏弱 = 黑K放空）。
        """
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
                from dataclasses import asdict
                from datetime import datetime as _dt, timedelta
                from services.strategy_eval_service import (
                    find_short_daytrade_candidates,
                )

                self._db.connect()
                self._db.ensure_tables()

                try:
                    end_dt = _dt.strptime(trade_date, "%Y-%m-%d")
                except ValueError:
                    self.error_text = "日期格式錯誤，請用 yyyy-mm-dd"
                    return

                # 1. 分點：覆蓋 main_window 個交易日
                broker_start = (end_dt - timedelta(days=main_window * 2 + 10)
                                ).strftime("%Y-%m-%d")
                broker_rows = self._db.get_broker_history_range(
                    broker_start, trade_date)
                if not broker_rows:
                    self.error_text = f"{trade_date} 之前無分點資料"
                    self.results = []
                    return

                grouped: dict[str, list[dict]] = defaultdict(list)
                for r in broker_rows:
                    grouped[r["stock_code"]].append(r)

                # 2. 價格：覆蓋 250 交易日年線 + 緩衝（≈ 420 日曆日）
                price_start = (end_dt - timedelta(days=420)
                               ).strftime("%Y-%m-%d")
                price_map = self._db.get_all_prices_range(
                    price_start, trade_date)
                if not price_map:
                    self.error_text = f"{trade_date} 無價格資料"
                    self.results = []
                    return

                cands = find_short_daytrade_candidates(
                    grouped, price_map, trade_date,
                    main_window=main_window, top_n=top_n,
                    conc_max=conc_max, band_min=band_min,
                    slope_max=slope_max, rank_window=rank_window,
                    bias_min=bias_min,
                    bias6_max=bias6_max, bias12_max=bias12_max,
                    bias20_max=bias20_max, bias72_max=bias72_max,
                    use_bias_min=use_bias_min,
                    use_bias6=use_bias6, use_bias12=use_bias12,
                    use_bias20=use_bias20, use_bias72=use_bias72,
                )

                # 取得候選的 insti + TDCC 資料以計算助空/警訊 icon
                if cands:
                    self.status_text = "計算輔助訊號..."
                    from services.strategy_eval_service import (
                        compute_short_setup_signals,
                    )
                    from dataclasses import asdict as _asdict

                    cand_codes = list({c.stock_code for c in cands})
                    insti_start = (end_dt - timedelta(days=20)
                                   ).strftime("%Y-%m-%d")
                    insti_rows = self._db.get_insti_history_range(
                        insti_start, trade_date)
                    insti_grouped: dict[str, list[dict]] = defaultdict(list)
                    for r in insti_rows:
                        insti_grouped[r["stock_code"]].append(r)

                    pct_map = self._db.get_distribution_summary_for_codes(
                        cand_codes)
                    holder_map = self._db.get_holder_count_history_for_codes(
                        cand_codes)

                    # 融資融券（近 10 交易日；用於散戶追高 & 券資比警訊）
                    margin_start = (end_dt - timedelta(days=20)
                                    ).strftime("%Y-%m-%d")
                    margin_map_all = self._db.get_margin_history_range(
                        margin_start, trade_date)
                    margin_map = {c: rows
                                  for c, rows in margin_map_all.items()
                                  if c in set(cand_codes)}

                    enriched: list[dict] = []
                    for c in cands:
                        sig = compute_short_setup_signals(
                            grouped.get(c.stock_code, []),
                            insti_grouped.get(c.stock_code, []),
                            pct_map.get(c.stock_code, []),
                            holder_map.get(c.stock_code, []),
                            trade_date,
                            margin_history=margin_map.get(c.stock_code, []),
                            next_flip_share_min=sig_next_flip_pct,
                            foreign_sell_min_streak=sig_foreign_streak,
                            dealer_dump_shares_min=sig_dealer_dump_lots * 1000,
                            margin_chase_pct=sig_margin_chase_pct,
                            short_squeeze_ratio_min=sig_short_ratio,
                        )
                        d = _asdict(c)
                        d["signals"] = _asdict(sig)
                        enriched.append(d)
                    result_dicts = enriched
                else:
                    result_dicts = []

                if not result_dicts:
                    parts = [
                        f"主10<{conc_max:g}",
                        f"帶寬>{band_min:g}",
                        f"月斜率<{slope_max:g}",
                    ]
                    if use_bias_min:
                        parts.append(f"年線≥{bias_min:g}%")
                    if use_bias6:
                        parts.append(f"周乖≤{bias6_max:g}%")
                    if use_bias12:
                        parts.append(f"雙週乖≤{bias12_max:g}%")
                    if use_bias20:
                        parts.append(f"月乖≤{bias20_max:g}%")
                    if use_bias72:
                        parts.append(f"季乖≤{bias72_max:g}%")
                    self.error_text = (
                        f"{trade_date} 無符合條件的標的（{'、'.join(parts)}）"
                    )
                else:
                    self.status_text = (
                        f"找到 {len(result_dicts)} 檔放空當沖候選"
                    )
                self.results = result_dicts
            except Exception as e:
                self.error_text = f"查詢錯誤：{e}"
                import traceback
                traceback.print_exc()
            finally:
                self.loading = False

        threading.Thread(target=_work, daemon=True).start()

    def run_main_accumulation_strategy(
        self, trade_date: str,
        conc_delta_min: float = 0.3,
        chip_weeks: int = 4,
        big_delta_min: float = 0.0,
        main_window: int = 10,
        top_n: int = 15,
    ):
        """策略五：主力進場 + 大戶集中 做多候選。

        主10 趨勢遞增（近 5 日主10 增加 ≥ conc_delta_min）且
        大戶持股 chip_weeks 週前 → 現在 增幅 ≥ big_delta_min。
        排序依 conc_10_delta desc。
        """
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
                from dataclasses import asdict
                from datetime import datetime as _dt, timedelta
                from services.strategy_eval_service import (
                    find_main_accumulation_candidates,
                )

                self._db.connect()
                self._db.ensure_tables()

                try:
                    end_dt = _dt.strptime(trade_date, "%Y-%m-%d")
                except ValueError:
                    self.error_text = "日期格式錯誤，請用 yyyy-mm-dd"
                    return

                # 分點需要覆蓋 main_window + 5 個交易日（趨勢比對）
                broker_start = (
                    end_dt - timedelta(days=main_window * 2 + 15)
                ).strftime("%Y-%m-%d")
                broker_rows = self._db.get_broker_history_range(
                    broker_start, trade_date)
                if not broker_rows:
                    self.error_text = f"{trade_date} 之前無分點資料"
                    self.results = []
                    return

                grouped: dict[str, list[dict]] = defaultdict(list)
                for r in broker_rows:
                    grouped[r["stock_code"]].append(r)

                # 價格 — 覆蓋 MA250 + 緩衝
                price_start = (
                    end_dt - timedelta(days=420)
                ).strftime("%Y-%m-%d")
                price_map = self._db.get_all_prices_range(
                    price_start, trade_date)
                if not price_map:
                    self.error_text = f"{trade_date} 無價格資料"
                    self.results = []
                    return

                # TDCC 週報（大戶/散戶 比例變化）
                self.status_text = "載入 TDCC 週報..."
                all_codes = list(price_map.keys())
                chip_map = self._db.get_distribution_summary_for_codes(
                    all_codes)

                cands = find_main_accumulation_candidates(
                    grouped, price_map, chip_map, trade_date,
                    main_window=main_window, top_n=top_n,
                    conc_delta_min=conc_delta_min,
                    chip_weeks=chip_weeks,
                    big_delta_min=big_delta_min,
                )

                result_dicts = [asdict(c) for c in cands]
                if not result_dicts:
                    self.error_text = (
                        f"{trade_date} 無符合條件的標的"
                        f"（主10勢≥+{conc_delta_min:g}、"
                        f"大戶≥+{big_delta_min:g}%（比對 {chip_weeks} 週前））"
                    )
                else:
                    self.status_text = (
                        f"找到 {len(result_dicts)} 檔做多候選"
                    )
                self.results = result_dicts
            except Exception as e:
                self.error_text = f"查詢錯誤：{e}"
                import traceback
                traceback.print_exc()
            finally:
                self.loading = False

        threading.Thread(target=_work, daemon=True).start()

    def shutdown(self):
        try:
            self._db.close()
        except Exception:
            pass
