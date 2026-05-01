"""ViewModel for broker-tag stock ranking (主力分點排行)."""

from __future__ import annotations

import threading
from collections import defaultdict

from viewmodels.base_viewmodel import BaseViewModel, ObservableProperty
from services.db_service import DbService
from services.broker_tags import (
    get_broker_tags, TAG_DAY, TAG_NEXT, TAG_SHORT,
)


def _parse_vol(v) -> int:
    try:
        return int(str(v).replace(",", "").replace(" ", ""))
    except (ValueError, TypeError):
        return 0


class TagRankingViewModel(BaseViewModel):

    rankings = ObservableProperty(None)   # dict[tag -> list[dict]] | None
    loading = ObservableProperty(False)
    error_text = ObservableProperty("")

    def __init__(self):
        super().__init__()
        self._db = DbService()

    def load_rankings(self, trade_date: str):
        trade_date = trade_date.strip()
        if not trade_date:
            self.error_text = "請輸入日期"
            return
        if self.loading:
            return
        self.loading = True
        self.error_text = ""
        self.rankings = None

        def _work():
            try:
                self._db.connect()
                rows = self._db.get_all_broker_buys_by_date(trade_date)
                if not rows:
                    self.error_text = f"{trade_date} 無資料（可能非交易日或尚未下載）"
                    return

                result = self._compute(rows)
                self.rankings = result
            except Exception as e:
                self.error_text = f"查詢錯誤：{e}"
            finally:
                self.loading = False

        threading.Thread(target=_work, daemon=True).start()

    def _compute(self, rows: list[dict]) -> dict[str, list[dict]]:
        # Per stock: accumulate total volume, and tagged buy volume
        # stock_code -> {stock_name, close_price, total_volume,
        #                tag_buy: {tag -> int}, total_buy: int}
        stocks: dict[str, dict] = {}
        for r in rows:
            code = r["stock_code"]
            if code not in stocks:
                stocks[code] = {
                    "stock_name": r["stock_name"],
                    "close_price": r["close_price"],
                    "total_volume": _parse_vol(r["total_volume"]),
                    "tag_buy": defaultdict(int),
                    "total_buy": 0,
                }
            s = stocks[code]
            bv = r["buy_volume"] or 0
            s["total_buy"] += bv

            tags = get_broker_tags(r["broker_name"])
            for t in tags:
                s["tag_buy"][t] += bv

        # Build top 20 per tag
        result: dict[str, list[dict]] = {}
        for tag in (TAG_DAY, TAG_NEXT, TAG_SHORT):
            ranked = []
            for code, s in stocks.items():
                tv = s["total_volume"]
                tbv = s["tag_buy"].get(tag, 0)
                if tbv <= 0 or tv <= 0:
                    continue
                ranked.append({
                    "stock_code": code,
                    "stock_name": s["stock_name"],
                    "close_price": s["close_price"],
                    "total_volume": tv,
                    "tag_buy_volume": tbv,
                    "ratio": round(tbv / tv * 100, 2),
                })
            ranked.sort(key=lambda x: x["ratio"], reverse=True)
            result[tag] = ranked[:20]

        return result

    def shutdown(self):
        try:
            self._db.close()
        except Exception:
            pass
