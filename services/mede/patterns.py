"""Pattern Library — 可設定/停用的規則型態（Phase 5，配合 Weighted Score）。

每個 pattern 是偵測器結果 + snapshot 的規則檢查，回傳 (name, direction)。
可用 cfg.pattern_enabled[name]=False 停用；後續可單獨回測。
"""

from __future__ import annotations


def _trig(results: dict, name: str, direction=None) -> bool:
    r = results.get(name)
    if not r or not r.is_triggered:
        return False
    return direction is None or r.direction == direction


def match_patterns(results: dict, snap, cfg) -> list:
    en = cfg.pattern_enabled or {}

    def on(p):
        return en.get(p, True)

    up_flow = _trig(results, "aggressive_flow", 1)
    dn_flow = _trig(results, "aggressive_flow", -1)
    burst_up = _trig(results, "trade_burst", 1) or _trig(results, "volume_burst", 1)
    burst_dn = _trig(results, "trade_burst", -1) or _trig(results, "volume_burst", -1)
    ofi_up = _trig(results, "ofi_shock", 1) or _trig(results, "book_imbalance_shift", 1)
    ofi_dn = _trig(results, "ofi_shock", -1) or _trig(results, "book_imbalance_shift", -1)
    vel = snap.price_velocity
    absb = results.get("absorption")
    sell_abs = bool(absb and absb.is_triggered and absb.direction < 0)
    buy_abs = bool(absb and absb.is_triggered and absb.direction > 0)

    out = []
    if on("A_bull_momentum_start") and burst_up and up_flow and ofi_up and vel > 0 and not sell_abs:
        out.append(("A_bull_momentum_start", 1))
    if on("B_bear_momentum_start") and burst_dn and dn_flow and ofi_dn and vel < 0 and not buy_abs:
        out.append(("B_bear_momentum_start", -1))
    if on("C_bull_liquidity_vacuum") and _trig(results, "liquidity_vacuum", 1) and vel >= 0 and up_flow:
        out.append(("C_bull_liquidity_vacuum", 1))
    if on("D_bear_liquidity_vacuum") and _trig(results, "liquidity_vacuum", -1) and vel <= 0 and dn_flow:
        out.append(("D_bear_liquidity_vacuum", -1))
    if on("E_bullish_absorption_reversal") and _trig(results, "absorption", 1):
        out.append(("E_bullish_absorption_reversal", 1))
    if on("F_bearish_absorption_reversal") and _trig(results, "absorption", -1):
        out.append(("F_bearish_absorption_reversal", -1))
    if on("G_failed_bull_breakout") and _trig(results, "failed_breakout", -1):
        out.append(("G_failed_bull_breakout", -1))
    if on("H_failed_bear_breakout") and _trig(results, "failed_breakout", 1):
        out.append(("H_failed_bear_breakout", 1))
    return out
