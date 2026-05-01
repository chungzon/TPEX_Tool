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

    # Holder distribution
    holder_data = ObservableProperty(None)         # dict | None
    holder_loading = ObservableProperty(False)

    # Tag rankings (主力分點排行)
    tag_rankings = ObservableProperty(None)        # dict[tag -> list] | None
    tag_rankings_loading = ObservableProperty(False)
    tag_rankings_error = ObservableProperty("")

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
        try:
            d_min, d_max = self._db.get_stock_date_range(stock_code)
            self.date_min = d_min
            self.date_max = d_max
            self._load_brokers(stock_code, d_min, d_max)
            # Load institutional data
            insti = self._db.get_insti_history(stock_code, d_min, d_max)
            self.insti_data = insti if insti else None
        except Exception as e:
            self.error_text = f"載入錯誤：{e}"

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
                    "total_volume": _pv(r["total_volume"]),
                    "tag_net": defaultdict(int),  # net = buy - sell
                }
            s = stocks[code]
            bv = r["buy_volume"] or 0
            sv = r["sell_volume"] or 0
            net = bv - sv
            # Only count brokers with net buy (positive)
            if net > 0:
                for t in get_broker_tags(r["broker_name"]):
                    s["tag_net"][t] += net

        # Top 20 by net buy (買超) / total volume
        result: dict[str, list[dict]] = {}
        for tag in (TAG_DAY, TAG_NEXT, TAG_SHORT):
            ranked = []
            for code, s in stocks.items():
                tv = s["total_volume"]
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
