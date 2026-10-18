"""ViewModel for the 主力追價 tab.

沿用高周轉率監控 VM（篩選 / 搜尋 / 監控彈窗 / Top N），只換排行來源：
成交量 > 1000 張且近5日主力買均價 > 5日均價，依溢價% 排序。
"""

from __future__ import annotations

from viewmodels.turnover_monitor_viewmodel import TurnoverMonitorViewModel


class MainCostPremiumViewModel(TurnoverMonitorViewModel):
    """主力追價分頁 ViewModel。"""

    LOADING_TEXT = "計算主力追價（近5日主力買均價 vs 5日均價）..."

    def _fetch(self, db):
        from services.main_cost_premium_service import latest_premium
        return latest_premium(db, min_lots=self.MIN_LOTS, top_n=self.top_n,
                              return_ctx=True)

    def _lookup(self, db, q: str, days: list, imap: dict):
        from services.main_cost_premium_service import premium_for_rows
        row = super()._lookup(db, q, days, imap)
        if row is not None:
            premium_for_rows(db, [row], days)
        return row

    def _status_text(self, date: str, ctx: dict) -> str:
        n = (ctx or {}).get("universe")
        uni = f"（{n} 檔中）" if n else ""
        return (
            f"資料日 {date}　量 > {self.MIN_LOTS:,} 張{uni}、"
            f"近5日主力買均價 > 5日均價者，依溢價% Top {self.top_n}"
            f"　5日主力買均＝近5交易日淨買超前15家分點、以各家買進量加權的買進均價；"
            f"5日均價＝近5交易日成交金額÷成交股數（VWAP）；"
            f"溢價%＝(主力買均−5日均價)÷5日均價；"
            f"分點日＝5日內實際有分點資料的天數（< 5 代表爬蟲漏抓，均價僅以有資料日計）；"
            f"其餘欄位同高周轉率監控"
        )
