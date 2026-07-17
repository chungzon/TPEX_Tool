"""BedBacktester（Phase 6）— 事件驅動空方回測。

用與即時**完全相同**的 MedeEngine 重播歷史逐筆（依 seq，決定性）產生事件，
再依事件模擬進出場並統計績效。成本/滑價/淨報酬/績效機制沿用
`microstructure_backtest` 的既有公式（不重造回測數學）。

防未來函數：進場在事件成立後 entry_delay 才成交、以當下對手檔+滑價成交、
出場逐筆前進判斷，不偷看下一筆。
資料降級：無五檔（歷史 L1）→ data_mode='l1'，報告標示、不與 full 混比。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

from services.mede.engine import MedeEngine
from services.mede.replay import TickReplayEngine
from services.mede.outcome import OutcomeEngine
from services.mede.tick_size import tw_tick_size
from services.microstructure_backtest import tw_tick_size as _mb_tick  # noqa: F401  (同源，確認一致)


@dataclass
class BedBacktestParams:
    direction: int = -1              # 只回測空方；設 0=多空皆測、+1=只多方
    min_final_score: float = 75.0    # 只交易 final_score ≥ 此的事件
    patterns: list = field(default_factory=list)   # 空=全部；否則只取命中這些型態
    entry_delay_ms: int = 300        # 訊號確認/下單延遲
    take_profit_ticks: float = 6.0
    stop_loss_ticks: float = 4.0
    max_holding_ms: int = 60_000
    time_stop_ms: int = 15_000       # 時間停損：此時間內無有利進展即出場
    min_progress_ticks: float = 1.0  # time_stop 的「有利進展」門檻
    slippage_ticks: float = 1.0
    fee_rate: float = 0.001425
    fee_discount: float = 0.3
    tax_rate: float = 0.0015         # 當沖賣出證交稅
    cooldown_ms: int = 5_000         # 兩筆交易間冷卻
    max_trades_per_day: int = 50


@dataclass
class BedTrade:
    code: str
    date: str
    pattern: str
    direction: int
    trigger_price: float
    entry_price: float
    exit_price: float
    bear_score: float
    final_score: float
    mfe_pct: float
    mae_pct: float
    net_pnl_pct: float
    holding_ms: int
    outcome: str
    exit_reason: str
    entry_time_ns: int
    data_mode: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class BedBacktestResult:
    code: str = ""
    date: str = ""
    tick_count: int = 0
    data_mode: str = "full"          # full | l1
    event_count: int = 0
    tradable: int = 0
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)
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
    avg_mfe_pct: float = 0.0
    avg_mae_pct: float = 0.0
    avg_holding_ms: float = 0.0
    max_consec_losses: int = 0
    error: str = ""

    def summary_dict(self) -> dict:
        d = asdict(self)
        d.pop("trades", None)
        d.pop("equity_curve", None)
        return d


class BedBacktester:
    def __init__(self, cfg, params: BedBacktestParams | None = None):
        self.cfg = cfg
        self.p = params or BedBacktestParams()
        self._oc = OutcomeEngine(cfg)

    # ---- 重播：產生 (價格/書 series, 事件, data_mode) ----
    def _replay(self, storage, code: str, date: str):
        rep = TickReplayEngine(storage, tw_tick_size)
        evs, has_ba = rep.load_events(code, date)
        eng = MedeEngine(code, self.cfg, tw_tick_size)
        series = []   # (t_ns, price, bid, ask)
        events = []
        for seq, kind, data in evs:
            if kind == "tick":
                snap, fusion, state, ev = eng.on_tick({
                    "code": data.get("code", ""), "time": data.get("exchange_time", ""),
                    "close": data.get("close"), "volume": data.get("volume"),
                    "tick_type": data.get("tick_type", 0), "seq": data.get("seq", 0)})
                series.append((snap.t_ns, snap.last_price, snap.best_bid, snap.best_ask))
                if ev is not None:
                    events.append(ev)
            else:
                eng.on_bidask({
                    "code": data.get("code", ""), "time": data.get("exchange_time", ""),
                    "bid_price": data.get("bid_price", []),
                    "bid_volume": data.get("bid_volume", []),
                    "ask_price": data.get("ask_price", []),
                    "ask_volume": data.get("ask_volume", []),
                    "seq": data.get("seq", 0)})
        return series, events, ("full" if has_ba else "l1")

    def run(self, storage, code: str, date: str) -> BedBacktestResult:
        series, events, data_mode = self._replay(storage, code, date)
        res = BedBacktestResult(code=code, date=date, tick_count=len(series),
                                data_mode=data_mode, event_count=len(events))
        if not series:
            res.error = "無逐筆資料"
            return res
        price_path = [(t, p) for (t, p, _b, _a) in series]
        p = self.p
        nav = 1.0
        res.equity_curve.append(nav)
        last_exit_t = -1 << 62
        count = 0
        for ev in events:
            if p.direction and ev.direction != p.direction:
                continue
            if ev.final_score < p.min_final_score:
                continue
            if p.patterns and not (set(ev.matched_patterns) & set(p.patterns)):
                continue
            if count >= p.max_trades_per_day:
                break
            if ev.event_time_ns < last_exit_t + p.cooldown_ms * 1_000_000:
                continue
            trade = self._simulate(ev, series, price_path, data_mode, code, date)
            if trade is None:
                continue
            nav *= (1 + trade.net_pnl_pct / 100.0)
            res.equity_curve.append(nav)
            res.trades.append(trade)
            last_exit_t = trade.entry_time_ns + trade.holding_ms * 1_000_000
            count += 1
        res.tradable = count
        self._metrics(res)
        return res

    def _simulate(self, ev, series, price_path, data_mode, code, date):
        p = self.p
        entry_t = ev.event_time_ns + p.entry_delay_ms * 1_000_000
        # 進場點：事件延遲後第一筆
        idx = next((i for i, (t, _pr, _b, _a) in enumerate(series)
                    if t >= entry_t), None)
        if idx is None:
            return None
        t_e, price_e, bid_e, ask_e = series[idx]
        ts = tw_tick_size(price_e)
        if ts <= 0:
            return None
        d = ev.direction
        # 進場成交：空方砍 Bid、多方吃 Ask（無五檔則用成交價），加滑價
        if d < 0:
            entry_fill = (bid_e if bid_e > 0 else price_e) - p.slippage_ticks * ts
        else:
            entry_fill = (ask_e if ask_e > 0 else price_e) + p.slippage_ticks * ts

        exit_reason = "收盤平倉"
        exit_fill = None
        exit_t = series[-1][0]
        for j in range(idx + 1, len(series)):
            t, pr, bid, ask = series[j]
            held_ms = (t - t_e) // 1_000_000
            # 順方向進展（tick）：空方=進場價高於現價、多方=現價高於進場價
            favorable = ((entry_fill - pr) / ts) if d < 0 else ((pr - entry_fill) / ts)
            if favorable >= p.take_profit_ticks:
                exit_reason = "停利"
            elif favorable <= -p.stop_loss_ticks:
                exit_reason = "停損"
            elif held_ms >= p.max_holding_ms:
                exit_reason = "最大持有"
            elif held_ms >= p.time_stop_ms and favorable < p.min_progress_ticks:
                exit_reason = "時間停損"
            else:
                continue
            # 出場成交：空方補 Ask、多方賣 Bid，加滑價
            if d < 0:
                exit_fill = (ask if ask > 0 else pr) + p.slippage_ticks * ts
            else:
                exit_fill = (bid if bid > 0 else pr) - p.slippage_ticks * ts
            exit_t = t
            break
        if exit_fill is None:                       # 收盤前未出場
            last_pr = series[-1][1]
            exit_fill = last_pr
        net = self._net_return(entry_fill, exit_fill, d)
        oc = self._oc.evaluate(ev.event_id, d, ev.trigger_price, ev.event_time_ns,
                               ts, price_path)
        return BedTrade(
            code=code, date=date,
            pattern=(",".join(ev.matched_patterns) or ev.event_type),
            direction=d, trigger_price=round(ev.trigger_price, 4),
            entry_price=round(entry_fill, 4), exit_price=round(exit_fill, 4),
            bear_score=ev.score, final_score=ev.final_score,
            mfe_pct=oc.mfe_pct, mae_pct=oc.mae_pct, net_pnl_pct=round(net, 4),
            holding_ms=int((exit_t - t_e) // 1_000_000),
            outcome=oc.outcome, exit_reason=exit_reason,
            entry_time_ns=t_e, data_mode=data_mode)

    # ---- 淨報酬（含手續費/稅），沿用 microstructure_backtest 公式 ----
    def _net_return(self, entry: float, exit_: float, direction: int) -> float:
        fee = self.p.fee_rate * self.p.fee_discount
        tax = self.p.tax_rate
        if entry <= 0:
            return 0.0
        if direction > 0:                            # 做多：買付 fee、賣付 fee+tax
            cost = entry * fee + exit_ * (fee + tax)
            pnl = (exit_ - entry) - cost
        else:                                        # 做空：賣付 fee+tax、買回付 fee
            cost = entry * (fee + tax) + exit_ * fee
            pnl = (entry - exit_) - cost
        return pnl / entry * 100.0

    def _metrics(self, res: BedBacktestResult) -> None:
        trades = res.trades
        res.total_trades = len(trades)
        if not trades:
            return
        rets = [t.net_pnl_pct for t in trades]
        wins = [r for r in rets if r > 0]
        losses = [r for r in rets if r <= 0]
        res.wins, res.losses = len(wins), len(losses)
        res.win_rate = len(wins) / len(trades) * 100
        gp, gl = sum(wins), abs(sum(losses))
        res.profit_factor = (gp / gl) if gl > 0 else float("inf")
        res.avg_win_pct = (gp / len(wins)) if wins else 0.0
        res.avg_loss_pct = (-gl / len(losses)) if losses else 0.0
        res.expectancy_pct = sum(rets) / len(rets)
        res.avg_mfe_pct = sum(t.mfe_pct for t in trades) / len(trades)
        res.avg_mae_pct = sum(t.mae_pct for t in trades) / len(trades)
        res.avg_holding_ms = sum(t.holding_ms for t in trades) / len(trades)
        nav = res.equity_curve
        res.total_return_pct = (nav[-1] - 1.0) * 100 if nav else 0.0
        peak, mdd = nav[0], 0.0
        for v in nav:
            peak = max(peak, v)
            mdd = max(mdd, (peak - v) / peak * 100 if peak > 0 else 0)
        res.max_drawdown_pct = mdd
        cons = mx = 0
        for r in rets:
            cons = cons + 1 if r <= 0 else 0
            mx = max(mx, cons)
        res.max_consec_losses = mx
