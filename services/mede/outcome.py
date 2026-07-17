"""OutcomeEngine（Phase 6）— 事件事後結果標記（不參與訊號，純 post-hoc 標籤）。

對每個已觸發事件，沿之後的價格路徑計算：
  - 各 horizon（1/3/5/10/15/30/60 秒）「順事件方向」的報酬
  - MFE / MAE（最大有利 / 不利偏移，%）及達成時間
  - first-touch（先碰到有利或不利門檻）
  - 結果分類：WIN / LOSS / NEUTRAL / AMBIGUOUS / INVALID

「順事件方向」報酬 signed_ret = direction × (price − trigger) / trigger：
空方(−1)價跌為正、多方(+1)價漲為正。以事件驅動掃描決定 WIN/LOSS 的先後順序，
不偷看未來以外的資訊（本身即事後標記，允許用事件之後的價格）。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

HORIZONS_S = [1, 3, 5, 10, 15, 30, 60]


@dataclass
class Outcome:
    event_id: str
    direction: int
    forward_returns: dict = field(default_factory=dict)   # {"1s":.., "3s":..}
    mfe_pct: float = 0.0
    mae_pct: float = 0.0
    t_to_mfe_ms: int = 0
    t_to_mae_ms: int = 0
    first_touch: str = "none"        # favorable | adverse | none
    first_touch_ms: int = 0
    continued: bool = False          # 觸發後續往有利方向延伸（達 target）
    failed: bool = False             # 反向達 stop
    outcome: str = "INVALID"         # WIN | LOSS | NEUTRAL | AMBIGUOUS | INVALID

    def as_dict(self) -> dict:
        return asdict(self)


class OutcomeEngine:
    def __init__(self, cfg):
        self.cfg = cfg

    def evaluate(self, event_id: str, direction: int, trigger_price: float,
                 trigger_t_ns: int, tick_size: float,
                 series: list) -> Outcome:
        """series: [(t_ns, price)]（全日或事件後皆可，內部只取 t>=trigger）。"""
        if direction == 0 or trigger_price <= 0 or tick_size <= 0:
            return Outcome(event_id, direction)
        fwd = [(t, p) for (t, p) in series if t >= trigger_t_ns and p > 0]
        if len(fwd) < 2:
            return Outcome(event_id, direction)

        def sret(p):                 # 順方向報酬 %
            return direction * (p - trigger_price) / trigger_price * 100.0

        def sret_ticks(p):
            return direction * (p - trigger_price) / tick_size

        t0 = trigger_t_ns
        # forward returns：各 horizon 取「第一筆 t>=cutoff」的價；不足則標 None
        rets = {}
        for h in HORIZONS_S:
            cutoff = t0 + h * 1_000_000_000
            price_at = next((p for (t, p) in fwd if t >= cutoff), None)
            rets[f"{h}s"] = round(sret(price_at), 4) if price_at is not None else None

        target = self.cfg.outcome_target_ticks
        stop = self.cfg.outcome_stop_ticks
        ftk = self.cfg.outcome_first_touch_ticks
        horizon_ns = HORIZONS_S[-1] * 1_000_000_000

        mfe, mae = -1e18, 1e18
        t_mfe = t_mae = 0
        first_touch, first_touch_ms = "none", 0
        outcome, decided = "NEUTRAL", False
        for (t, p) in fwd:
            if t - t0 > horizon_ns:
                break
            s = sret(p)
            stk = sret_ticks(p)
            ms = (t - t0) // 1_000_000
            if s > mfe:
                mfe, t_mfe = s, ms
            if s < mae:
                mae, t_mae = s, ms
            if first_touch == "none" and abs(stk) >= ftk:
                first_touch = "favorable" if stk > 0 else "adverse"
                first_touch_ms = ms
            if not decided:
                if stk >= target:
                    outcome, decided = "WIN", True
                elif stk <= -stop:
                    outcome, decided = "LOSS", True
        if mfe < -1e17:
            return Outcome(event_id, direction)
        return Outcome(
            event_id=event_id, direction=direction, forward_returns=rets,
            mfe_pct=round(mfe, 4), mae_pct=round(mae, 4),
            t_to_mfe_ms=int(t_mfe), t_to_mae_ms=int(t_mae),
            first_touch=first_touch, first_touch_ms=int(first_touch_ms),
            continued=(outcome == "WIN"), failed=(outcome == "LOSS"),
            outcome=outcome)
