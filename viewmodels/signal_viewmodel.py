"""ViewModel for the branch signal (分點訊號) tab."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta
from collections import defaultdict

from viewmodels.base_viewmodel import BaseViewModel, ObservableProperty
from services.db_service import DbService
from services.alpha_service import (
    compute_signals_for_date, compute_branch_alphas,
    enrich_cluster_scores, compute_composite_scores,
    BranchSignal, BranchAlpha,
)


class SignalViewModel(BaseViewModel):

    signals = ObservableProperty(None)          # list[BranchSignal] | None
    branch_alphas = ObservableProperty(None)    # list[BranchAlpha] | None
    loading = ObservableProperty(False)
    error_text = ObservableProperty("")
    status_text = ObservableProperty("")

    def __init__(self):
        super().__init__()
        self._db = DbService()

    def run_analysis(self, trade_date: str, lookback_days: int = 60):
        """Full pipeline: anomaly detection → alpha → clustering → composite score."""
        trade_date = trade_date.strip()
        if not trade_date:
            self.error_text = "請輸入日期"
            return
        if self.loading:
            return
        self.loading = True
        self.error_text = ""
        self.status_text = "分析中..."
        self.signals = None
        self.branch_alphas = None

        def _work():
            try:
                self._db.connect()
                self._db.ensure_tables()

                # Date range for history
                dt = datetime.strptime(trade_date, "%Y-%m-%d")
                hist_start = (dt - timedelta(days=int(lookback_days * 1.6))).strftime("%Y-%m-%d")
                # Forward dates for event study (D+10)
                fwd_end = (dt + timedelta(days=15)).strftime("%Y-%m-%d")

                self.status_text = "載入當日分點資料..."
                today_rows = self._db.get_all_broker_buys_by_date(trade_date)
                if not today_rows:
                    self.error_text = f"{trade_date} 無分點資料"
                    return

                self.status_text = "載入歷史分點資料..."
                hist_rows = self._db.get_broker_history_range(hist_start, trade_date)

                # Separate history (exclude target date for Z-score)
                hist_only = [r for r in hist_rows if str(r["trade_date"])[:10] != trade_date]

                self.status_text = "載入價格資料..."
                price_history = self._db.get_all_prices_range(hist_start, fwd_end)

                # Phase 1: Anomaly + Event Study
                self.status_text = "Phase 1：異常偵測 + 事件研究..."
                signals = compute_signals_for_date(
                    today_rows, hist_only, price_history, trade_date)

                if not signals:
                    self.error_text = f"{trade_date} 無異常分點訊號"
                    self.signals = []
                    return

                # Phase 2: Branch Alpha
                self.status_text = "Phase 2：分點 Alpha 計算..."
                # Compute signals for ALL historical dates for alpha
                all_hist_signals = self._compute_all_historical_signals(
                    hist_rows, price_history)
                branch_alphas, branch_stock_alphas = compute_branch_alphas(
                    all_hist_signals)

                ba_map = {(a.broker_code, a.broker_name): a.alpha_score
                          for a in branch_alphas}
                bsa_map = {(a.broker_code, a.broker_name, a.stock_code): a.alpha_score
                           for a in branch_stock_alphas}

                # Phase 3: Clustering + Composite
                self.status_text = "Phase 3：群聚偵測 + 綜合評分..."
                enrich_cluster_scores(signals)
                compute_composite_scores(signals, ba_map, bsa_map)

                # Sort by signal_score descending
                signals.sort(key=lambda s: s.signal_score, reverse=True)

                self.signals = signals
                self.branch_alphas = branch_alphas
                self.status_text = f"完成！{len(signals)} 筆訊號"

            except Exception as e:
                self.error_text = f"分析錯誤：{e}"
                import traceback
                traceback.print_exc()
            finally:
                self.loading = False

        threading.Thread(target=_work, daemon=True).start()

    def _compute_all_historical_signals(
        self, hist_rows: list[dict], price_history: dict,
    ) -> list[BranchSignal]:
        """Compute simplified signals for all historical dates (for alpha calc)."""
        # Group history by date
        by_date: dict[str, list[dict]] = defaultdict(list)
        for r in hist_rows:
            by_date[str(r["trade_date"])[:10]].append(r)

        dates = sorted(by_date.keys())
        all_signals: list[BranchSignal] = []

        for d in dates:
            rows = by_date[d]
            # Simplified: just compute basic signals without Z-score
            stock_totals: dict[str, int] = defaultdict(int)
            stock_names: dict[str, str] = {}
            for r in rows:
                code = r["stock_code"]
                bv = r["buy_volume"] or 0
                sv = r["sell_volume"] or 0
                stock_totals[code] += bv + sv
                stock_names[code] = r.get("stock_name", "")

            for r in rows:
                code = r["stock_code"]
                bv = r["buy_volume"] or 0
                sv = r["sell_volume"] or 0
                net = bv - sv
                tv = stock_totals.get(code, 0)
                if tv <= 0 or net == 0:
                    continue
                vol_share = bv / tv * 100

                # Only include notable activity
                if vol_share < 2.0 and abs(net) < 50000:
                    continue

                close = 0.0
                prices = price_history.get(code, [])
                for p in prices:
                    if str(p["trade_date"])[:10] == d:
                        close = float(str(p["close_price"]).replace(",", "") or 0)
                        break

                # Event study returns
                d1_ret, d3_ret, d5_ret, d1_max = None, None, None, None
                if prices and close > 0:
                    idx = None
                    for i, p in enumerate(prices):
                        if str(p["trade_date"])[:10] == d:
                            idx = i
                            break
                    if idx is not None:
                        def _pr(p):
                            try:
                                return float(str(p["close_price"]).replace(",", ""))
                            except:
                                return 0
                        def _ph(p):
                            try:
                                return float(str(p.get("high_price", 0)).replace(",", ""))
                            except:
                                return 0
                        if idx + 1 < len(prices):
                            d1_ret = round((_pr(prices[idx+1]) - close) / close * 100, 3)
                            h = _ph(prices[idx+1])
                            if h > 0:
                                d1_max = round((h - close) / close * 100, 3)
                        if idx + 3 < len(prices):
                            d3_ret = round((_pr(prices[idx+3]) - close) / close * 100, 3)
                        if idx + 5 < len(prices):
                            d5_ret = round((_pr(prices[idx+5]) - close) / close * 100, 3)

                all_signals.append(BranchSignal(
                    stock_code=code,
                    stock_name=stock_names.get(code, ""),
                    trade_date=d,
                    broker_code=r.get("broker_code", ""),
                    broker_name=r["broker_name"],
                    buy_volume=bv, sell_volume=sv, net_volume=net,
                    total_volume=tv, close_price=close,
                    volume_share=round(vol_share, 2),
                    d1_return=d1_ret, d3_return=d3_ret,
                    d5_return=d5_ret, d1_max_high_pct=d1_max,
                ))

        return all_signals

    def shutdown(self):
        try:
            self._db.close()
        except Exception:
            pass
