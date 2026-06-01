"""策略效益評估 — 計算「主力集中度突破策略」的歷史報酬。

策略一：5/15 集中度黃金交叉
- 進場條件：5日集中度「上穿」15日集中度（前一日 ≤、當日 >）
  （不限正負 — 兩線皆負時的交叉視為賣壓減弱的反轉訊號）
- 進場價：訊號日收盤
- 出場：訊號日後第 N 個交易日收盤（預設 N = 4）
- 報酬：(出場價 / 進場價 − 1) × 100%

集中度公式（與分點分析頁一致）：
  (買超前15家張數合計 − 賣超前15家張數合計) / 區間成交量 × 100%
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, asdict

log = logging.getLogger(__name__)


@dataclass
class StrategySignal:
    """單一進場訊號 + 後續報酬。"""
    stock_code: str
    stock_name: str
    signal_date: str        # 訊號日 (D)
    exit_date: str          # 出場日 (D+N)
    hold_days: int          # 持有交易日數
    conc_short: float       # 訊號日 5日集中度 (%)
    conc_long: float        # 訊號日 15日集中度 (%)
    entry_price: float
    exit_price: float
    return_pct: float       # 報酬 (%)
    # --- 籌碼面（可選；啟用 chip 過濾時填寫，否則為 None）---
    chip_big_delta: float | None = None      # 大戶% 變化 (now − then)
    chip_retail_delta: float | None = None   # 散戶% 變化
    chip_latest_date: str | None = None      # 比對所用的最新週報日
    chip_earlier_date: str | None = None     # 比對所用的較早週報日


# ---------------------------------------------------------------------------

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


def _aggregate_by_date(broker_rows: list[dict]):
    """把 broker 列群組為 per-date 結構。

    Returns: (dates, broker_net_by_date, vol_by_date, close_by_date)
    """
    by_date_net: dict[str, dict[str, int]] = defaultdict(
        lambda: defaultdict(int))
    vol_by_date: dict[str, int] = {}
    close_by_date: dict[str, float | None] = {}
    for r in broker_rows:
        d = str(r["trade_date"])[:10]
        net = r.get("net_volume")
        if net is None:
            net = (r.get("buy_volume") or 0) - (r.get("sell_volume") or 0)
        by_date_net[d][r["broker_name"]] += net
        vol_by_date[d] = _pi(r.get("total_volume"))
        close_by_date[d] = _pf(r.get("close_price"))
    return sorted(by_date_net.keys()), by_date_net, vol_by_date, close_by_date


def _window_concentration(window_dates: list[str],
                           by_date_net: dict, vol_by_date: dict,
                           top_n: int = 15) -> float:
    """單一窗口的主力集中度 (%)。0 區間量回 0。"""
    broker_net: dict[str, int] = defaultdict(int)
    total_vol = 0
    for d in window_dates:
        for bn, net in by_date_net[d].items():
            broker_net[bn] += net
        total_vol += vol_by_date.get(d, 0)
    if total_vol <= 0:
        return 0.0
    buyers = sorted((v for v in broker_net.values() if v > 0), reverse=True)
    sellers = sorted(v for v in broker_net.values() if v < 0)
    top_buy = sum(buyers[:top_n])
    top_sell = sum(sellers[:top_n])   # 負值
    return (top_buy + top_sell) / total_vol * 100


def detect_breakout_signals(
    broker_rows: list[dict],
    stock_code: str,
    stock_name: str,
    short_window: int = 5,
    long_window: int = 15,
    hold_days: int = 4,
    top_n: int = 15,
) -> list[StrategySignal]:
    """偵測「短期集中度上穿長期集中度」訊號並算後續報酬。

    Args:
        broker_rows: 來自 db.get_all_brokers_daily 的列表（單一股票）。
        short_window: 短期窗口（預設 5 日）。
        long_window: 長期窗口（預設 15 日）。
        hold_days: 持有日數（預設 4 日）。
        top_n: 主力家數（預設 15 家）。

    Returns: 該股票所有訊號。出場資料不足者跳過。
    """
    dates, by_date_net, vol_by_date, close_by_date = _aggregate_by_date(
        broker_rows)
    n = len(dates)
    if n < long_window + 1:
        return []

    # 預計算兩個窗口的每日集中度
    conc_s: list[float | None] = [None] * n
    conc_l: list[float | None] = [None] * n
    for i in range(n):
        if i >= short_window - 1:
            conc_s[i] = _window_concentration(
                dates[i - short_window + 1: i + 1],
                by_date_net, vol_by_date, top_n)
        if i >= long_window - 1:
            conc_l[i] = _window_concentration(
                dates[i - long_window + 1: i + 1],
                by_date_net, vol_by_date, top_n)

    signals: list[StrategySignal] = []
    # 從 long_window 開始才會有 i-1 的長期集中度
    for i in range(long_window, n):
        ps, pl = conc_s[i - 1], conc_l[i - 1]
        cs, cl = conc_s[i], conc_l[i]
        if ps is None or pl is None or cs is None or cl is None:
            continue
        # 黃金交叉（短期上穿長期）— 不限制正負，
        # 賣壓減弱（兩線皆負時的交叉）也算反轉訊號
        if not (ps <= pl and cs > cl):
            continue

        entry = close_by_date.get(dates[i])
        if entry is None or entry <= 0:
            continue
        exit_idx = i + hold_days
        if exit_idx >= n:
            continue  # 還沒走完 hold_days 個交易日，跳過
        exit_price = close_by_date.get(dates[exit_idx])
        if exit_price is None or exit_price <= 0:
            continue

        signals.append(StrategySignal(
            stock_code=stock_code,
            stock_name=stock_name,
            signal_date=dates[i],
            exit_date=dates[exit_idx],
            hold_days=hold_days,
            conc_short=round(cs, 2),
            conc_long=round(cl, 2),
            entry_price=round(entry, 2),
            exit_price=round(exit_price, 2),
            return_pct=round((exit_price / entry - 1) * 100, 2),
        ))
    return signals


# ---------------------------------------------------------------------------

def summarise(signals: list[StrategySignal]) -> dict:
    """彙總 signal list → KPI dict（勝率、平均報酬、期望值…）。"""
    n = len(signals)
    if n == 0:
        return {
            "count": 0, "win_rate": 0.0, "avg_return": 0.0,
            "median_return": 0.0, "best": 0.0, "worst": 0.0,
            "avg_win": 0.0, "avg_loss": 0.0, "expectancy": 0.0,
            "total_return": 0.0,
        }
    returns = [s.return_pct for s in signals]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    sorted_r = sorted(returns)
    mid = sorted_r[n // 2] if n % 2 == 1 else (
        (sorted_r[n // 2 - 1] + sorted_r[n // 2]) / 2)
    win_rate = len(wins) / n
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    expectancy = win_rate * avg_win + (1 - win_rate) * avg_loss
    return {
        "count": n,
        "win_rate": round(win_rate * 100, 1),
        "avg_return": round(sum(returns) / n, 2),
        "median_return": round(mid, 2),
        "best": round(max(returns), 2),
        "worst": round(min(returns), 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(expectancy, 2),
        "total_return": round(sum(returns), 2),
    }


def signals_to_dicts(signals: list[StrategySignal]) -> list[dict]:
    return [asdict(s) for s in signals]


# ---------------------------------------------------------------------------
# Filter: 主力集中度即將黃金交叉（候選掃描）
# ---------------------------------------------------------------------------

@dataclass
class ImminentCrossCandidate:
    """短期集中度即將上穿長期集中度的候選個股。"""
    stock_code: str
    stock_name: str
    trade_date: str
    close_price: float
    conc_short: float          # 當日短期集中度 (%)
    conc_long: float           # 當日長期集中度 (%)
    gap: float                 # long − short（正值代表還沒交叉）
    prev_gap: float            # 前一交易日的 gap
    narrowing: float           # prev_gap − gap（>0 = 縮窄中）
    eta_days: float | None     # 依 narrowing 速率推估幾日內交叉
    short_slope: float         # short 較前一日的變化量（正 = 上升）


def find_imminent_crossovers(
    grouped_rows: dict[str, list[dict]],
    trade_date: str,
    short_window: int = 5,
    long_window: int = 15,
    top_n: int = 15,
    max_gap_pct: float = 2.0,
    require_narrowing: bool = True,
    eta_cap: float = 30.0,
) -> list[ImminentCrossCandidate]:
    """掃所有個股，找出當日「短期集中度即將上穿長期集中度」的候選。

    條件：
      • 當日 short ≤ long（還沒交叉）
      • gap (= long − short) ≤ max_gap_pct
      • 當日「最近交易日」必須等於 trade_date（避免拿到停牌的舊資料）
      • 若 require_narrowing：要求 prev_gap > gap（gap 在收窄）

    集中度本身不限制正負 — 兩線皆負（賣壓中）時的即將交叉也算反轉候選。

    Args:
        grouped_rows: {stock_code: [broker_row, ...]}，可由
            ``itertools.groupby`` 或 dict 累積取得。每筆 broker_row 與
            ``db.get_broker_history_range`` 回傳格式相同。
        trade_date: 'yyyy-mm-dd'，當日。
        eta_cap: 推估天數上限，超過就顯示為 cap 值。

    Returns:
        排序好的候選清單（eta_days 升冪、無 eta 者按 gap 升冪排在後段）。
    """
    out: list[ImminentCrossCandidate] = []
    for code, rows in grouped_rows.items():
        if not rows:
            continue
        dates, by_date_net, vol_by_date, close_by_date = _aggregate_by_date(rows)
        n = len(dates)
        if n < long_window + 1:
            continue
        # 嚴格要求當日就是 trade_date（避免停牌資料）
        if dates[-1] != trade_date:
            continue

        # 取最後一日與前一交易日
        i = n - 1
        cur_dates_s = dates[i - short_window + 1: i + 1]
        cur_dates_l = dates[i - long_window + 1: i + 1]
        prev_dates_s = dates[i - short_window: i]
        prev_dates_l = dates[i - long_window: i]

        cs = _window_concentration(cur_dates_s, by_date_net, vol_by_date, top_n)
        cl = _window_concentration(cur_dates_l, by_date_net, vol_by_date, top_n)
        ps = _window_concentration(prev_dates_s, by_date_net, vol_by_date, top_n)
        pl = _window_concentration(prev_dates_l, by_date_net, vol_by_date, top_n)

        if cs > cl:
            continue  # 已經交叉，不算「即將」
        gap = cl - cs
        if gap > max_gap_pct:
            continue
        prev_gap = pl - ps
        narrowing = prev_gap - gap
        if require_narrowing and narrowing <= 0:
            continue

        # 推估天數 = 目前 gap / 每日收窄速率
        eta: float | None = None
        if narrowing > 0:
            raw_eta = gap / narrowing
            eta = round(min(raw_eta, eta_cap), 1)

        name = rows[0].get("stock_name") or code
        close = close_by_date.get(dates[i]) or 0.0
        out.append(ImminentCrossCandidate(
            stock_code=code,
            stock_name=name,
            trade_date=dates[i],
            close_price=round(close, 2),
            conc_short=round(cs, 2),
            conc_long=round(cl, 2),
            gap=round(gap, 2),
            prev_gap=round(prev_gap, 2),
            narrowing=round(narrowing, 2),
            eta_days=eta,
            short_slope=round(cs - ps, 2),
        ))

    # 排序：有 eta 的按 eta 升冪，沒 eta 的按 gap 升冪排後面
    out.sort(key=lambda c: (
        c.eta_days is None, c.eta_days if c.eta_days is not None else c.gap))
    return out


def candidates_to_dicts(
    cands: list[ImminentCrossCandidate],
) -> list[dict]:
    return [asdict(c) for c in cands]


# ---------------------------------------------------------------------------
# Filter: 放空當沖候選（高位階 + 主力出貨 + 月線下彎 + 帶寬大）
# ---------------------------------------------------------------------------

@dataclass
class ShortDayCandidate:
    """放空當沖候選個股。

    篩選邏輯：
      • 主10 (10日主力集中度) < conc_max（預設 0 → 主力近10日淨賣）
      • 帶寬 (布林通道帶寬 %) > band_min（預設 20 → 振幅大適合當沖）
      • 月線斜率 (%) < slope_max（預設 0 → 月線下彎）
      • 年線乖離 ≥ bias_min（預設 10% → 還在年線上方，強勢股）
      • 周/雙週/月/季 乖離 ≤ 對應弱勢門檻（短中期偏弱 → 黑K機會）
      • 依「位階」desc 排序（高位階 = 高基期 = 適合放空）
    """
    stock_code: str
    stock_name: str
    trade_date: str
    close_price: float
    conc_10: float              # 主10 (%)
    bb_bandwidth: float         # 布林帶寬 (%)
    ma20_slope: float           # 月線斜率 (%)
    rank_pos: float             # 位階 [-10, +10]
    amplitude: float            # 當日振幅 (%) = (high − low) / prev_close * 100
    ma6_bias: float | None      # 周線乖離 (%) — 資料不足回 None
    ma12_bias: float | None     # 雙週線乖離 (%)
    ma20_bias: float | None     # 月線乖離 (%)
    ma72_bias: float | None     # 季線乖離 (%)
    ma250_bias: float | None    # 年線乖離 (%)


def _price_metrics(
    price_rows: list[dict],
    bb_period: int = 20,
    bb_k: float = 2.0,
    rank_window: int = 60,
    ma_long: int = 250,
) -> dict | None:
    """計算放空當沖所需的價格端指標。

    price_rows 必須依日期升冪排序、最後一筆為 trade_date 當日。
    需要至少 bb_period + 1 筆才能算 MA 斜率與帶寬。
    """
    n = len(price_rows)
    if n < bb_period + 1:
        return None

    closes: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    for p in price_rows:
        c = _pf(p.get("close_price"))
        h = _pf(p.get("high_price"))
        lo = _pf(p.get("low_price"))
        if c is None:
            return None
        closes.append(c)
        highs.append(h if h is not None else 0.0)
        lows.append(lo if lo is not None else 0.0)

    today_close = closes[-1]
    prev_close = closes[-2]
    if today_close <= 0 or prev_close <= 0:
        return None

    # 振幅 (%)
    today_high = highs[-1]
    today_low = lows[-1]
    if today_high > 0 and today_low > 0:
        amp = (today_high - today_low) / prev_close * 100.0
    else:
        amp = 0.0

    # 布林通道（period=bb_period, k=bb_k）— 帶寬 = (upper − lower) / mid * 100
    win = closes[-bb_period:]
    mid = sum(win) / bb_period
    if mid <= 0:
        return None
    var = sum((x - mid) ** 2 for x in win) / bb_period
    sd = var ** 0.5
    bbw = (2 * bb_k * sd) / mid * 100.0

    # 月線斜率 (%) = (MA20[今] − MA20[昨]) / MA20[昨] * 100
    win_prev = closes[-bb_period - 1: -1]
    mid_prev = sum(win_prev) / bb_period
    slope = (mid - mid_prev) / mid_prev * 100.0 if mid_prev > 0 else 0.0

    # 位階：K 棒在布林通道內的相對位置
    #   上軌 (mid + k*sd) = +10、中軌 (mid) = 0、下軌 (mid - k*sd) = -10
    #   位階 = (close - mid) / (k*sd) * 10
    #   ≥ 8 為近期強勢，≤ -8 為近期弱勢；收盤跑出通道時可超出 ±10
    # 註：rank_window 參數已棄用，保留簽名以維持向後相容
    _ = rank_window  # noqa: F841 — 保留參數，但實際以 BB 計算
    band_half = bb_k * sd
    if band_half > 0:
        rank_pos = (today_close - mid) / band_half * 10.0
    else:
        rank_pos = 0.0

    # 各週期 MA 乖離 (%)
    def _bias(period: int) -> float | None:
        if n < period:
            return None
        ma = sum(closes[-period:]) / period
        if ma <= 0:
            return None
        return (today_close - ma) / ma * 100.0

    return {
        "amp": amp, "bbw": bbw, "slope": slope,
        "rank_pos": rank_pos,
        "ma6_bias": _bias(6),
        "ma12_bias": _bias(12),
        "ma20_bias": _bias(bb_period),
        "ma72_bias": _bias(72),
        "ma250_bias": _bias(ma_long),
        "today_close": today_close,
    }


def find_short_daytrade_candidates(
    grouped_broker_rows: dict[str, list[dict]],
    price_map: dict[str, list[dict]],
    trade_date: str,
    main_window: int = 10,
    top_n: int = 15,
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
    bb_period: int = 20,
    bb_k: float = 2.0,
    ma_long: int = 250,
) -> list[ShortDayCandidate]:
    """掃所有個股，找出放空當沖候選。

    Args:
        grouped_broker_rows: {stock_code: [broker_row, ...]}，跨足夠的
            交易日 (≥ main_window) 以便計算 10 日主力集中度。
        price_map: {stock_code: [price_row, ...]}，依日期升冪、含 OHLC。
        trade_date: 'yyyy-mm-dd'，當日。
        main_window: 主力集中度窗口（預設 10 日）。
        conc_max: 主10 上限（嚴格 <），預設 0。
        band_min: 帶寬下限（嚴格 >），預設 20。
        slope_max: 月線斜率上限（嚴格 <），預設 0。
        rank_window: 已棄用 — 位階改以布林通道上下軌為基準
            （上軌=+10、月線=0、下軌=-10）。參數保留以維持簽名相容。
        bias_min: 年線乖離下限（≥），預設 10%。
        bias{6,12,20,72}_max: 周/雙週/月/季 乖離上限（≤），代表「弱勢」門檻；
            預設依市場慣例：-3 / -4.5 / -7 / -11。
        use_bias_*: 對應乖離條件是否啟用；False 則略過該過濾，
            該 MA 資料不足也不會剔除候選。

    Returns:
        依位階 desc 排序的候選清單。
    """
    out: list[ShortDayCandidate] = []

    for code, price_rows in price_map.items():
        if not price_rows:
            continue
        # 嚴格要求當日就是 trade_date（避免停牌的舊資料）
        if str(price_rows[-1]["trade_date"])[:10] != trade_date:
            continue

        m = _price_metrics(
            price_rows, bb_period=bb_period, bb_k=bb_k,
            rank_window=rank_window, ma_long=ma_long)
        if m is None:
            continue

        # 帶寬 & 月線斜率（屬便宜計算，先過濾）
        if not (m["bbw"] > band_min):
            continue
        if not (m["slope"] < slope_max):
            continue
        # 年線乖離（勾選才套用；None = MA250 資料不足，啟用時視為不符）
        if use_bias_min:
            if m["ma250_bias"] is None or m["ma250_bias"] < bias_min:
                continue
        # 短中期 MA 乖離弱勢過濾（每條 MA 需勾選才套用）
        fail_short_bias = False
        for use, key, thr in (
            (use_bias6, "ma6_bias", bias6_max),
            (use_bias12, "ma12_bias", bias12_max),
            (use_bias20, "ma20_bias", bias20_max),
            (use_bias72, "ma72_bias", bias72_max),
        ):
            if not use:
                continue
            if m[key] is None or m[key] > thr:
                fail_short_bias = True
                break
        if fail_short_bias:
            continue

        # 主10：需要分點資料
        broker_rows = grouped_broker_rows.get(code, [])
        if not broker_rows:
            continue
        dates, by_date_net, vol_by_date, _ = _aggregate_by_date(broker_rows)
        if not dates or dates[-1] != trade_date:
            continue
        if len(dates) < main_window:
            continue
        conc_10 = _window_concentration(
            dates[-main_window:], by_date_net, vol_by_date, top_n)
        if not (conc_10 < conc_max):
            continue

        name = broker_rows[0].get("stock_name") or ""
        def _r(v):
            return round(v, 2) if v is not None else None

        out.append(ShortDayCandidate(
            stock_code=code,
            stock_name=name,
            trade_date=trade_date,
            close_price=round(m["today_close"], 2),
            conc_10=round(conc_10, 2),
            bb_bandwidth=round(m["bbw"], 2),
            ma20_slope=round(m["slope"], 2),
            rank_pos=round(m["rank_pos"], 2),
            amplitude=round(m["amp"], 2),
            ma6_bias=_r(m["ma6_bias"]),
            ma12_bias=_r(m["ma12_bias"]),
            ma20_bias=_r(m["ma20_bias"]),
            ma72_bias=_r(m["ma72_bias"]),
            ma250_bias=_r(m["ma250_bias"]),
        ))

    # 排序：位階 desc（高基期優先）
    out.sort(key=lambda c: c.rank_pos, reverse=True)
    return out


# ---------------------------------------------------------------------------
# 放空候選的輔助訊號（icon 用）
# ---------------------------------------------------------------------------


@dataclass
class ShortSetupSignals:
    """放空候選的輔助訊號 — 顯示於策略四結果表的圖示欄。

    bearish_*：助空訊號（放空可能性 ↑）
    bullish_*：反向警訊（放空可能性 ↓，可能被反彈軋到）
    """
    bearish_next_flip_buy: bool       # 隔日沖分點當日大買進場
    bearish_foreign_sell_streak: int  # 外資連賣天數（≥ min_streak 才算）
    bearish_dealer_dump: bool         # 自營當日大賣（自行+避險）
    bearish_holder_surge: bool        # 集保戶數週增加
    bearish_margin_chasing: bool      # 融資逆勢追高（主力賣 + 融資增）
    bullish_trust_first_buy: bool     # 投信第一天買（追價警訊）
    bullish_big_pct_rising: bool      # 大戶持股增加（籌碼集中向大戶）
    bullish_holder_drop: bool         # 集保戶數週下降
    bullish_short_squeeze_risk: bool  # 券資比 ≥ 30%（軋空風險）


# ---- broker_tags 延後 import：strategy_eval_service 不該強制連到 broker_tags
#      若 import 失敗（極不常見），全部隔日沖判斷會回 False，不影響主流程 ----
try:
    from services.broker_tags import TAG_NEXT, get_broker_tags
    _HAS_BROKER_TAGS = True
except Exception:  # pragma: no cover
    _HAS_BROKER_TAGS = False
    TAG_NEXT = ""
    def get_broker_tags(_name):  # type: ignore
        return []


def _bearish_next_flip_buy(
    broker_rows: list[dict], signal_date: str,
    vol_share_min: float,
) -> bool:
    """隔日沖分點當日淨買 / 成交量 ≥ vol_share_min%。

    注意：``total_volume`` 在 DB schema 是 NVARCHAR（從 StockDailySummary
    join 過來，可能含千分位逗號），必須先用 _pi 解析成 int。
    """
    if not _HAS_BROKER_TAGS:
        return False
    total_vol = 0
    next_net = 0
    for r in broker_rows:
        if str(r.get("trade_date"))[:10] != signal_date:
            continue
        tv = _pi(r.get("total_volume"))
        if tv > total_vol:
            total_vol = tv
        if TAG_NEXT in get_broker_tags(r.get("broker_name") or ""):
            bv = _pi(r.get("buy_volume"))
            sv = _pi(r.get("sell_volume"))
            next_net += bv - sv
    if total_vol <= 0 or next_net <= 0:
        return False
    return (next_net / total_vol * 100.0) >= vol_share_min


def _foreign_sell_streak(
    insti_history: list[dict], signal_date: str,
) -> int:
    """外資在 signal_date（含當日）之前連續 net < 0 的天數。
    insti_history 升冪；最後一筆必須等於 signal_date 才採信。"""
    if not insti_history:
        return 0
    if str(insti_history[-1].get("trade_date"))[:10] != signal_date:
        return 0
    streak = 0
    for r in reversed(insti_history):
        if (r.get("foreign_net") or 0) < 0:
            streak += 1
        else:
            break
    return streak


def _trust_first_buy(
    insti_history: list[dict], signal_date: str, lookback: int,
) -> bool:
    """投信當日 net > 0，且前 lookback 個交易日的 net 都 ≤ 0。"""
    if not insti_history:
        return False
    if str(insti_history[-1].get("trade_date"))[:10] != signal_date:
        return False
    if (insti_history[-1].get("trust_net") or 0) <= 0:
        return False
    prev = insti_history[-lookback - 1: -1]
    if len(prev) < lookback:
        return False  # 資料不足，不主張「第一天」
    for r in prev:
        if (r.get("trust_net") or 0) > 0:
            return False
    return True


def _dealer_dump_today(
    insti_history: list[dict], signal_date: str, min_shares: int,
) -> bool:
    """自營（自行 + 避險）當日合計淨賣 ≥ min_shares 股。"""
    if not insti_history:
        return False
    if str(insti_history[-1].get("trade_date"))[:10] != signal_date:
        return False
    r = insti_history[-1]
    net = (r.get("dealer_self_net") or 0) + (r.get("dealer_hedge_net") or 0)
    return net <= -min_shares


def _margin_chasing_up(
    margin_history: list[dict], signal_date: str,
    lookback_days: int = 5, min_growth_pct: float = 5.0,
) -> bool:
    """近 N 個交易日融資餘額增加 ≥ min_growth_pct%。

    margin_history：該股的融資融券歷史（升冪），需含 trade_date、margin_balance。
    最後一筆日期必須等於 signal_date 才採信。
    """
    if not margin_history:
        return False
    if str(margin_history[-1].get("trade_date"))[:10] != signal_date:
        return False
    if len(margin_history) < lookback_days + 1:
        return False
    base = margin_history[-lookback_days - 1].get("margin_balance") or 0
    now = margin_history[-1].get("margin_balance") or 0
    if base <= 0:
        return False
    return (now - base) / base * 100.0 >= min_growth_pct


def _short_squeeze_ratio(
    margin_history: list[dict], signal_date: str,
    ratio_min: float = 30.0,
) -> bool:
    """當日券資比 ≥ ratio_min %。
    券資比 = short_balance / margin_balance × 100。
    """
    if not margin_history:
        return False
    if str(margin_history[-1].get("trade_date"))[:10] != signal_date:
        return False
    r = margin_history[-1]
    margin = r.get("margin_balance") or 0
    short = r.get("short_balance") or 0
    if margin <= 0:
        return False
    return (short / margin * 100.0) >= ratio_min


def _holder_count_change_pct(
    holder_count_history: list[dict], signal_date: str,
    lookback_weeks: int = 1,
) -> float | None:
    """近 lookback_weeks 週的戶數變化 (%)。資料不足回 None。"""
    if not holder_count_history:
        return None
    latest_idx = -1
    for i, h in enumerate(holder_count_history):
        if str(h.get("report_date"))[:10] <= signal_date:
            latest_idx = i
        else:
            break
    earlier_idx = latest_idx - lookback_weeks
    if latest_idx < 1 or earlier_idx < 0:
        return None
    latest = holder_count_history[latest_idx]
    earlier = holder_count_history[earlier_idx]
    base = earlier.get("total_holders") or 0
    if base <= 0:
        return None
    return (latest["total_holders"] - base) / base * 100.0


def compute_short_setup_signals(
    broker_rows: list[dict],
    insti_history: list[dict],
    holder_pct_history: list[dict],
    holder_count_history: list[dict],
    signal_date: str,
    margin_history: list[dict] | None = None,
    *,
    next_flip_share_min: float = 2.0,
    foreign_sell_min_streak: int = 3,
    trust_first_buy_lookback: int = 5,
    dealer_dump_shares_min: int = 200_000,
    holder_surge_pct: float = 1.0,
    holder_drop_pct: float = 0.5,
    big_rising_pct: float = 0.0,
    big_rising_weeks: int = 4,
    margin_chase_days: int = 5,
    margin_chase_pct: float = 5.0,
    short_squeeze_ratio_min: float = 30.0,
) -> ShortSetupSignals:
    """計算放空輔助訊號。

    Args:
        broker_rows: 該股的 broker 歷史（含 signal_date 當日）。
        insti_history: 該股的 InstiDailyTrade 升冪。
        holder_pct_history: 該股的 TDCC 週報（升冪），含 retail_pct / big_pct。
        holder_count_history: 該股的 TDCC 戶數總和週報（升冪），含 total_holders。
        signal_date: 'yyyy-mm-dd'。
        next_flip_share_min: 隔日沖分點淨買佔成交量 ≥ X%。
        foreign_sell_min_streak: 外資連賣 ≥ N 天才算助空。
        trust_first_buy_lookback: 投信第一天買要求前 N 天皆 ≤ 0。
        dealer_dump_shares_min: 自營單日合計淨賣 ≥ N 股（200,000 股 = 200 張）。
        holder_surge_pct: 戶數週變化 ≥ +X% 算助空（爆增）。
        holder_drop_pct: 戶數週變化 ≤ −X% 算反向警訊（穩定）。
        big_rising_pct: 大戶比例上升門檻；搭配 big_rising_weeks 比對。
        big_rising_weeks: 大戶比例比對的週期。
    """
    foreign_streak = _foreign_sell_streak(insti_history, signal_date)

    # 戶數變化（一次取，雙向判斷）
    hc_change = _holder_count_change_pct(
        holder_count_history, signal_date, lookback_weeks=1)
    holder_surge = hc_change is not None and hc_change >= holder_surge_pct
    holder_drop = hc_change is not None and hc_change <= -abs(holder_drop_pct)

    # 大戶比例上升（沿用既有 chip_change_at_date）
    chip_info = chip_change_at_date(
        holder_pct_history, signal_date, big_rising_weeks)
    big_rising = (chip_info is not None
                  and chip_info["big_delta"] >= big_rising_pct
                  and chip_info["big_delta"] > 0)

    margin = margin_history or []
    return ShortSetupSignals(
        bearish_next_flip_buy=_bearish_next_flip_buy(
            broker_rows, signal_date, next_flip_share_min),
        bearish_foreign_sell_streak=(
            foreign_streak if foreign_streak >= foreign_sell_min_streak else 0),
        bearish_dealer_dump=_dealer_dump_today(
            insti_history, signal_date, dealer_dump_shares_min),
        bearish_holder_surge=holder_surge,
        bearish_margin_chasing=_margin_chasing_up(
            margin, signal_date, margin_chase_days, margin_chase_pct),
        bullish_trust_first_buy=_trust_first_buy(
            insti_history, signal_date, trust_first_buy_lookback),
        bullish_big_pct_rising=big_rising,
        bullish_holder_drop=holder_drop,
        bullish_short_squeeze_risk=_short_squeeze_ratio(
            margin, signal_date, short_squeeze_ratio_min),
    )


# ---------------------------------------------------------------------------
# Backtest: 策略四（放空當沖）回測
# ---------------------------------------------------------------------------

@dataclass
class ShortDayBacktestSignal:
    """放空當沖回測單一進場訊號 + 後續報酬。

    報酬定義（放空）：
      • 進場：訊號日收盤 (借券放空)
      • 出場：訊號日後第 N 個交易日收盤 (回補)
      • return_pct = (entry − exit) / entry × 100
        — 正值代表股價下跌 → 放空獲利
    """
    stock_code: str
    stock_name: str
    signal_date: str
    exit_date: str
    hold_days: int
    rank_pos: float
    conc_10: float
    bb_bandwidth: float
    ma20_slope: float
    ma20_bias: float | None
    ma250_bias: float | None
    entry_price: float
    exit_price: float
    return_pct: float


def backtest_short_daytrade(
    all_broker_grouped: dict[str, list[dict]],
    all_price_map: dict[str, list[dict]],
    trading_dates: list[str],
    hold_days: int = 1,
    main_window: int = 10,
    top_n: int = 15,
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
    cancel_flag=None,
    progress_cb=None,
) -> list[ShortDayBacktestSignal]:
    """對每個交易日跑策略四 → 收集所有訊號 → 算放空後續報酬。

    Args:
        all_broker_grouped: {stock_code: [broker_row, ...]} 跨足整個回測
            範圍（含進場端的歷史回溯）。每筆 row 需有 trade_date / broker_name /
            buy_volume / sell_volume / total_volume / stock_name。
        all_price_map: {stock_code: [price_row, ...]} 同上，依日期升冪排序。
            每筆需有 trade_date / close_price / high_price / low_price。
        trading_dates: 要回測的交易日清單，升冪排序，僅含 [start, end] 區間
            內的日期。
        hold_days: 持有交易日數（D → D+N），預設 1（隔日回補）。
        其餘策略參數同 ``find_short_daytrade_candidates``。
        cancel_flag: 可選的 callable，回 True 即停止。
        progress_cb: 可選的 callable(done, total)，每完成一個交易日呼叫。

    Returns:
        所有訊號的 list（依訊號日升冪排序；caller 視需要再排序）。
    """
    results: list[ShortDayBacktestSignal] = []

    # 預建 code → {date: idx} 以便快速找 D+N 出場價
    price_date_idx: dict[str, dict[str, int]] = {}
    for code, prices in all_price_map.items():
        price_date_idx[code] = {
            str(p["trade_date"])[:10]: i for i, p in enumerate(prices)
        }

    # 每檔股票一個遊標，沿著 trading_dates 一路推進，O(N+M+D)
    broker_ptrs: dict[str, int] = {code: 0 for code in all_broker_grouped}
    price_ptrs: dict[str, int] = {code: 0 for code in all_price_map}

    n_dates = len(trading_dates)
    for i, D in enumerate(trading_dates):
        if cancel_flag and cancel_flag():
            break

        # 推進 broker 遊標：把 trade_date ≤ D 的全收進來
        grouped_D: dict[str, list[dict]] = {}
        for code, rows in all_broker_grouped.items():
            p = broker_ptrs[code]
            while p < len(rows) and str(rows[p]["trade_date"])[:10] <= D:
                p += 1
            broker_ptrs[code] = p
            if p > 0:
                grouped_D[code] = rows[:p]

        # 推進 price 遊標
        price_D: dict[str, list[dict]] = {}
        for code, prices in all_price_map.items():
            p = price_ptrs[code]
            while p < len(prices) and str(prices[p]["trade_date"])[:10] <= D:
                p += 1
            price_ptrs[code] = p
            if p > 0:
                price_D[code] = prices[:p]

        cands = find_short_daytrade_candidates(
            grouped_D, price_D, D,
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

        # 對每個候選算出場價
        for c in cands:
            idx_map = price_date_idx.get(c.stock_code, {})
            D_idx = idx_map.get(D)
            if D_idx is None:
                continue
            exit_idx = D_idx + hold_days
            prices = all_price_map[c.stock_code]
            if exit_idx >= len(prices):
                continue  # 出場日尚未到（資料不夠）
            exit_row = prices[exit_idx]
            exit_price = _pf(exit_row.get("close_price"))
            if exit_price is None or exit_price <= 0:
                continue
            entry_price = c.close_price
            if entry_price <= 0:
                continue
            # 放空報酬：股價下跌 → 正值
            ret = (entry_price - exit_price) / entry_price * 100.0
            results.append(ShortDayBacktestSignal(
                stock_code=c.stock_code,
                stock_name=c.stock_name,
                signal_date=D,
                exit_date=str(exit_row["trade_date"])[:10],
                hold_days=hold_days,
                rank_pos=c.rank_pos,
                conc_10=c.conc_10,
                bb_bandwidth=c.bb_bandwidth,
                ma20_slope=c.ma20_slope,
                ma20_bias=c.ma20_bias,
                ma250_bias=c.ma250_bias,
                entry_price=round(entry_price, 2),
                exit_price=round(exit_price, 2),
                return_pct=round(ret, 2),
            ))

        if progress_cb:
            progress_cb(i + 1, n_dates)

    return results


# ---------------------------------------------------------------------------
# 三大法人連續買超 streak（給策略三搭配集中度過濾使用）
# ---------------------------------------------------------------------------

INSTI_TYPES = ("foreign", "trust", "dealer")
INSTI_LABELS = {"foreign": "外資", "trust": "投信", "dealer": "自營"}


def _insti_net(row: dict, type_key: str) -> int:
    """取出指定法人在這筆 row 的當日淨買賣（張數）。

    ``dealer`` = 自營商自行 + 自營商避險（市場慣例的「自營合計」）。
    """
    if type_key == "foreign":
        return row.get("foreign_net") or 0
    if type_key == "trust":
        return row.get("trust_net") or 0
    if type_key == "dealer":
        return ((row.get("dealer_self_net") or 0)
                + (row.get("dealer_hedge_net") or 0))
    return 0


def chip_change_at_date(
    dist_history: list[dict],
    signal_date: str,
    lookback_weeks: int = 4,
) -> dict | None:
    """比對訊號日附近的 TDCC 週報，回傳大戶/散戶比例變化。

    Args:
        dist_history: 同一檔股票的週報歷史，需依日期升冪排序。
            每筆: ``{report_date, retail_pct, big_pct}``。
        signal_date: 'yyyy-mm-dd'。會用「≤ signal_date 的最後一份週報」
            作為「現在」。
        lookback_weeks: 往前看幾份週報當「之前」。

    Returns:
        ``{big_now, big_then, big_delta, retail_now, retail_then,
        retail_delta, latest_date, earlier_date}`` 或 None
        （週報資料不足以比對時）。
    """
    if not dist_history or lookback_weeks < 1:
        return None
    # 找 ≤ signal_date 的最後一份週報
    latest_idx = -1
    for i, snap in enumerate(dist_history):
        if str(snap["report_date"])[:10] <= signal_date:
            latest_idx = i
        else:
            break
    if latest_idx < 0:
        return None
    earlier_idx = latest_idx - lookback_weeks
    if earlier_idx < 0:
        return None

    latest = dist_history[latest_idx]
    earlier = dist_history[earlier_idx]
    return {
        "big_now": latest["big_pct"],
        "big_then": earlier["big_pct"],
        "big_delta": round(latest["big_pct"] - earlier["big_pct"], 2),
        "retail_now": latest["retail_pct"],
        "retail_then": earlier["retail_pct"],
        "retail_delta": round(
            latest["retail_pct"] - earlier["retail_pct"], 2),
        "latest_date": str(latest["report_date"])[:10],
        "earlier_date": str(earlier["report_date"])[:10],
    }


def chip_concentration_passes(
    chip_info: dict,
    min_big_gain: float = 0.0,
) -> bool:
    """檢查「大戶% 上升」條件是否成立（籌碼集中於大戶）。

    門檻為絕對值；例如 min_big_gain=1.0 代表大戶必須上升 ≥ 1%。
    預設門檻為 0 → 只要 ≥ 0（大戶持股有增加或持平）就過。
    """
    return chip_info["big_delta"] >= abs(min_big_gain)


def insti_buy_streak(rows_for_stock: list[dict], trade_date: str,
                      type_key: str) -> int:
    """指定法人在 trade_date 之前（含當日）連續買超的天數。

    rows_for_stock 必須是同一檔股票的 InstiDailyTrade 列、依日期升冪排序。
    嚴格要求最後一筆日期 = trade_date —— 否則代表該股當日沒法人資料，
    回 0（不採信過時 streak）。

    遇到「淨買 ≤ 0」即中斷，從尾巴往前數。
    """
    if not rows_for_stock:
        return 0
    if str(rows_for_stock[-1]["trade_date"])[:10] != trade_date:
        return 0
    streak = 0
    for r in reversed(rows_for_stock):
        if _insti_net(r, type_key) > 0:
            streak += 1
        else:
            break
    return streak
