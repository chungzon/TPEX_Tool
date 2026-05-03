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

    def shutdown(self):
        try:
            self._db.close()
        except Exception:
            pass
