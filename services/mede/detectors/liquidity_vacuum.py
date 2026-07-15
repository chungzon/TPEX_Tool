"""LiquidityVacuumDetector — 上/下方流動性突然消失，價格易跨 Tick 移動。

點差擴大(相對基準) 或 檔位間距變大，且某一側明顯較薄 → 該側易被穿越。
賣方薄 → 偏多(+1)；買方薄 → 偏空(-1)。需 BidAsk。
"""

from __future__ import annotations

from services.mede.detectors.base import Detector, DetectorResult, RollingZ, clamp


class LiquidityVacuumDetector(Detector):
    name = "liquidity_vacuum"
    requires_bidask = True

    def __init__(self, cfg):
        super().__init__(cfg)
        self._spread = RollingZ(cfg.baseline_window)
        self._last_ba = -1
        self._last = DetectorResult.idle(self.name, self.pv)

    def update(self, snap) -> DetectorResult:
        if not snap.bidask_available:
            return self._idle(reason="tick-only")
        if snap.bidask_updates == self._last_ba:
            return self._last
        self._last_ba = snap.bidask_updates

        spread = snap.spread_ticks
        mean, _ = self._spread.stats()
        warm = self._spread.ready(self.cfg.minimum_warmup_ticks)
        expanded = warm and mean > 0 and spread >= mean * self.cfg.spread_expansion_ratio
        self._spread.push(spread)

        wb, wa = snap.weighted_bid_depth, snap.weighted_ask_depth
        thin_ask = wa < wb * 0.5 and snap.ask_liquidity_gap >= 2
        thin_bid = wb < wa * 0.5 and snap.bid_liquidity_gap >= 2
        direction = 1 if thin_ask else (-1 if thin_bid else 0)
        big_gap = max(snap.bid_liquidity_gap, snap.ask_liquidity_gap) >= 2
        triggered = bool(direction != 0 and (expanded or big_gap))
        gap = snap.ask_liquidity_gap if direction > 0 else snap.bid_liquidity_gap
        score = clamp((1.5 if expanded else 0.8) * 30 + min(gap, 5) * 8) if triggered else 0.0
        conf = clamp((0.4 if expanded else 0.2) + min(gap / 3.0, 1.0) * 0.4
                     + 0.2, 0.0, 1.0) if triggered else 0.0
        reasons = ([f"{'上方' if direction > 0 else '下方'}流動性真空"
                    f"（點差{spread:.0f}跳{'(擴大)' if expanded else ''}, 缺口{gap:.0f}跳）"]
                   if triggered else [])
        self._last = DetectorResult(
            self.name, direction, score, conf, triggered, reasons,
            {"spread_ticks": spread, "spread_base": round(mean, 2),
             "bid_gap": snap.bid_liquidity_gap, "ask_gap": snap.ask_liquidity_gap,
             "expanded": expanded}, self.pv)
        return self._last
