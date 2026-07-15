"""事件驅動微觀結構回測模組（MicrostructureBacktester）。

用永豐歷史逐筆資料（Tick，含 level-1 買賣價量）重放，套用與即時追蹤相同的
OBI / VPIN / 大單 偵測邏輯，模擬做多/做空進出場並統計績效。

策略邏輯（對稱）
----------------
🟢 做多進場（同時滿足，且在 confluence 窗口內）：
   1. OBI 持續 > obi_threshold（買盤蓄勢，setup_side == 'buy'）
   2. 連續 ≥2 個成交量桶買方推進 > buy_push_ratio
   3. 出現外盤連續大單且價格向上跳檔（起漲點 attack buy）
🔴 做空進場（鏡像）：
   1. OBI 持續 < -obi_threshold（賣壓蓄勢，setup_side == 'sell'）
   2. 連續 ≥2 個量桶賣方推進 > buy_push_ratio
   3. 內盤連續大單且價格向下跳檔（起跌點 attack sell）

出場：固定停利 / 固定停損 / 移動停損 / OBI 反轉（蓄勢方向翻向，與進場對稱）/ 反向訊號。

防未來函數（Look-ahead bias）
------------------------------
每筆 tick 只用「當下與過去」的資訊決策，成交價用「當前 tick」的對手檔位
（做多吃 Ask、做空砍 Bid）再加滑價，不偷看下一筆。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

from services.microstructure_service import MicrostructureEngine, MicroConfig, Alert
from services.trend_filter_service import daily_trend_bias, IntradayTrendFilter

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
#  台股跳動單位（tick size）
# --------------------------------------------------------------------------- #

def tw_tick_size(price: float) -> float:
    """台股股票各價格區間的最小跳動單位。"""
    if price < 10:
        return 0.01
    if price < 50:
        return 0.05
    if price < 100:
        return 0.1
    if price < 500:
        return 0.5
    if price < 1000:
        return 1.0
    return 5.0


# --------------------------------------------------------------------------- #
#  參數 / 結果資料結構
# --------------------------------------------------------------------------- #

@dataclass
class BacktestParams:
    # 回測策略：
    #   'confluence'   — K-of-N 合流計分（多空對稱，原策略）
    #   'attack_point' — 起漲/起跌點（順勢，照即時監控買賣點邏輯）：
    #                    做多→強起漲點(attack buy)買進、遇強賣訊出場；
    #                    做空→強起跌點(attack sell)沖賣、遇強買訊補回。方向由 allow_long/short 決定。
    strategy: str = "confluence"
    allow_long: bool = True
    allow_short: bool = True
    confluence_window: int = 40      # 各訊號的「新鮮度」窗口：最近 N 筆 tick 內成立才算數
    # --- 進場：K-of-N 合流計分（取代舊版三條件硬性 AND，避免過度嚴格跑不出訊號）---
    entry_min_conditions: int = 2    # {OBI蓄勢, VPIN連續桶, 大單起漲} 至少幾項成立即進場（3=最嚴、1=最鬆）
    min_consec_buckets: int = 1      # VPIN 條件：需連續幾個量桶同向推進（舊版硬寫 2）
    require_attack: bool = False     # 是否「強制」需有大單起漲訊號（無論 K）
    require_breakout: bool = False   # 是否「強制」需價格突破近 N 筆高/低點
    invert_signals: bool = False     # 反向(fade)模式：買訊號叢集開空、賣訊號叢集開多
                                     #（微觀訊號常標的是短線頂/底，fade 反而有 edge）
                                     # 預設關閉＝順勢：買訊號做多(起漲點買)、賣訊號做空(起跌點空)
    breakout_lookback: int = 120     # 突破判斷回看的 tick 數（近似「近一分鐘高低」）
    debug_every: int = 1000          # 每 N 筆 tick 記一次診斷快照（0=關閉），用於觀察哪個門檻太嚴
    # --- 雙層濾網：上層決定方向，微觀訊號只在閘門開啟時扣板機 ---
    daily_trend_filter: bool = False   # 日線趨勢方向濾網（需 VM 傳入回測日前的日收盤）
    daily_ma_period: int = 20          # 日線 SMA 期數
    intraday_filter: str = "off"       # 分線濾網：'off' | 'ma' | 'squeeze'
    bar_seconds: int = 300             # 分線 bar 長度（秒），預設 5 分鐘
    intraday_ma_period: int = 10       # 分線 MA 期數（mode='ma'）
    bb_period: int = 20                # 布林通道期數（mode='squeeze'）
    bb_k: float = 2.0                  # 布林通道標準差倍數
    squeeze_factor: float = 0.6        # 擠壓判定：帶寬 ≤ factor×近期平均帶寬
    squeeze_lookback: int = 20         # 擠壓帶寬回看 bar 數
    take_profit_pct: float = 0.5     # 固定停利 %
    stop_loss_pct: float = 0.3       # 固定停損 %
    trailing_pct: float = 0.0        # 移動停損 %（0 = 關閉）
    exit_on_obi_flip: bool = True    # OBI 蓄勢方向翻向即出場（與進場對稱，非單純穿越 0）
    exit_on_reverse: bool = True     # 出現反向 attack 訊號即出場
    slippage_ticks: float = 1.0      # 進出場各加幾個 tick 的滑價
    fee_rate: float = 0.001425       # 手續費率（單邊）
    fee_discount: float = 0.3        # 手續費折數（電子下單常見 3 折）
    tax_rate: float = 0.0015         # 證交稅（當沖賣出 0.15%）
    min_hold_ticks: int = 3          # 進場後最少持有幾筆才允許出場（濾雜訊）
    sar_skip_open_minutes: float = 10.0  # 點火進出策略：開盤前 N 分鐘不交易（開盤量價未穩）
    sar_exit_consecutive: int = 4    # 點火進出策略：需連續 N 筆反向點火（支撐轉強/轉弱）才出場
    sar_tp_ticks: int = 4            # 點火進出策略：無反向訊號時，可實現獲利(對手檔+滑價) > N 跳即了結


@dataclass
class TradeRecord:
    direction: str          # 'long' | 'short'
    entry_time: str
    entry_price: float
    exit_time: str
    exit_price: float
    ret_pct: float          # 淨報酬率（含手續費/稅/滑價）%
    exit_reason: str
    hold_ticks: int

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class BacktestResult:
    code: str = ""
    date: str = ""
    tick_count: int = 0
    trades: list[TradeRecord] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)  # NAV 序列（起始 1.0）
    # 績效指標
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    total_return_pct: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    expectancy_pct: float = 0.0
    long_trades: int = 0
    short_trades: int = 0
    error: str = ""
    # --- 診斷：幫忙看「零訊號」到底卡在哪個條件 ---
    diag: dict = field(default_factory=dict)
    # --- 反手(SAR)策略：完整訊號歷程（每個點火訊號 + 系統動作）---
    sar_events: list = field(default_factory=list)

    def summary_dict(self) -> dict:
        d = asdict(self)
        d.pop("trades", None)
        d.pop("equity_curve", None)
        d.pop("sar_events", None)
        return d


# --------------------------------------------------------------------------- #
#  Backtester
# --------------------------------------------------------------------------- #

class MicrostructureBacktester:
    """事件驅動回測器：重放逐筆資料 → 產生交易 → 統計績效。"""

    def __init__(self, cfg: MicroConfig, params: BacktestParams,
                 daily_closes: list[float] | None = None):
        self.cfg = cfg
        self.p = params
        # 日線收盤序列（回測日之前，最舊在前）供日線趨勢濾網用
        self.daily_closes = daily_closes or []

    # ---- 主流程 ----
    def run(self, ticks: list[dict], code: str = "", date: str = "") -> BacktestResult:
        res = BacktestResult(code=code, date=date, tick_count=len(ticks))
        if not ticks:
            res.error = "無逐筆資料"
            return res

        # 每筆 tick 的 alert 收集器（不看未來）
        tick_alerts: list[Alert] = []
        engine = MicrostructureEngine(
            self.cfg, on_alert=lambda a: tick_alerts.append(a))

        # 訊號新鮮度追蹤（記錄最後一次成立的 tick index）
        last_attack_buy = -10**9
        last_attack_sell = -10**9

        # 部位狀態
        pos = 0                 # 0 flat, 1 long, -1 short
        entry_price = 0.0
        entry_i = 0
        entry_time = ""
        peak = 0.0              # 移動停損用：多單記最高、空單記最低
        nav = 1.0
        res.equity_curve.append(nav)
        # 點火進出策略狀態：進場(啟動)只認「起漲/起跌點(強)」，方向照該訊號、不反手。
        #   opp_streak = 持倉時「反向訊號(點火或起漲/起跌)」連續數，達門檻→出場、回到空手。
        opp_streak = 0
        last_entry_dir = 0     # 最近一次進場方向（診斷用）：+1 多 / -1 空

        W = self.p.confluence_window
        K = max(1, min(3, self.p.entry_min_conditions))
        min_buckets = max(1, self.p.min_consec_buckets)

        # 反向(fade)模式：進場方向鏡射；且順勢式訊號出場(OBI反轉/反向訊號)對 fade
        # 無意義會扯後腿，故自動關閉，只留停利/停損/移動停損/收盤。
        inv = self.p.invert_signals
        exit_obi_flip = self.p.exit_on_obi_flip and not inv
        exit_reverse = self.p.exit_on_reverse and not inv

        # --- 上層濾網 ---
        # 日線方向偏向（整日固定）
        daily_long_ok, daily_short_ok, daily_note = True, True, ""
        if self.p.daily_trend_filter:
            daily_long_ok, daily_short_ok, daily_note = daily_trend_bias(
                self.daily_closes, self.p.daily_ma_period)
        # 分線趨勢/擠壓濾網（盤中逐 tick 更新）
        intraday_tf: IntradayTrendFilter | None = None
        if self.p.intraday_filter in ("ma", "squeeze"):
            intraday_tf = IntradayTrendFilter(
                mode=self.p.intraday_filter, bar_seconds=self.p.bar_seconds,
                ma_period=self.p.intraday_ma_period, bb_period=self.p.bb_period,
                bb_k=self.p.bb_k, squeeze_factor=self.p.squeeze_factor,
                squeeze_lookback=self.p.squeeze_lookback)
        filter_blocked_long = 0   # 診斷：微觀訊號成立但被濾網擋下的次數
        filter_blocked_short = 0

        # 突破判斷：維護近 breakout_lookback 筆收盤，判斷是否創窗口新高/低
        from collections import deque
        closes: deque[float] = deque(maxlen=max(2, self.p.breakout_lookback))

        # 診斷計數器：在「空手」時統計各條件成立次數與合流分數分布
        flat_ticks = 0
        cond_hits = {"obi_buy": 0, "obi_sell": 0, "vpin_buy": 0, "vpin_sell": 0,
                     "attack_buy": 0, "attack_sell": 0, "brk_up": 0, "brk_dn": 0}
        score_hist_long = {0: 0, 1: 0, 2: 0, 3: 0}
        score_hist_short = {0: 0, 1: 0, 2: 0, 3: 0}
        obi_sum = 0.0
        max_volume = 0.0
        max_consec = 0
        atk_buy_signals = 0     # 起漲/起跌點策略：強起漲點次數
        atk_sell_signals = 0    # 起漲/起跌點策略：強起跌點次數
        strong_buy_signals = 0  # 反手(SAR)策略：買方轉強(點火/攻擊)次數
        strong_sell_signals = 0  # 反手(SAR)策略：賣方轉強次數
        ignite_buy_signals = 0   # 反手(SAR)策略：明確買方點火(momentum)次數
        ignite_sell_signals = 0  # 反手(SAR)策略：明確賣方點火(momentum)次數
        sar_long_entries = 0    # 反手(SAR)策略：翻多進場次數
        sar_short_entries = 0   # 反手(SAR)策略：翻空(沖賣)進場次數

        # 反手(SAR)：開盤前 N 分鐘不交易 —— 以第一筆有效 ts 為開盤基準 + N 分鐘
        open_ts = next((int(tk["ts"]) for tk in ticks
                        if tk.get("ts") is not None), None)
        no_trade_until = (open_ts + int(self.p.sar_skip_open_minutes * 60 * 1e9)
                          if open_ts is not None else None)

        for i, tk in enumerate(ticks):
            bid = tk["bid_price"]
            ask = tk["ask_price"]
            close = tk["close"]
            t_str = self._ts_str(tk.get("ts"))

            # --- 餵引擎（先 bidask 再 tick），收集本筆 alert ---
            tick_alerts.clear()
            if bid > 0 and ask > 0:
                engine.on_bidask({
                    "bid_price": [bid], "bid_volume": [tk["bid_volume"]],
                    "ask_price": [ask], "ask_volume": [tk["ask_volume"]],
                })
            engine.on_tick({
                "code": code, "close": close, "volume": tk["volume"],
                "tick_type": tk["tick_type"],
            })

            # 起漲/起跌點策略用的本筆訊號旗標
            attack_buy_now = False    # 強起漲點（attack buy）→ 做多進場
            attack_sell_now = False   # 強起跌點（attack sell）→ 做空進場
            strong_buy_now = False    # 買方訊號轉強（起漲攻擊/動能點火）→ 空單補回
            strong_sell_now = False   # 賣方訊號轉強（起跌殺盤/動能點火）→ 多單獲利了結
            ignite_buy_now = False    # 明確「動能點火」買訊（momentum,強）→ 反手做多依據
            ignite_sell_now = False   # 明確「動能點火」賣訊（momentum,強）→ 反手做空依據
            for a in tick_alerts:
                if a.kind == "attack" and a.side == "buy":
                    last_attack_buy = i
                    attack_buy_now = True
                elif a.kind == "attack" and a.side == "sell":
                    last_attack_sell = i
                    attack_sell_now = True
                if a.kind == "momentum":  # 動能點火（明確的買/賣點火訊號）
                    if a.side == "buy":
                        ignite_buy_now = True
                    elif a.side == "sell":
                        ignite_sell_now = True
                if a.level == "strong":  # 動能點火/起漲起跌攻擊皆為 strong
                    if a.side == "buy":
                        strong_buy_now = True
                    elif a.side == "sell":
                        strong_sell_now = True
            if attack_buy_now:
                atk_buy_signals += 1
            if attack_sell_now:
                atk_sell_signals += 1
            if strong_buy_now:
                strong_buy_signals += 1
            if strong_sell_now:
                strong_sell_signals += 1
            if ignite_buy_now:
                ignite_buy_signals += 1
            if ignite_sell_now:
                ignite_sell_signals += 1

            snap = engine.snapshot()
            obi = snap["obi5"]

            # 突破用收盤序列（用「加入本筆前」的窗口做為過去高/低基準）
            prev_high = max(closes) if closes else close
            prev_low = min(closes) if closes else close
            closes.append(close)

            # 上層分線濾網逐 tick 餵入
            if intraday_tf is not None:
                intraday_tf.update(tk.get("ts"), close)

            # 診斷running stats
            obi_sum += obi
            if tk["volume"] > max_volume:
                max_volume = tk["volume"]
            mc = max(snap["consec_buy_buckets"], snap["consec_sell_buckets"])
            if mc > max_consec:
                max_consec = mc

            # ============ 點火進出策略（不反手；確認式出場）============
            # 進場(啟動)：只認「起漲點→做多 / 起跌點→做空」(attack，強)；方向照該訊號。
            #   每次啟動(含止盈後再進場)都需起漲/起跌點；動能點火不啟動。
            # 出場：做空遇連續 N 筆反向「支撐轉強」、做多遇連續 N 筆反向「支撐轉弱」
            #   （點火或起漲/起跌皆算）→ 補回/賣出；或無反向訊號時可實現獲利 > N 跳
            #   → 獲利了結。出場後回到空手，等下一個起漲/起跌點再啟動。
            if self.p.strategy == "sar_flip":
                # 進出訊號＝「動能點火」或「起漲/起跌點(attack)」擇一觸發即算。
                # 同一 tick 買賣兩邊皆有訊號＝矛盾，不動作。
                trig_buy = ignite_buy_now or attack_buy_now
                trig_sell = ignite_sell_now or attack_sell_now
                sig_side = ""
                if trig_buy and not trig_sell:
                    sig_side = "buy"
                elif trig_sell and not trig_buy:
                    sig_side = "sell"
                # 訊號名稱與強度（與監控買賣點一致）：
                #   起漲/起跌點(attack)=「強」；動能點火(momentum)=「中」，OBI 蓄勢同向升「強」。
                sig_name = ""
                sig_strength = ""
                sig_is_attack = False   # 是否為起漲/起跌點（進場啟動的必要條件）
                if sig_side == "buy":
                    sig_is_attack = attack_buy_now
                    sig_name = "買方起漲點" if attack_buy_now else "買方點火"
                    sig_strength = "強" if (attack_buy_now
                                            or engine.ob.setup_side == "buy") else "中"
                elif sig_side == "sell":
                    sig_is_attack = attack_sell_now
                    sig_name = "賣方起跌點" if attack_sell_now else "賣方點火"
                    sig_strength = "強" if (attack_sell_now
                                            or engine.ob.setup_side == "sell") else "中"

                def _ev(action: str, detail: str, traded: bool, ret=None):
                    res.sar_events.append({
                        "time": t_str, "side": sig_side,
                        "signal": sig_name, "strength": sig_strength,
                        "price": round(close, 2), "action": action,
                        "detail": detail, "traded": traded,
                        "ret_pct": None if ret is None else round(ret, 4)})

                def _maybe_tp() -> bool:
                    """無反向訊號出場時的保險：以「可實現出場價(對手檔+滑價)」衡量，
                    實際落袋獲利 > sar_tp_ticks 跳即獲利了結。每筆 tick 都檢查，回傳是否已出場。"""
                    nonlocal pos, nav, opp_streak
                    if pos == 0 or self.p.sar_tp_ticks <= 0 or bid <= 0 or ask <= 0:
                        return False
                    tsz = tw_tick_size(entry_price)
                    # 可實現成交價：做多以 Bid−滑價 賣出、做空以 Ask+滑價 買回
                    if pos == 1:
                        fill = self._slip(bid, -1, close)
                        prof = (fill - entry_price) / tsz
                    else:
                        fill = self._slip(ask, +1, close)
                        prof = (entry_price - fill) / tsz
                    if prof <= self.p.sar_tp_ticks:
                        return False
                    held = i - entry_i
                    ret = self._net_return(entry_price, fill,
                                           "long" if pos == 1 else "short")
                    nav *= (1 + ret / 100)
                    res.equity_curve.append(nav)
                    res.trades.append(TradeRecord(
                        direction="long" if pos == 1 else "short",
                        entry_time=entry_time, entry_price=round(entry_price, 4),
                        exit_time=t_str, exit_price=round(fill, 4),
                        ret_pct=round(ret, 4),
                        exit_reason=f"獲利了結(>{self.p.sar_tp_ticks}跳)",
                        hold_ticks=held))
                    res.sar_events.append({
                        "time": t_str, "side": "", "signal": "獲利了結", "strength": "",
                        "price": round(close, 2), "action": "獲利了結",
                        "detail": (f"無反向訊號，可實現獲利 {prof:.1f} 跳 > "
                                   f"{self.p.sar_tp_ticks} 跳，了結"
                                   f"{'多單' if pos == 1 else '空單'}"),
                        "traded": True, "ret_pct": round(ret, 4)})
                    pos = 0
                    opp_streak = 0
                    return True

                # 開盤前 N 分鐘不交易（引擎仍持續吃 tick 累積狀態，只是不進出場）
                in_open = (no_trade_until is not None and tk.get("ts") is not None
                           and int(tk["ts"]) < no_trade_until)
                if in_open:
                    if sig_side:
                        _ev("開盤前忽略",
                            f"開盤前 {self.p.sar_skip_open_minutes:g} 分鐘不交易", False)
                    continue
                if not sig_side:
                    _maybe_tp()  # 無訊號：仍檢查「浮動獲利 > N 跳」的獲利了結
                    continue

                sig = 1 if sig_side == "buy" else -1
                need = max(1, self.p.sar_exit_consecutive)

                def _enter(direction: int, label: str):
                    nonlocal pos, entry_price, entry_i, entry_time, peak
                    nonlocal opp_streak, last_entry_dir
                    nonlocal sar_long_entries, sar_short_entries
                    if direction == 1:
                        entry_price = self._slip(ask, +1, close)  # 吃 Ask + 滑價
                        pos = 1
                        sar_long_entries += 1
                    else:
                        entry_price = self._slip(bid, -1, close)  # 砍 Bid - 滑價
                        pos = -1
                        sar_short_entries += 1
                    last_entry_dir = pos
                    entry_i = i
                    entry_time = t_str
                    peak = close
                    opp_streak = 0
                    _ev("進場", label, True)

                if pos == 0:
                    # ---------------- 空手：只有「起漲/起跌點(強)」才啟動 ----------------
                    if not sig_is_attack:
                        # 動能點火但非起漲/起跌點 → 不啟動（僅記錄觀望）
                        _ev("不啟動", "動能點火但非起漲/起跌點，不啟動", False)
                        continue
                    allow_sig = (self.p.allow_long if sig == 1
                                 else self.p.allow_short)
                    if not allow_sig:
                        _ev("略過", "該方向未啟用，不進場", False)
                        continue
                    if bid <= 0 or ask <= 0:
                        _ev("略過", "無對手報價可成交", False)
                        continue
                    # 方向照起漲/起跌點：起漲點→做多、起跌點→沖賣(做空)
                    _enter(sig, f"{sig_name}啟動 {'做多' if sig == 1 else '沖賣(做空)'}")
                    continue

                # ---------------- 持倉中：不反手，累計反向點火達 N 筆才出場 ----------------
                if sig == pos:
                    # 同向點火＝支撐延續 → 重置反向計數；先看是否已達獲利了結
                    opp_streak = 0
                    if _maybe_tp():
                        continue
                    _ev("續抱", f"同向訊號，支撐延續，維持{'多單' if pos == 1 else '空單'}",
                        False)
                    continue
                # 反向點火：累計「支撐轉強(空單)／支撐轉弱(多單)」
                opp_streak += 1
                trend_txt = "支撐轉強" if pos == -1 else "支撐轉弱"
                if opp_streak < need:
                    # 未達出場門檻 → 若浮動獲利已 > N 跳先獲利了結，否則續抱觀察
                    if _maybe_tp():
                        continue
                    _ev("觀察", f"{trend_txt} {opp_streak}/{need}，未達門檻續抱", False)
                    continue
                # 達門檻 → 出場（做空補回 / 做多賣出），回到空手（不反手）
                if bid <= 0 or ask <= 0:
                    _ev("略過", "出場訊號成立但無對手報價", False)
                    continue
                held = i - entry_i
                if pos == 1:
                    fill = self._slip(bid, -1, close)
                    ret = self._net_return(entry_price, fill, "long")
                    act, reason = "賣出", f"賣出（連續{need}筆{trend_txt}）"
                else:
                    fill = self._slip(ask, +1, close)
                    ret = self._net_return(entry_price, fill, "short")
                    act, reason = "補回", f"補回（連續{need}筆{trend_txt}）"
                nav *= (1 + ret / 100)
                res.equity_curve.append(nav)
                res.trades.append(TradeRecord(
                    direction="long" if pos == 1 else "short",
                    entry_time=entry_time, entry_price=round(entry_price, 4),
                    exit_time=t_str, exit_price=round(fill, 4),
                    ret_pct=round(ret, 4), exit_reason=reason, hold_ticks=held))
                _ev(act, reason, True, ret)
                pos = 0
                opp_streak = 0
                continue

            # ===================== 出場判斷 =====================
            if pos != 0:
                held = i - entry_i
                exit_reason = ""
                # 目前浮動報酬（用 close 概估，實際成交用對手檔位）
                if pos == 1:
                    cur_ret = (close - entry_price) / entry_price * 100
                    peak = max(peak, close)
                    draw = (peak - close) / peak * 100 if peak > 0 else 0
                else:
                    cur_ret = (entry_price - close) / entry_price * 100
                    peak = min(peak, close) if peak > 0 else close
                    draw = (close - peak) / peak * 100 if peak > 0 else 0

                if held >= self.p.min_hold_ticks:
                    if cur_ret >= self.p.take_profit_pct:
                        exit_reason = "停利"
                    elif cur_ret <= -self.p.stop_loss_pct:
                        exit_reason = "停損"
                    elif self.p.trailing_pct > 0 and draw >= self.p.trailing_pct:
                        exit_reason = "移動停損"
                    elif self.p.strategy in ("attack_point", "attack_short"):
                        # 起漲/起跌點：遇反向強訊號出場（適合時機）——
                        # 空單見買方轉強補回、多單見賣方轉強獲利了結；否則續抱靠停利停損。
                        if pos == -1 and strong_buy_now:
                            exit_reason = "訊號轉強(補回)"
                        elif pos == 1 and strong_sell_now:
                            exit_reason = "訊號轉弱(賣出)"
                    elif exit_obi_flip and (
                            (pos == 1 and snap["setup_side"] == "sell") or
                            (pos == -1 and snap["setup_side"] == "buy")):
                        # 與進場對稱：OBI 蓄勢「翻向」（連續數筆 obi5 壓過反向門檻）才出場，
                        # 而非單純穿越 0，避免雜訊把停利提前洗掉。
                        exit_reason = "OBI反轉"
                    elif exit_reverse and (
                            (pos == 1 and last_attack_sell == i) or
                            (pos == -1 and last_attack_buy == i)):
                        exit_reason = "反向訊號"

                if exit_reason:
                    # 成交：多單以 Bid 賣出、空單以 Ask 買回，含滑價
                    if pos == 1:
                        fill = self._slip(bid if bid > 0 else close, -1, close)
                        ret = self._net_return(entry_price, fill, "long")
                    else:
                        fill = self._slip(ask if ask > 0 else close, +1, close)
                        ret = self._net_return(entry_price, fill, "short")
                    nav *= (1 + ret / 100)
                    res.equity_curve.append(nav)
                    res.trades.append(TradeRecord(
                        direction="long" if pos == 1 else "short",
                        entry_time=entry_time, entry_price=round(entry_price, 4),
                        exit_time=t_str, exit_price=round(fill, 4),
                        ret_pct=round(ret, 4), exit_reason=exit_reason,
                        hold_ticks=held))
                    pos = 0
                    continue  # 出場當筆不再進場

            # ===================== 進場判斷 =====================
            if pos == 0 and self.p.strategy in ("attack_point", "attack_short"):
                # 起漲/起跌點（順勢，照即時監控邏輯，不走合流計分）：
                #   做多 → 強起漲點(attack buy)買進；做空 → 強起跌點(attack sell)沖賣。
                if self.p.allow_long and attack_buy_now and bid > 0 and ask > 0:
                    entry_price = self._slip(ask, +1, close)  # 吃 Ask + 滑價
                    pos = 1
                    entry_i = i
                    entry_time = t_str
                    peak = close
                elif self.p.allow_short and attack_sell_now and bid > 0 and ask > 0:
                    entry_price = self._slip(bid, -1, close)  # 砍 Bid - 滑價
                    pos = -1
                    entry_i = i
                    entry_time = t_str
                    peak = close
                continue

            # ---------------- 進場判斷（K-of-N 合流計分）----------------
            if pos == 0:
                flat_ticks += 1

                # --- 三個核心訊號（各自有新鮮度窗口 W）---
                a_buy = snap["setup_active"] and snap["setup_side"] == "buy"
                b_buy = snap["consec_buy_buckets"] >= min_buckets
                c_buy = (i - last_attack_buy) <= W
                a_sell = snap["setup_active"] and snap["setup_side"] == "sell"
                b_sell = snap["consec_sell_buckets"] >= min_buckets
                c_sell = (i - last_attack_sell) <= W
                # --- 突破訊號（選配過濾條件，不計入 K 分數）---
                d_up = close > prev_high
                d_dn = close < prev_low

                score_long = a_buy + b_buy + c_buy
                score_short = a_sell + b_sell + c_sell
                score_hist_long[score_long] += 1
                score_hist_short[score_short] += 1
                for flag, key in ((a_buy, "obi_buy"), (b_buy, "vpin_buy"),
                                  (c_buy, "attack_buy"), (a_sell, "obi_sell"),
                                  (b_sell, "vpin_sell"), (c_sell, "attack_sell"),
                                  (d_up, "brk_up"), (d_dn, "brk_dn")):
                    if flag:
                        cond_hits[key] += 1

                # 訊號叢集是否成立（尚未決定做多/做空方向）
                buy_sig = (score_long >= K
                           and (not self.p.require_attack or c_buy)
                           and (not self.p.require_breakout or d_up))
                sell_sig = (score_short >= K
                            and (not self.p.require_attack or c_sell)
                            and (not self.p.require_breakout or d_dn))
                # 正常：買訊→多、賣訊→空；反向(fade)：買訊→空、賣訊→多
                want_long = sell_sig if inv else buy_sig
                want_short = buy_sig if inv else sell_sig
                pre_long = self.p.allow_long and want_long
                pre_short = self.p.allow_short and want_short

                # ===== 上層濾網閘門：實際部位方向須大局點頭才放行 =====
                gate_long = daily_long_ok and (
                    intraday_tf is None or intraday_tf.long_ok)
                gate_short = daily_short_ok and (
                    intraday_tf is None or intraday_tf.short_ok)
                if pre_long and not gate_long:
                    filter_blocked_long += 1
                if pre_short and not gate_short:
                    filter_blocked_short += 1
                long_ok = pre_long and gate_long
                short_ok = pre_short and gate_short

                if self.p.debug_every and i > 0 and i % self.p.debug_every == 0:
                    log.info(
                        "[bt %s] tick %d/%d｜avgOBI=%.3f maxVol=%.0f maxBuckets=%d｜"
                        "命中 OBI買%d VPIN買%d 大單買%d｜score≥%d 多%d 空%d",
                        code, i, len(ticks), obi_sum / (i + 1), max_volume, max_consec,
                        cond_hits["obi_buy"], cond_hits["vpin_buy"], cond_hits["attack_buy"],
                        K, score_hist_long[3] + (score_hist_long[2] if K <= 2 else 0),
                        score_hist_short[3] + (score_hist_short[2] if K <= 2 else 0))

                if long_ok and bid > 0 and ask > 0:
                    entry_price = self._slip(ask, +1, close)  # 吃 Ask + 滑價
                    pos = 1
                    entry_i = i
                    entry_time = t_str
                    peak = close
                elif short_ok and bid > 0 and ask > 0:
                    entry_price = self._slip(bid, -1, close)  # 砍 Bid - 滑價
                    pos = -1
                    entry_i = i
                    entry_time = t_str
                    peak = close

        # 收盤強制平倉
        if pos != 0:
            last = ticks[-1]
            close = last["close"]
            t_str = self._ts_str(last.get("ts"))
            if pos == 1:
                fill = self._slip(last["bid_price"] or close, -1, close)
                ret = self._net_return(entry_price, fill, "long")
            else:
                fill = self._slip(last["ask_price"] or close, +1, close)
                ret = self._net_return(entry_price, fill, "short")
            nav *= (1 + ret / 100)
            res.equity_curve.append(nav)
            res.trades.append(TradeRecord(
                direction="long" if pos == 1 else "short",
                entry_time=entry_time, entry_price=round(entry_price, 4),
                exit_time=t_str, exit_price=round(fill, 4),
                ret_pct=round(ret, 4), exit_reason="收盤平倉",
                hold_ticks=len(ticks) - 1 - entry_i))
            if self.p.strategy == "sar_flip":
                res.sar_events.append({
                    "time": t_str, "side": "", "signal": "收盤", "strength": "",
                    "price": round(close, 2), "action": "收盤平倉",
                    "detail": f"收盤強制平倉{'多單' if pos == 1 else '空單'}",
                    "traded": True, "ret_pct": round(ret, 4)})

        # 診斷彙整（供報告顯示零訊號卡點）
        ft = max(1, flat_ticks)
        res.diag = {
            "flat_ticks": flat_ticks,
            "avg_obi5": obi_sum / len(ticks) if ticks else 0.0,
            "max_volume": max_volume,
            "max_consec_buckets": max_consec,
            "K": K,
            "min_buckets": min_buckets,
            "invert": inv,
            "strategy": self.p.strategy,
            "atk_buy_signals": atk_buy_signals,
            "atk_sell_signals": atk_sell_signals,
            "strong_buy_signals": strong_buy_signals,
            "strong_sell_signals": strong_sell_signals,
            "ignite_buy_signals": ignite_buy_signals,
            "ignite_sell_signals": ignite_sell_signals,
            "sar_skip_open_minutes": self.p.sar_skip_open_minutes,
            "sar_exit_consecutive": self.p.sar_exit_consecutive,
            "sar_tp_ticks": self.p.sar_tp_ticks,
            "sar_bias": last_entry_dir,
            "sar_long_entries": sar_long_entries,
            "sar_short_entries": sar_short_entries,
            "cond_hits": cond_hits,
            "cond_pct": {k: v / ft * 100 for k, v in cond_hits.items()},
            "score_hist_long": score_hist_long,
            "score_hist_short": score_hist_short,
            # 濾網
            "daily_filter": self.p.daily_trend_filter,
            "daily_note": daily_note,
            "intraday_filter": self.p.intraday_filter,
            "intraday_state": intraday_tf.state if intraday_tf else "",
            "filter_blocked_long": filter_blocked_long,
            "filter_blocked_short": filter_blocked_short,
        }

        self._compute_metrics(res)
        return res

    # ---- 成交價：滑價（direction +1 買方向上、-1 賣方向下）----
    def _slip(self, price: float, direction: int, ref_price: float) -> float:
        ts = tw_tick_size(ref_price)
        return price + direction * self.p.slippage_ticks * ts

    # ---- 淨報酬率（含手續費/稅），以進場價為分母 ----
    def _net_return(self, entry: float, exit_: float, direction: str) -> float:
        fee = self.p.fee_rate * self.p.fee_discount
        tax = self.p.tax_rate
        if entry <= 0:
            return 0.0
        if direction == "long":
            # 買進付 fee，賣出付 fee+tax
            cost = entry * fee + exit_ * (fee + tax)
            pnl = (exit_ - entry) - cost
        else:
            # 放空：賣出付 fee+tax，買回付 fee
            cost = entry * (fee + tax) + exit_ * fee
            pnl = (entry - exit_) - cost
        return pnl / entry * 100

    @staticmethod
    def _ts_str(ts) -> str:
        """永豐歷史 ticks 的 ts 為奈秒 epoch，數值本身即「台北當地時間」（把當地時間
        當成 UTC 編碼）。因此固定用 UTC 解讀、不套用本機時區，否則在 UTC+8 的機器上
        會再多加 8 小時。與 pandas.to_datetime(ticks.ts) 得到的時間一致。"""
        try:
            return (datetime.fromtimestamp(int(ts) / 1e9, tz=timezone.utc)
                    .strftime("%H:%M:%S"))
        except (TypeError, ValueError, OSError, OverflowError):
            return ""

    # ---- 績效統計 ----
    def _compute_metrics(self, res: BacktestResult) -> None:
        trades = res.trades
        res.total_trades = len(trades)
        if not trades:
            return
        rets = [t.ret_pct for t in trades]
        wins = [r for r in rets if r > 0]
        losses = [r for r in rets if r <= 0]
        res.wins = len(wins)
        res.losses = len(losses)
        res.win_rate = len(wins) / len(trades) * 100
        res.long_trades = sum(1 for t in trades if t.direction == "long")
        res.short_trades = sum(1 for t in trades if t.direction == "short")

        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        res.profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
        res.avg_win_pct = (gross_profit / len(wins)) if wins else 0.0
        res.avg_loss_pct = (-gross_loss / len(losses)) if losses else 0.0
        res.expectancy_pct = sum(rets) / len(rets)

        # 以 NAV 複利序列計算總報酬與最大回撤
        nav = res.equity_curve
        res.total_return_pct = (nav[-1] - 1.0) * 100 if nav else 0.0
        peak = nav[0] if nav else 1.0
        mdd = 0.0
        for v in nav:
            peak = max(peak, v)
            dd = (peak - v) / peak * 100 if peak > 0 else 0
            mdd = max(mdd, dd)
        res.max_drawdown_pct = mdd

    # ---- 診斷文字（哪個條件太嚴格）----
    def _diag_text(self, res: BacktestResult) -> str:
        d = res.diag
        if not d:
            return ""
        # 反手輪詢(SAR)策略：專屬診斷
        if d.get("strategy") == "sar_flip":
            bias = d.get("sar_bias", 0)
            bias_txt = ("做多" if bias == 1 else "做空" if bias == -1 else "未觸發")
            lines = [
                "—— 點火進出(不反手)診斷 ——",
                f"開盤前 {d.get('sar_skip_open_minutes', 0):g} 分鐘不交易；"
                f"進場(啟動)＝起漲點→做多 / 起跌點→做空（僅起漲/起跌點，強）",
                f"出場＝連續 {d.get('sar_exit_consecutive', 0)} 筆反向支撐轉強/轉弱"
                f"（點火或起漲/起跌皆算）；或可實現獲利 > {d.get('sar_tp_ticks', 0)} 跳了結",
                f"不反手；出場後回空手，等下一個起漲/起跌點再啟動（反向起漲/起跌點直接換向）",
                f"最後進場方向：{bias_txt}",
                f"起跌點：{d.get('atk_sell_signals', 0)} 次（做空啟動）｜"
                f"賣方點火：{d.get('ignite_sell_signals', 0)} 次（僅供出場計數）",
                f"起漲點：{d.get('atk_buy_signals', 0)} 次（做多啟動）｜"
                f"買方點火：{d.get('ignite_buy_signals', 0)} 次（僅供出場計數）",
                f"實際進場：做多 {d.get('sar_long_entries', 0)} 次 / "
                f"做空 {d.get('sar_short_entries', 0)} 次",
            ]
            if res.total_trades == 0:
                lines.append("→ 未觸發交易：該日開盤 10 分鐘後無『起漲/起跌點(attack)』訊號，"
                             "或大單倍數/最低張門檻過高。可調低門檻或用「依股價自動」放寬。")
            return "\n".join(lines)
        # 起漲/起跌點策略：專屬診斷
        if d.get("strategy") in ("attack_point", "attack_short"):
            lines = [
                "—— 起漲/起跌點診斷 ——",
                f"強起漲點(attack buy)：{d.get('atk_buy_signals', 0)} 次（做多進場機會）",
                f"強起跌點(attack sell)：{d.get('atk_sell_signals', 0)} 次（做空進場機會）",
            ]
            if res.total_trades == 0:
                lines.append("→ 未觸發交易：該日無對應方向的起漲/起跌點，或大單門檻過高。"
                             "確認已勾做多/做空，並可調低『大單倍數/最低張』或用「依股價自動」放寬。")
            return "\n".join(lines)
        cp = d.get("cond_pct", {})
        sl = d.get("score_hist_long", {})
        ss = d.get("score_hist_short", {})
        ft = d.get("flat_ticks", 0)
        lines = [
            f"—— 訊號診斷（空手 {ft:,} 筆 tick）——",
            f"平均 OBI5：{d.get('avg_obi5', 0):+.3f}　最大單筆量：{d.get('max_volume', 0):,.0f} 張"
            f"　最大連續桶：{d.get('max_consec_buckets', 0)}",
            f"各條件命中率：OBI買 {cp.get('obi_buy', 0):.1f}%｜VPIN買 {cp.get('vpin_buy', 0):.1f}%"
            f"｜大單買 {cp.get('attack_buy', 0):.1f}%｜突破 {cp.get('brk_up', 0):.1f}%",
            f"　　　　　　OBI賣 {cp.get('obi_sell', 0):.1f}%｜VPIN賣 {cp.get('vpin_sell', 0):.1f}%"
            f"｜大單賣 {cp.get('attack_sell', 0):.1f}%",
            f"合流分數分布(多)：0={sl.get(0,0)} 1={sl.get(1,0)} 2={sl.get(2,0)} 3={sl.get(3,0)}"
            f"　需 ≥{d.get('K')} 進場",
            f"合流分數分布(空)：0={ss.get(0,0)} 1={ss.get(1,0)} 2={ss.get(2,0)} 3={ss.get(3,0)}",
        ]
        # 濾網狀態（有開才顯示）
        if d.get("daily_filter"):
            lines.append(f"日線濾網：{d.get('daily_note', '')}")
        if d.get("intraday_filter") in ("ma", "squeeze"):
            lines.append(
                f"分線濾網({d.get('intraday_filter')})：{d.get('intraday_state', '')}")
        if d.get("filter_blocked_long") or d.get("filter_blocked_short"):
            lines.append(
                f"※ 微觀訊號成立但被濾網擋下：多 {d.get('filter_blocked_long', 0)} 次 / "
                f"空 {d.get('filter_blocked_short', 0)} 次")
        lines.append(self._diag_hint(res))
        return "\n".join(lines)

    @staticmethod
    def _diag_hint(res: BacktestResult) -> str:
        """依命中率給一句「哪裡太嚴」的建議。"""
        d = res.diag
        cp = d.get("cond_pct", {})
        weakest = min(
            [("OBI 蓄勢", cp.get("obi_buy", 0) + cp.get("obi_sell", 0)),
             ("VPIN 連續桶", cp.get("vpin_buy", 0) + cp.get("vpin_sell", 0)),
             ("大單起漲", cp.get("attack_buy", 0) + cp.get("attack_sell", 0))],
            key=lambda x: x[1])
        blocked = d.get("filter_blocked_long", 0) + d.get("filter_blocked_short", 0)
        if res.total_trades == 0:
            if blocked > 0:
                return (f"→ 微觀訊號其實成立過 {blocked} 次，但全被上層濾網擋下："
                        f"方向與大局相反或未達突破。可放寬/關閉濾網，或改抓另一方向。")
            return (f"→ 建議：最少見的是「{weakest[0]}」；可調降對應門檻、"
                    f"降低『進場門檻數K』或『連續桶數』，或用「依股價自動」放寬。")
        return f"→ 三訊號中最稀有：「{weakest[0]}」。"

    # ---- 文字報告 ----
    def report_text(self, res: BacktestResult) -> str:
        if res.error:
            return f"回測失敗：{res.error}"
        if res.total_trades == 0:
            return (f"{res.code} {res.date}｜{res.tick_count:,} 筆 tick\n"
                    f"未觸發任何交易。\n" + self._diag_text(res))
        pf = "∞" if res.profit_factor == float("inf") else f"{res.profit_factor:.2f}"
        if res.diag.get("strategy") == "sar_flip":
            mode = "點火進出(不反手)"
        elif res.diag.get("strategy") in ("attack_point", "attack_short"):
            mode = "起漲/起跌點"
        else:
            mode = "反向(fade)" if res.diag.get("invert") else "順勢"
        lines = [
            f"===== 微觀結構回測報告（{mode}）=====",
            f"標的：{res.code}　日期：{res.date}　Tick 數：{res.tick_count:,}",
            f"總交易：{res.total_trades}（多 {res.long_trades} / 空 {res.short_trades}）",
            f"勝率：{res.win_rate:.1f}%（{res.wins} 勝 / {res.losses} 敗）",
            f"總報酬：{res.total_return_pct:+.2f}%",
            f"獲利因子：{pf}",
            f"最大回撤：{res.max_drawdown_pct:.2f}%",
            f"平均獲利：{res.avg_win_pct:+.3f}%　平均虧損：{res.avg_loss_pct:+.3f}%",
            f"每筆期望值：{res.expectancy_pct:+.3f}%",
            f"============================",
            self._diag_text(res),
        ]
        return "\n".join(lines)
