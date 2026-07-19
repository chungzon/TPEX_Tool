"""ChipSwingService — 籌碼波段（CHIP）核心。

Phase 2：資料組裝 + 主力成本 + 6 分量逐日特徵（每分量正規化 0~100）。
Phase 3（後續）：加權融合 chip_score + 買/出場訊號。
Phase 4（後續）：5/10/20/40/60 日持有回測。

純計算函式吃 dict/list（可離線測），DB 載入為薄包裝。重用：
  strategy_eval_service._aggregate_by_date / _window_concentration（主力集中度）、
  broker_tags（排除隔日沖分點）。
資料來源皆 MSSQL 既有查詢（db_service），不新建資料層。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, asdict

from services.chip_config import ChipConfig
from services.strategy_eval_service import _aggregate_by_date, _window_concentration
from services import broker_tags


def _f(v):
    """數值容錯：接受帶千分位逗號 / 空白的字串（DB 量價欄位為 NVARCHAR）。"""
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").replace(" ", ""))
    except (TypeError, ValueError):
        return None


def _clamp(x, lo=0.0, hi=100.0):
    return lo if x < lo else hi if x > hi else x


@dataclass
class ChipFeatureDay:
    trade_date: str
    close: float
    # 分量原始值
    conc_short: float = 0.0
    conc_long: float = 0.0
    conc_trend: float = 0.0
    broker_net_pct: float = 0.0        # 排隔日沖後主力淨買 / 窗口量 (%)
    main_cost: float = 0.0            # 主力平均成本
    cost_gap_pct: float = 0.0        # (close - cost) / cost * 100
    big_pct: float = 0.0             # 大戶持股比 (%)
    retail_pct: float = 0.0
    holder_trend: float = 0.0        # 大戶比週變化
    insti_net: float = 0.0           # 法人(外資+投信)當日淨買賣超（張）
    insti_streak: int = 0            # 法人(外資+投信)連續同向天數（+買 / -賣）
    ma_mid: float = 0.0
    ma_long: float = 0.0
    ma_mid_slope: float = 0.0        # MA20 較前一日變化（<0 視為月線轉下）
    breakout: bool = False
    vol_ratio: float = 0.0
    tech_strong: bool = False        # 技術面轉強旗標
    # 分量正規化分數 0~100
    s_concentration: float = 50.0
    s_broker_net: float = 50.0
    s_cost: float = 50.0
    s_holder: float = 50.0
    s_insti: float = 50.0
    s_technical: float = 50.0
    # 融合分數
    chip_score: float = 50.0

    def as_dict(self) -> dict:
        return asdict(self)


def _latest_le(snapshots: list[dict], date: str, key: str):
    """回傳 report_date <= date 的最近一筆快照（週頻集保向前填）。"""
    chosen = None
    for s in snapshots:
        if str(s[key])[:10] <= date:
            chosen = s
        else:
            break
    return chosen


def compute_chip_features(prices: list[dict], broker_daily: list[dict],
                          insti: list[dict], distribution: list[dict],
                          cfg: ChipConfig | None = None) -> list[ChipFeatureDay]:
    """逐日計算 6 分量特徵。prices 為主時間軸（升冪）。"""
    cfg = cfg or ChipConfig()
    dates = [str(p["trade_date"])[:10] for p in prices]
    closes = [_f(p.get("close_price")) or 0.0 for p in prices]
    highs = [_f(p.get("high_price")) or 0.0 for p in prices]
    vols = [_f(p.get("total_volume")) or 0.0 for p in prices]
    idx = {d: i for i, d in enumerate(dates)}

    # 集中度：用 strategy_eval 的聚合（含全部分點）
    b_dates, by_date_net, vol_by_date, _close_by = _aggregate_by_date(broker_daily)

    # 排隔日沖後：per-date、per-broker 淨額 + 買量/買成本（主力成本用）
    net_excl_by_date: dict[str, int] = defaultdict(int)
    buy_by_date_broker: dict[str, dict[str, list]] = defaultdict(
        lambda: defaultdict(lambda: [0.0, 0.0, 0]))  # broker -> [buy_vol, buy_cost, net]
    for r in broker_daily:
        d = str(r["trade_date"])[:10]
        name = r.get("broker_name", "")
        net = r.get("net_volume")
        if net is None:
            net = (r.get("buy_volume") or 0) - (r.get("sell_volume") or 0)
        is_day = (cfg.exclude_daytrade_brokers
                  and broker_tags.TAG_NEXT in broker_tags.get_broker_tags(name))
        if not is_day:
            net_excl_by_date[d] += net
        bv = _f(r.get("buy_volume")) or 0.0
        abp = _f(r.get("avg_buy_price"))
        cell = buy_by_date_broker[d][name]
        cell[0] += bv
        cell[1] += bv * abp if abp else 0.0
        cell[2] += net

    # 法人：連續同向（外資+投信 net）
    insti_by_date: dict[str, int] = {}
    for r in insti:
        d = str(r["trade_date"])[:10]
        fn = _f(r.get("foreign_net")) or 0.0
        tn = _f(r.get("trust_net")) or 0.0
        insti_by_date[d] = int(fn + tn)

    out: list[ChipFeatureDay] = []
    for i, d in enumerate(dates):
        close = closes[i]
        fd = ChipFeatureDay(trade_date=d, close=close)
        if close <= 0:
            out.append(fd)
            continue

        # ---- 1. 主力集中度趨勢 ----
        past = [x for x in b_dates if x <= d]
        ws = past[-cfg.conc_short_days:]
        wl = past[-cfg.conc_long_days:]
        if ws:
            fd.conc_short = _window_concentration(ws, by_date_net, vol_by_date, cfg.top_n_brokers)
        if wl:
            fd.conc_long = _window_concentration(wl, by_date_net, vol_by_date, cfg.top_n_brokers)
        fd.conc_trend = fd.conc_short - fd.conc_long
        fd.s_concentration = _clamp(50 + fd.conc_short * 2 + fd.conc_trend * 5)

        # ---- 2. 分點淨買（排隔日沖）----
        win = [x for x in past[-cfg.cost_window_days:]]
        net_excl = sum(net_excl_by_date.get(x, 0) for x in win)
        vol_win = sum(vol_by_date.get(x, 0) for x in win)
        fd.broker_net_pct = (net_excl / vol_win * 100) if vol_win > 0 else 0.0
        fd.s_broker_net = _clamp(50 + fd.broker_net_pct * 5)

        # ---- 3. 主力成本 vs 價 ----
        agg: dict[str, list] = defaultdict(lambda: [0.0, 0.0, 0])
        for x in win:
            for name, cell in buy_by_date_broker.get(x, {}).items():
                if (cfg.exclude_daytrade_brokers
                        and broker_tags.TAG_NEXT in broker_tags.get_broker_tags(name)):
                    continue
                a = agg[name]
                a[0] += cell[0]; a[1] += cell[1]; a[2] += cell[2]
        top = sorted(agg.values(), key=lambda c: c[2], reverse=True)[:cfg.top_n_brokers]
        tbv = sum(c[0] for c in top)
        tbc = sum(c[1] for c in top)
        fd.main_cost = (tbc / tbv) if tbv > 0 else 0.0
        if fd.main_cost > 0:
            fd.cost_gap_pct = (close - fd.main_cost) / fd.main_cost * 100
            fd.s_cost = _clamp(50 + fd.cost_gap_pct * 3)

        # ---- 4. 大戶 / 散戶結構（週頻向前填）----
        snap = _latest_le(distribution, d, "report_date")
        if snap:
            fd.big_pct = _f(snap.get("big_pct")) or 0.0
            fd.retail_pct = _f(snap.get("retail_pct")) or 0.0
            # 趨勢：holder_trend_weeks 週前的快照
            prev_snaps = [s for s in distribution if str(s["report_date"])[:10] <= d]
            if len(prev_snaps) > cfg.holder_trend_weeks:
                base = prev_snaps[-cfg.holder_trend_weeks - 1]
                fd.holder_trend = fd.big_pct - (_f(base.get("big_pct")) or 0.0)
            fd.s_holder = _clamp(50 + fd.holder_trend * 10
                                 + (fd.big_pct - fd.retail_pct) * 0.3)

        # ---- 5. 法人連續買賣超 ----
        streak = 0
        for x in reversed(past):
            v = insti_by_date.get(x)
            if v is None or v == 0:
                break
            if streak == 0:
                streak = 1 if v > 0 else -1
            elif (streak > 0 and v > 0):
                streak += 1
            elif (streak < 0 and v < 0):
                streak -= 1
            else:
                break
        fd.insti_streak = streak
        fd.insti_net = insti_by_date.get(d, 0)
        fd.s_insti = _clamp(50 + streak * 12)

        # ---- 6. 技術面 ----
        if i + 1 >= cfg.ma_mid:
            fd.ma_mid = sum(closes[i + 1 - cfg.ma_mid:i + 1]) / cfg.ma_mid
        if i + 1 >= cfg.ma_long:
            fd.ma_long = sum(closes[i + 1 - cfg.ma_long:i + 1]) / cfg.ma_long
        lb = cfg.breakout_lookback
        if i >= lb:
            prior_high = max(highs[i - lb:i])
            fd.breakout = close > prior_high > 0
        if i + 1 >= cfg.vol_ma_days:
            vma = sum(vols[i + 1 - cfg.vol_ma_days:i + 1]) / cfg.vol_ma_days
            fd.vol_ratio = (vols[i] / vma) if vma > 0 else 0.0
        above_mid = fd.ma_mid > 0 and close >= fd.ma_mid
        above_long = fd.ma_long > 0 and close >= fd.ma_long
        vol_up = fd.vol_ratio >= cfg.vol_increase_ratio
        if fd.ma_mid > 0 and i >= cfg.ma_mid:
            fd.ma_mid_slope = fd.ma_mid - sum(closes[i - cfg.ma_mid:i]) / cfg.ma_mid
        mid_slope_up = fd.ma_mid_slope > 0
        fd.tech_strong = fd.breakout or (above_mid and vol_up)
        fd.s_technical = _clamp((20 if above_mid else 0) + (15 if above_long else 0)
                                + (30 if fd.breakout else 0) + (15 if vol_up else 0)
                                + (20 if mid_slope_up else 0))
        out.append(fd)
    return out


# ---------------------------------------------------------------------------
# Phase 3：chip_score 加權融合
# ---------------------------------------------------------------------------

_SCORE_PARTS = (
    ("concentration", "s_concentration"),
    ("broker_net", "s_broker_net"),
    ("cost_vs_price", "s_cost"),
    ("holder", "s_holder"),
    ("insti", "s_insti"),
    ("technical", "s_technical"),
)


def compute_chip_score(fd: ChipFeatureDay, cfg: ChipConfig | None = None) -> float:
    """6 分量加權平均 → 0~100。權重取自 cfg.weights，合計自動正規化。"""
    cfg = cfg or ChipConfig()
    total_w = 0.0
    acc = 0.0
    for key, attr in _SCORE_PARTS:
        w = float(cfg.weights.get(key, 0.0) or 0.0)
        if w <= 0:
            continue
        total_w += w
        acc += w * getattr(fd, attr)
    if total_w <= 0:
        return 50.0
    return _clamp(acc / total_w)


def apply_chip_scores(features: list[ChipFeatureDay],
                      cfg: ChipConfig | None = None) -> list[ChipFeatureDay]:
    """就地填入每日 chip_score。"""
    cfg = cfg or ChipConfig()
    for fd in features:
        fd.chip_score = round(compute_chip_score(fd, cfg), 1)
    return features


# ---------------------------------------------------------------------------
# Phase 3：買進候選 / 出場訊號
# ---------------------------------------------------------------------------

EXIT_SCORE_WEAK = "籌碼轉弱"
EXIT_BELOW_COST = "跌破主力成本"
EXIT_TREND_REVERSAL = "趨勢反轉"
EXIT_OPEN = "持有中"


@dataclass
class ChipSignal:
    """一筆完整波段（買進候選 → 出場）。open_position=True 表區間結束仍持有。"""
    stock_code: str
    stock_name: str
    entry_date: str
    entry_price: float
    entry_score: float
    entry_cost: float          # 進場日主力成本
    exit_date: str
    exit_price: float
    exit_score: float
    exit_reason: str
    hold_days: int             # 持有交易日數
    return_pct: float
    open_position: bool = False


def _exit_reason(fd: ChipFeatureDay, cfg: ChipConfig) -> str | None:
    if fd.chip_score < cfg.exit_threshold:
        return EXIT_SCORE_WEAK
    if cfg.exit_below_cost and fd.main_cost > 0 and fd.close < fd.main_cost:
        return EXIT_BELOW_COST
    if cfg.exit_on_trend_reversal and fd.ma_mid > 0 and fd.ma_mid_slope < 0:
        return EXIT_TREND_REVERSAL
    return None


def detect_chip_signals(features: list[ChipFeatureDay], stock_code: str = "",
                        stock_name: str = "",
                        cfg: ChipConfig | None = None) -> list[ChipSignal]:
    """規則波段訊號：chip_score ≥ buy_threshold 且技術面轉強 → 進場；
    籌碼轉弱 / 跌破主力成本 / 月線轉下 → 出場。同時間僅持有一筆。"""
    cfg = cfg or ChipConfig()
    out: list[ChipSignal] = []
    entry: ChipFeatureDay | None = None
    entry_i = -1

    def _close(exit_fd: ChipFeatureDay, exit_i: int, reason: str) -> ChipSignal:
        ret = ((exit_fd.close - entry.close) / entry.close * 100
               if entry.close > 0 else 0.0)
        return ChipSignal(
            stock_code=stock_code, stock_name=stock_name,
            entry_date=entry.trade_date, entry_price=round(entry.close, 2),
            entry_score=entry.chip_score, entry_cost=round(entry.main_cost, 2),
            exit_date=exit_fd.trade_date, exit_price=round(exit_fd.close, 2),
            exit_score=exit_fd.chip_score, exit_reason=reason,
            hold_days=exit_i - entry_i, return_pct=round(ret, 2),
            open_position=(reason == EXIT_OPEN),
        )

    for i, fd in enumerate(features):
        if fd.close <= 0:
            continue
        if entry is None:
            if fd.chip_score >= cfg.buy_threshold and fd.tech_strong:
                entry, entry_i = fd, i
            continue
        reason = _exit_reason(fd, cfg)
        if reason:
            out.append(_close(fd, i, reason))
            entry, entry_i = None, -1

    if entry is not None:
        last_i = len(features) - 1
        out.append(_close(features[last_i], last_i, EXIT_OPEN))
    return out


def chip_signals_to_dicts(signals: list[ChipSignal]) -> list[dict]:
    return [asdict(s) for s in signals]


# ---------------------------------------------------------------------------
# Phase 4：多持有週期回測（5/10/20/40/60 日）
# ---------------------------------------------------------------------------

@dataclass
class ChipBacktestTrade:
    """一筆固定持有期回測交易（進場後持有 hold_days 日）。"""
    entry_date: str
    entry_price: float
    entry_score: float
    hold_days: int
    exit_date: str
    exit_price: float
    return_pct: float


@dataclass
class ChipBacktestResult:
    """單一持有週期的回測績效。"""
    hold_days: int
    count: int
    win_rate: float          # %
    avg_return: float        # %
    median_return: float     # %
    expectancy: float        # 每筆期望報酬 %
    avg_win: float
    avg_loss: float
    best: float
    worst: float
    max_drawdown: float      # 累積報酬曲線最大回撤 %（正值）
    incomplete: int          # 進場後不足 hold_days 日、無法評估的筆數

    def as_dict(self) -> dict:
        return asdict(self)


def find_entry_indices(features: list[ChipFeatureDay],
                       cfg: ChipConfig | None = None) -> list[int]:
    """買進候選日索引：chip_score ≥ buy_threshold 且技術面轉強。
    只取「首次進入買區」當日（前一日不符），避免同一波段連續多日重複計入。"""
    cfg = cfg or ChipConfig()
    out: list[int] = []
    prev_ok = False
    for i, fd in enumerate(features):
        ok = (fd.close > 0 and fd.chip_score >= cfg.buy_threshold
              and fd.tech_strong)
        if ok and not prev_ok:
            out.append(i)
        prev_ok = ok
    return out


def _max_drawdown(returns_in_order: list[float]) -> float:
    """累積報酬（加總）曲線的最大回撤，回傳正值 %。無交易 → 0。"""
    peak = 0.0
    equity = 0.0
    mdd = 0.0
    for r in returns_in_order:
        equity += r
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > mdd:
            mdd = dd
    return round(mdd, 2)


def _summarise_trades(trades: list[ChipBacktestTrade], hold_days: int,
                      incomplete: int) -> ChipBacktestResult:
    n = len(trades)
    if n == 0:
        return ChipBacktestResult(hold_days, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
                                  0.0, 0.0, 0.0, incomplete)
    rets = [t.return_pct for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    sr = sorted(rets)
    mid = sr[n // 2] if n % 2 == 1 else (sr[n // 2 - 1] + sr[n // 2]) / 2
    win_rate = len(wins) / n
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
    return ChipBacktestResult(
        hold_days=hold_days, count=n,
        win_rate=round(win_rate * 100, 1),
        avg_return=round(sum(rets) / n, 2),
        median_return=round(mid, 2),
        expectancy=round(expectancy, 2),
        avg_win=round(avg_win, 2),
        avg_loss=round(avg_loss, 2),
        best=round(max(rets), 2),
        worst=round(min(rets), 2),
        max_drawdown=_max_drawdown(rets),
        incomplete=incomplete,
    )


def backtest_holding_periods(
    features: list[ChipFeatureDay], cfg: ChipConfig | None = None,
) -> tuple[dict[int, ChipBacktestResult], dict[int, list[ChipBacktestTrade]]]:
    """對每個買進候選日，計算持有 5/10/20/40/60 日的前向報酬並彙總。

    Returns: (results_by_period, trades_by_period)。區間末端不足 N 日的進場
    無法評估，計入 result.incomplete，不列入績效。
    """
    cfg = cfg or ChipConfig()
    entries = find_entry_indices(features, cfg)
    results: dict[int, ChipBacktestResult] = {}
    trades_by: dict[int, list[ChipBacktestTrade]] = {}
    last = len(features) - 1
    for n in cfg.holding_periods:
        trades: list[ChipBacktestTrade] = []
        incomplete = 0
        for ei in entries:
            entry = features[ei]
            xi = ei + n
            if xi > last:
                incomplete += 1
                continue
            ex = features[xi]
            if entry.close <= 0 or ex.close <= 0:
                incomplete += 1
                continue
            ret = (ex.close - entry.close) / entry.close * 100
            trades.append(ChipBacktestTrade(
                entry_date=entry.trade_date, entry_price=round(entry.close, 2),
                entry_score=entry.chip_score, hold_days=n,
                exit_date=ex.trade_date, exit_price=round(ex.close, 2),
                return_pct=round(ret, 2),
            ))
        trades_by[n] = trades
        results[n] = _summarise_trades(trades, n, incomplete)
    return results, trades_by


def backtest_results_to_dicts(
    results: dict[int, ChipBacktestResult],
) -> list[dict]:
    """依 holding_periods 順序輸出，供 UI 表格。"""
    return [results[n].as_dict() for n in sorted(results)]


def summarise_brokers(broker_daily: list[dict],
                      cfg: ChipConfig | None = None) -> list[dict]:
    """把區間內分點聚合為每家淨買賣超 + 買均價，並標記隔日沖分點。
    供「分點籌碼表格」使用，依淨額由大到小排序。"""
    cfg = cfg or ChipConfig()
    agg: dict[str, list] = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    # name -> [buy_vol, buy_cost, sell_vol, net]
    for r in broker_daily:
        name = r.get("broker_name", "")
        bv = _f(r.get("buy_volume")) or 0.0
        sv = _f(r.get("sell_volume")) or 0.0
        abp = _f(r.get("avg_buy_price"))
        net = r.get("net_volume")
        if net is None:
            net = bv - sv
        cell = agg[name]
        cell[0] += bv
        cell[1] += bv * abp if abp else 0.0
        cell[2] += sv
        cell[3] += net
    out = []
    for name, (bv, bc, sv, net) in agg.items():
        is_day = broker_tags.TAG_NEXT in broker_tags.get_broker_tags(name)
        out.append({
            "broker_name": name,
            "buy_lots": int(bv // 1000),
            "sell_lots": int(sv // 1000),
            "net_lots": int(net // 1000),
            "avg_buy_price": round(bc / bv, 2) if bv > 0 else 0.0,
            "is_daytrade": is_day,
        })
    out.sort(key=lambda x: x["net_lots"], reverse=True)
    return out


class ChipSwingService:
    """DB 載入 + 特徵計算 + chip_score + 波段訊號。Phase 4 於此加多週期回測。"""

    def __init__(self, db, cfg: ChipConfig | None = None):
        self._db = db
        self.cfg = cfg or ChipConfig()

    def load_features(self, stock_code: str, start_date: str,
                      end_date: str) -> list[ChipFeatureDay]:
        prices = self._db.get_stock_prices(stock_code, start_date, end_date)
        brokers = self._db.get_all_brokers_daily(stock_code, start_date, end_date)
        insti = self._db.get_insti_history(stock_code, start_date, end_date)
        dist = self._db.get_distribution_history(stock_code)
        feats = compute_chip_features(prices, brokers, insti, dist, self.cfg)
        return apply_chip_scores(feats, self.cfg)

    def analyse(self, stock_code: str, start_date: str, end_date: str) -> dict:
        """一次回傳籌碼波段全套結果，供 UI 分頁。

        keys: features / signals / backtest（各持有期績效）/ backtest_trades /
              brokers（分點聚合）/ stock_code / stock_name。
        """
        prices = self._db.get_stock_prices(stock_code, start_date, end_date)
        brokers = self._db.get_all_brokers_daily(stock_code, start_date, end_date)
        insti = self._db.get_insti_history(stock_code, start_date, end_date)
        dist = self._db.get_distribution_history(stock_code)
        feats = apply_chip_scores(
            compute_chip_features(prices, brokers, insti, dist, self.cfg),
            self.cfg)
        names = self._db.get_stock_names([stock_code]) or {}
        name = names.get(stock_code, "")
        signals = detect_chip_signals(feats, stock_code, name, self.cfg)
        results, trades_by = backtest_holding_periods(feats, self.cfg)
        return {
            "stock_code": stock_code,
            "stock_name": name,
            "features": feats,
            "signals": signals,
            "backtest": results,
            "backtest_trades": trades_by,
            "brokers": summarise_brokers(brokers, self.cfg),
        }
