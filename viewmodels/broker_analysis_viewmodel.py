from __future__ import annotations

import threading

from collections import defaultdict

from viewmodels.base_viewmodel import BaseViewModel, ObservableProperty
from services.db_service import DbService
from services.correlation_service import compute_broker_correlations
from services.tdcc_service import fetch_distribution
from services.broker_tags import get_broker_tags, TAG_DAY, TAG_NEXT, TAG_SHORT


class BrokerAnalysisViewModel(BaseViewModel):
    """ViewModel for the broker analysis tab."""

    # Search
    search_results = ObservableProperty(None)     # list[dict] | None
    error_text = ObservableProperty("")

    # Selected stock
    selected_stock = ObservableProperty(None)      # dict {stock_code, stock_name}
    date_min = ObservableProperty("")
    date_max = ObservableProperty("")

    # Broker summary table
    brokers_data = ObservableProperty(None)        # list[dict] | None

    # Selected broker detail
    selected_broker = ObservableProperty(None)     # dict | None
    detail_data = ObservableProperty(None)         # dict | None

    # Correlation analysis
    correlation_data = ObservableProperty(None)    # list[BrokerCorrelation] | None
    correlation_loading = ObservableProperty(False)

    # Volume info (latest trading day)
    volume_info = ObservableProperty(None)          # dict | None

    # Main-force concentration (主力集中度: 20日/5日)
    concentration_data = ObservableProperty(None)   # dict | None

    # Holder distribution
    holder_data = ObservableProperty(None)         # dict | None
    holder_loading = ObservableProperty(False)

    # Tag rankings (主力分點排行)
    tag_rankings = ObservableProperty(None)        # dict[tag -> list] | None
    tag_rankings_loading = ObservableProperty(False)
    tag_rankings_error = ObservableProperty("")

    # Real next-day-flip ranking
    real_flip_rankings = ObservableProperty(None)  # list[dict] | None

    # Institutional data (三大法人)
    insti_data = ObservableProperty(None)           # list[dict] | None

    def __init__(self):
        super().__init__()
        self._db = DbService()

    def search(self, keyword: str):
        keyword = keyword.strip()
        if not keyword:
            self.error_text = "請輸入搜尋關鍵字"
            return
        self.error_text = ""
        try:
            self._db.connect()
            results = self._db.search_stocks(keyword)
            if not results:
                self.error_text = "查無符合的股票"
                self.search_results = None
            else:
                self.search_results = results
        except Exception as e:
            self.error_text = f"查詢錯誤：{e}"

    def select_stock(self, stock_code: str, stock_name: str):
        self.selected_stock = {"stock_code": stock_code, "stock_name": stock_name}
        self.selected_broker = None
        self.detail_data = None
        self.insti_data = None
        self.volume_info = None
        self.concentration_data = None
        try:
            d_min, d_max = self._db.get_stock_date_range(stock_code)
            self.date_min = d_min
            self.date_max = d_max
            self._load_brokers(stock_code, d_min, d_max)
            # Load institutional data
            insti = self._db.get_insti_history(stock_code, d_min, d_max)
            self.insti_data = insti if insti else None
            # Load volume info (last 2 days)
            vol_rows = self._db.get_latest_volume(stock_code)
            if vol_rows:
                self._build_volume_info(vol_rows)
            # Load main-force concentration (20日/5日 rolling)
            self._load_concentration(stock_code, d_max)
        except Exception as e:
            self.error_text = f"載入錯誤：{e}"

    def _build_volume_info(self, vol_rows: list[dict]):
        def _pv(v) -> int:
            try:
                return int(str(v).replace(",", "").replace(" ", ""))
            except (ValueError, TypeError):
                return 0

        def _pp(v) -> float | None:
            try:
                return float(str(v).replace(",", "").replace(" ", ""))
            except (ValueError, TypeError):
                return None

        latest = vol_rows[0]
        latest_vol = _pv(latest["total_volume"])
        latest_price = _pp(latest["close_price"])
        info = {
            "trade_date": latest["trade_date"],
            "close_price": latest["close_price"],
            "total_volume": latest_vol,
            "vol_change_pct": None,
            "price_change": None,
            "price_change_pct": None,
        }
        if len(vol_rows) >= 2:
            prev_vol = _pv(vol_rows[1]["total_volume"])
            prev_price = _pp(vol_rows[1]["close_price"])
            if prev_vol > 0:
                info["vol_change_pct"] = round(
                    (latest_vol - prev_vol) / prev_vol * 100, 1
                )
            if latest_price is not None and prev_price is not None and prev_price > 0:
                info["price_change"] = round(latest_price - prev_price, 2)
                info["price_change_pct"] = round(
                    (latest_price - prev_price) / prev_price * 100, 2
                )
        self.volume_info = info

    def _load_concentration(self, stock_code: str, end_date: str):
        """Load rolling 20日/5日 main-force concentration for the latest data."""
        if not end_date:
            self.concentration_data = None
            return
        from datetime import datetime as _dt, timedelta
        try:
            end_dt = _dt.strptime(end_date[:10], "%Y-%m-%d")
        except ValueError:
            self.concentration_data = None
            return
        # ~90 calendar days ≈ 60 trading days — enough history for the
        # rolling 20日 line plus a few weeks of trend.
        start_date = (end_dt - timedelta(days=90)).strftime("%Y-%m-%d")
        rows = self._db.get_all_brokers_daily(stock_code, start_date, end_date)
        self.concentration_data = self._compute_concentration(rows)

    @staticmethod
    def _compute_concentration(rows: list[dict]) -> dict | None:
        """Compute rolling main-force concentration (主力集中度).

        For a given interval, concentration =
          (買超前15家張數合計 − 賣超前15家張數合計) / 區間成交量

        Returns a dict with per-day rolling 20日/10日/5日 series (aligned to
        ``labels``/``close``) plus a ``stats`` block summarising the most
        recent 20-day / 10-day / 5-day windows.
        """
        if not rows:
            return None

        def _pi(v) -> int:
            try:
                return int(str(v).replace(",", "").replace(" ", ""))
            except (ValueError, TypeError):
                return 0

        def _pf(v):
            try:
                return float(str(v).replace(",", "").replace(" ", ""))
            except (ValueError, TypeError):
                return None

        # Aggregate per trading day
        by_date_net: dict[str, dict[str, int]] = defaultdict(
            lambda: defaultdict(int))
        vol_by_date: dict[str, int] = {}
        close_by_date: dict[str, float | None] = {}
        for r in rows:
            d = str(r["trade_date"])[:10]
            net = r.get("net_volume")
            if net is None:
                net = (r.get("buy_volume") or 0) - (r.get("sell_volume") or 0)
            by_date_net[d][r["broker_name"]] += net
            vol_by_date[d] = _pi(r.get("total_volume"))
            close_by_date[d] = _pf(r.get("close_price"))

        dates = sorted(by_date_net.keys())
        if not dates:
            return None

        def _window(window_dates: list[str]) -> dict:
            """Aggregate a date window → concentration + summary figures."""
            broker_net: dict[str, int] = defaultdict(int)
            total_vol = 0
            for d in window_dates:
                for bname, net in by_date_net[d].items():
                    broker_net[bname] += net
                total_vol += vol_by_date.get(d, 0)
            buyers = sorted(
                (v for v in broker_net.values() if v > 0), reverse=True)
            sellers = sorted(v for v in broker_net.values() if v < 0)
            top_buy = sum(buyers[:15])           # 買超前15家張數合計 (股)
            top_sell = sum(sellers[:15])         # 賣超前15家張數合計 (股, 負值)
            main_net = top_buy + top_sell        # 主力買賣超 (股)
            conc = (main_net / total_vol * 100) if total_vol > 0 else 0.0
            return {
                "conc": conc,
                "main_net": main_net,
                "buyer_cnt": len(buyers),
                "seller_cnt": len(sellers),
            }

        n = len(dates)
        labels: list[str] = []
        close: list[float | None] = []
        conc5: list[float | None] = []
        conc10: list[float | None] = []
        conc20: list[float | None] = []
        for i in range(n):
            labels.append(dates[i])
            close.append(close_by_date.get(dates[i]))
            if i >= 4:
                conc5.append(round(_window(dates[i - 4:i + 1])["conc"], 2))
            else:
                conc5.append(None)
            if i >= 9:
                conc10.append(round(_window(dates[i - 9:i + 1])["conc"], 2))
            else:
                conc10.append(None)
            if i >= 19:
                conc20.append(round(_window(dates[i - 19:i + 1])["conc"], 2))
            else:
                conc20.append(None)

        # Summary stats for the latest 20-day / 10-day / 5-day windows
        w20 = _window(dates[-20:])
        w10 = _window(dates[-10:])
        w5 = _window(dates[-5:])
        stats = {
            "days20": len(dates[-20:]),
            "days10": len(dates[-10:]),
            "days5": len(dates[-5:]),
            "main_net_lots": int(w20["main_net"] / 1000),
            "broker_diff": w20["buyer_cnt"] - w20["seller_cnt"],
            "conc5": round(w5["conc"], 2),
            "conc10": round(w10["conc"], 2),
            "conc20": round(w20["conc"], 2),
        }
        return {
            "labels": labels,
            "close": close,
            "conc5": conc5,
            "conc10": conc10,
            "conc20": conc20,
            "stats": stats,
        }

    def reload_brokers(self, start_date: str, end_date: str):
        """Reload broker summary for the selected stock with given date range."""
        stock = self.selected_stock
        if not stock:
            return
        try:
            self._load_brokers(stock["stock_code"], start_date, end_date)
        except Exception as e:
            self.error_text = f"載入錯誤：{e}"

    def _load_brokers(self, stock_code: str, start_date: str, end_date: str):
        brokers = self._db.get_brokers_summary(stock_code, start_date, end_date)
        # Compute P&L for each broker: sell_amount - buy_amount
        for b in brokers:
            sell_amt = (b["avg_sell_price"] or 0) * (b["sell_volume"] or 0)
            buy_amt = (b["avg_buy_price"] or 0) * (b["buy_volume"] or 0)
            b["pnl"] = round(sell_amt - buy_amt)
        self.brokers_data = brokers

    def select_broker(
        self, broker_code: str, broker_name: str,
        start_date: str, end_date: str,
    ):
        """Select a broker and load detail data (daily prices + broker volumes)."""
        stock = self.selected_stock
        if not stock:
            return
        self.selected_broker = {
            "broker_code": broker_code, "broker_name": broker_name,
        }
        try:
            self._load_detail(
                stock["stock_code"], broker_code, broker_name,
                start_date, end_date,
            )
        except Exception as e:
            self.error_text = f"載入明細錯誤：{e}"

    def reload_detail(self, start_date: str, end_date: str):
        """Reload detail data with a new date range."""
        stock = self.selected_stock
        broker = self.selected_broker
        if not stock or not broker:
            return
        try:
            self._load_detail(
                stock["stock_code"], broker["broker_code"],
                broker["broker_name"], start_date, end_date,
            )
        except Exception as e:
            self.error_text = f"載入明細錯誤：{e}"

    def _load_detail(
        self, stock_code: str, broker_code: str, broker_name: str,
        start_date: str, end_date: str,
    ):
        prices = self._db.get_stock_prices(stock_code, start_date, end_date)
        broker_daily = self._db.get_broker_daily(
            stock_code, broker_code, broker_name, start_date, end_date,
        )

        # Calculate average buy/sell price for the range
        total_buy_cost = 0.0
        total_buy_vol = 0
        total_sell_cost = 0.0
        total_sell_vol = 0
        for d in broker_daily:
            bv = d["buy_volume"] or 0
            sv = d["sell_volume"] or 0
            bp = d["avg_buy_price"]
            sp = d["avg_sell_price"]
            if bv > 0 and bp is not None:
                total_buy_cost += bp * bv
                total_buy_vol += bv
            if sv > 0 and sp is not None:
                total_sell_cost += sp * sv
                total_sell_vol += sv

        avg_buy = round(total_buy_cost / total_buy_vol, 2) if total_buy_vol > 0 else None
        avg_sell = round(total_sell_cost / total_sell_vol, 2) if total_sell_vol > 0 else None

        self.detail_data = {
            "prices": prices,
            "broker_daily": broker_daily,
            "avg_buy_price": avg_buy,
            "avg_sell_price": avg_sell,
            "total_buy_volume": total_buy_vol,
            "total_sell_volume": total_sell_vol,
            "net_volume": total_buy_vol - total_sell_vol,
            "start_date": start_date,
            "end_date": end_date,
        }

    def load_correlations(self, start_date: str, end_date: str):
        """Compute broker-price correlations in background thread."""
        stock = self.selected_stock
        if not stock:
            return
        self.correlation_loading = True
        self.correlation_data = None

        def _work():
            try:
                rows = self._db.get_all_brokers_daily(
                    stock["stock_code"], start_date, end_date,
                )
                results = compute_broker_correlations(rows)
                self.correlation_data = results
            except Exception as e:
                self.error_text = f"關聯度分析錯誤：{e}"
            finally:
                self.correlation_loading = False

        threading.Thread(target=_work, daemon=True).start()

    def load_holder_distribution(self):
        """Fetch holder distribution from TDCC and save to DB."""
        stock = self.selected_stock
        if not stock or self.holder_loading:
            return
        self.holder_loading = True
        self.holder_data = None

        def _work():
            try:
                code = stock["stock_code"]
                dist = fetch_distribution(code)
                if dist is None:
                    self.holder_data = {"error": "查無集保資料"}
                    return

                # Save to DB
                self._db.connect()
                self._db.ensure_tables()
                levels_dicts = [
                    {"level": lv.level, "label": lv.label,
                     "holders": lv.holders, "shares": lv.shares, "pct": lv.pct}
                    for lv in dist.levels
                ]
                self._db.save_distribution(code, dist.report_date, levels_dicts)

                # Load history from DB
                history = self._db.get_distribution_history(code)

                self.holder_data = {
                    "current": {
                        "report_date": dist.report_date,
                        "retail_pct": dist.retail_pct,
                        "mid_pct": dist.mid_pct,
                        "big_pct": dist.big_pct,
                        "total_holders": dist.total_holders,
                        "total_shares": dist.total_shares,
                        "levels": levels_dicts,
                    },
                    "history": history,
                }
            except Exception as e:
                self.holder_data = {"error": f"載入失敗：{e}"}
            finally:
                self.holder_loading = False

        threading.Thread(target=_work, daemon=True).start()

    # ---- Tag rankings ----

    def load_real_flip_rankings(self, trade_date: str, buy_min_pct: float = 3.0,
                                sell_min_pct: float = 2.0):
        """Find brokers that bought on trade_date and have high probability
        of selling within 2~5 days, based on historical behaviour.

        Logic:
        1. Find brokers that bought today with buy_net >= buy_min_pct% of volume
        2. Look back at their history: when they bought similarly, how often
           did they sell within 2~5 days (sell_net >= sell_min_pct%)?
        3. Rank stocks by the flip probability of the buying brokers
        """
        trade_date = trade_date.strip()
        if not trade_date:
            self.tag_rankings_error = "請輸入日期"
            return
        if self.tag_rankings_loading:
            return
        self.tag_rankings_loading = True
        self.tag_rankings_error = ""
        self.real_flip_rankings = None

        def _work():
            try:
                self._db.connect()

                # Today's broker data
                today_rows = self._db.get_all_broker_buys_by_date(trade_date)
                if not today_rows:
                    self.tag_rankings_error = f"{trade_date} 無分點資料"
                    return

                # Historical data (past 60 trading days ~ 90 calendar days)
                from datetime import datetime as _dt, timedelta
                dt = _dt.strptime(trade_date, "%Y-%m-%d")
                hist_start = (dt - timedelta(days=100)).strftime("%Y-%m-%d")
                hist_end = trade_date
                hist_rows = self._db.get_broker_history_range(hist_start, hist_end)

                result = self._compute_real_flips(
                    today_rows, hist_rows, trade_date,
                    buy_min_pct, sell_min_pct)
                self.real_flip_rankings = result
            except Exception as e:
                self.tag_rankings_error = f"查詢錯誤：{e}"
            finally:
                self.tag_rankings_loading = False

        threading.Thread(target=_work, daemon=True).start()

    @staticmethod
    def _compute_real_flips(
        today_rows: list[dict], hist_rows: list[dict],
        trade_date: str, buy_min_pct: float, sell_min_pct: float,
    ) -> list[dict]:
        """Find brokers buying today that historically flip within 2~5 days."""

        def _pp(v) -> float:
            try:
                return float(str(v).replace(",", ""))
            except (ValueError, TypeError):
                return 0.0

        # Today: find brokers with significant buy
        today_stock_vol: dict[str, int] = defaultdict(int)
        today_stock_info: dict[str, dict] = {}
        today_buys: dict[tuple[str, str], int] = {}  # (stock, broker) -> net_buy

        for r in today_rows:
            code = r["stock_code"]
            bname = r["broker_name"]
            bv = r["buy_volume"] or 0
            sv = r["sell_volume"] or 0
            today_stock_vol[code] += bv + sv
            today_stock_info[code] = {
                "stock_name": r.get("stock_name", ""),
                "close_price": _pp(r.get("close_price", 0)),
            }
            net = bv - sv
            if net > 0:
                key = (code, bname)
                today_buys[key] = today_buys.get(key, 0) + net

        # Filter: only keep significant buys
        sig_buys: dict[tuple[str, str], float] = {}
        for (code, bname), net in today_buys.items():
            tv = today_stock_vol.get(code, 0)
            if tv > 0:
                pct = net / tv * 100
                if pct >= buy_min_pct:
                    sig_buys[(code, bname)] = pct

        if not sig_buys:
            return []

        # Build history: per (stock, broker, date) -> net
        # and per (stock, date) -> total_vol
        hist_by_date: dict[str, dict[tuple[str, str], int]] = defaultdict(
            lambda: defaultdict(int))
        hist_stock_vol: dict[tuple[str, str], int] = defaultdict(int)
        hist_dates: set[str] = set()

        for r in hist_rows:
            d = str(r["trade_date"])[:10]
            if d == trade_date:
                continue
            code = r["stock_code"]
            bname = r["broker_name"]
            bv = r["buy_volume"] or 0
            sv = r["sell_volume"] or 0
            hist_by_date[d][(code, bname)] += (bv - sv)
            hist_stock_vol[(code, d)] += bv + sv
            hist_dates.add(d)

        sorted_dates = sorted(hist_dates)

        # For each broker that bought today: check historical flip rate
        # "flip" = broker bought on day X (>= buy_min_pct%), then sold
        # within day X+2 to X+5 (>= sell_min_pct%)
        broker_flip_stats: dict[str, dict] = {}  # broker -> {buys, flips}

        for bname in set(b for (_, b) in sig_buys):
            stats = {"buy_events": 0, "flip_events": 0}
            for i, d in enumerate(sorted_dates):
                # Check all stocks this broker bought on day d
                for code_key in [k for k in hist_by_date[d]
                                  if k[1] == bname and hist_by_date[d][k] > 0]:
                    code = code_key[0]
                    buy_net = hist_by_date[d][code_key]
                    stv = hist_stock_vol.get((code, d), 0)
                    if stv <= 0:
                        continue
                    buy_pct = buy_net / stv * 100
                    if buy_pct < buy_min_pct:
                        continue

                    stats["buy_events"] += 1

                    # Check D+2 to D+5: did they sell?
                    flipped = False
                    for j in range(i + 1, min(i + 6, len(sorted_dates))):
                        fd = sorted_dates[j]
                        sell_net_raw = hist_by_date[fd].get((code, bname), 0)
                        if sell_net_raw >= 0:
                            continue  # not selling
                        sell_net = abs(sell_net_raw)
                        fstv = hist_stock_vol.get((code, fd), 0)
                        if fstv <= 0:
                            continue
                        sell_pct = sell_net / fstv * 100
                        if sell_pct >= sell_min_pct:
                            flipped = True
                            break

                    if flipped:
                        stats["flip_events"] += 1

            if stats["buy_events"] >= 3:
                broker_flip_stats[bname] = stats

        # Aggregate per stock: which stocks have high-flip-rate brokers buying today
        stock_results: dict[str, dict] = {}
        for (code, bname), buy_pct in sig_buys.items():
            bs = broker_flip_stats.get(bname)
            if not bs or bs["buy_events"] < 3:
                continue
            flip_rate = bs["flip_events"] / bs["buy_events"]
            if flip_rate < 0.3:  # at least 30% flip rate
                continue

            if code not in stock_results:
                info = today_stock_info.get(code, {})
                stock_results[code] = {
                    "stock_code": code,
                    "stock_name": info.get("stock_name", ""),
                    "close_price": info.get("close_price", 0),
                    "flip_brokers": [],
                    "max_flip_rate": 0,
                    "avg_flip_rate": 0,
                    "total_buy_pct": 0,
                }
            sr = stock_results[code]
            sr["flip_brokers"].append({
                "name": bname,
                "buy_pct": buy_pct,
                "flip_rate": flip_rate,
                "buy_events": bs["buy_events"],
                "flip_events": bs["flip_events"],
            })
            sr["max_flip_rate"] = max(sr["max_flip_rate"], flip_rate)
            sr["total_buy_pct"] += buy_pct

        # Build final list
        result = []
        for code, sr in stock_results.items():
            brokers = sr["flip_brokers"]
            avg_rate = sum(b["flip_rate"] for b in brokers) / len(brokers)
            broker_names = ", ".join(
                f"{b['name']}({b['flip_rate']:.0%})" for b in
                sorted(brokers, key=lambda x: x["flip_rate"], reverse=True)[:3]
            )
            result.append({
                "stock_code": sr["stock_code"],
                "stock_name": sr["stock_name"],
                "close_price": sr["close_price"],
                "flip_broker_count": len(brokers),
                "flip_brokers": broker_names,
                "max_flip_rate": round(sr["max_flip_rate"] * 100, 1),
                "avg_flip_rate": round(avg_rate * 100, 1),
                "total_buy_pct": round(sr["total_buy_pct"], 1),
            })
        result.sort(key=lambda x: x["avg_flip_rate"], reverse=True)
        return result[:20]

    def load_tag_rankings(self, trade_date: str):
        trade_date = trade_date.strip()
        if not trade_date:
            self.tag_rankings_error = "請輸入日期"
            return
        if self.tag_rankings_loading:
            return
        self.tag_rankings_loading = True
        self.tag_rankings_error = ""
        self.tag_rankings = None

        def _work():
            try:
                self._db.connect()
                rows = self._db.get_all_broker_buys_by_date(trade_date)
                if not rows:
                    self.tag_rankings_error = (
                        f"{trade_date} 無資料（可能非交易日或尚未下載）"
                    )
                    return
                self.tag_rankings = self._compute_tag_rankings(rows)
            except Exception as e:
                self.tag_rankings_error = f"查詢錯誤：{e}"
            finally:
                self.tag_rankings_loading = False

        threading.Thread(target=_work, daemon=True).start()

    @staticmethod
    def _compute_tag_rankings(rows: list[dict]) -> dict[str, list[dict]]:
        def _pv(v) -> int:
            try:
                return int(str(v).replace(",", "").replace(" ", ""))
            except (ValueError, TypeError):
                return 0

        stocks: dict[str, dict] = {}
        for r in rows:
            code = r["stock_code"]
            if code not in stocks:
                stocks[code] = {
                    "stock_name": r["stock_name"],
                    "close_price": r["close_price"],
                    "total_broker_vol": 0,  # sum of all broker buy+sell
                    "tag_net": defaultdict(int),
                }
            s = stocks[code]
            bv = r["buy_volume"] or 0
            sv = r["sell_volume"] or 0
            s["total_broker_vol"] += bv + sv
            net = bv - sv
            if net > 0:
                for t in get_broker_tags(r["broker_name"]):
                    s["tag_net"][t] += net

        # Top 20 by net buy (買超) / total broker volume
        result: dict[str, list[dict]] = {}
        for tag in (TAG_DAY, TAG_NEXT, TAG_SHORT):
            ranked = []
            for code, s in stocks.items():
                tv = s["total_broker_vol"]
                net = s["tag_net"].get(tag, 0)
                if net <= 0 or tv <= 0:
                    continue
                ranked.append({
                    "stock_code": code,
                    "stock_name": s["stock_name"],
                    "close_price": s["close_price"],
                    "tag_net_volume": net,
                    "ratio": round(net / tv * 100, 2),
                })
            ranked.sort(key=lambda x: x["ratio"], reverse=True)
            result[tag] = ranked[:20]
        return result

    def shutdown(self):
        try:
            self._db.close()
        except Exception:
            pass
