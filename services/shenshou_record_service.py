"""即時神手事件錄製 / 回放 — 把監控中的逐筆軌跡與事件存成 JSON（每檔一檔），
供同頁下方「靜態回放」江波圖（價/量走勢 + 事件標記）。

一個監控 session 對應一個 SessionRecorder，內含多檔 StockTape。tick callback
在 Shioaji socket 執行緒觸發，append 需輕量；本模組自帶鎖，save 與 on_tick 併發安全。

事件（皆存進 JSON，圖上以標記呈現）：
- 買盤竭盡(buy_exhaust,+1) / 賣盤竭盡(sell_exhaust,-1)：沿用 tick_streak 的 exhaust。
- 連買達門檻(buy_streak,+1) / 連賣達門檻(sell_streak,-1)：連續同向達 milestone 筆數（每段一次）。

純資料 + JSON I/O，無 Shioaji 相依。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime

log = logging.getLogger(__name__)

RECORDS_DIR = "shenshou_data"      # repo 根目錄下，每檔每日一個 JSON
_STREAK_MILESTONE = 5              # 連買/連賣達此連續筆數 → 記里程碑事件（圖上三角）

# 事件類型
EV_BUY_EXHAUST = "buy_exhaust"     # 買盤竭盡（做頭）+1
EV_SELL_EXHAUST = "sell_exhaust"   # 賣盤竭盡（打底）-1
EV_BUY_STREAK = "buy_streak"       # 連買達門檻 +1
EV_SELL_STREAK = "sell_streak"     # 連賣達門檻 -1


def _hhmmss(t) -> str:
    """把 tick 時間（'2026-08-27 09:00:05.123456' 或 datetime）取 HH:MM:SS。"""
    s = str(t or "")
    if " " in s:
        s = s.split(" ", 1)[1]
    return s[:8]


@dataclass
class StockTape:
    """單檔的錄製帶：逐筆樣本 + 衍生事件。"""
    code: str
    name: str = ""
    prev_close: float = 0.0
    open_price: float = 0.0
    samples: list = field(default_factory=list)   # [{t, p(價), v(量張), s(方向)}]
    events: list = field(default_factory=list)     # [{t, i(sample索引), type, dir, price, count, vol}]
    _milestone_dir: int = 0        # 本段已記里程碑的方向（避免同段重覆）

    def to_dict(self) -> dict:
        return {"prev_close": self.prev_close, "open": self.open_price,
                "samples": self.samples, "events": self.events}


class SessionRecorder:
    """一次監控 session 的錄製器（多檔）。"""

    def __init__(self, date: str = "", big_lots: int = 100,
                 milestone: int = _STREAK_MILESTONE):
        self.date = date or datetime.now().strftime("%Y-%m-%d")
        self.big_lots = big_lots
        self.milestone = milestone
        self.tapes: dict[str, StockTape] = {}
        self.started_at = datetime.now().strftime("%H:%M:%S")
        self._lock = threading.Lock()

    def register(self, code: str, name: str = "", prev_close: float = 0.0,
                 open_price: float = 0.0) -> None:
        with self._lock:
            self.tapes[code] = StockTape(code=code, name=name or code,
                                         prev_close=prev_close,
                                         open_price=open_price)

    def on_tick(self, code: str, tick: dict, st) -> None:
        """記一筆 sample，並依更新後的 StreakState (st) 衍生事件。

        僅在有成交量的 tick 記錄；呼叫端（VM）持鎖與否皆安全（本身另有鎖）。
        """
        vol = int(tick.get("volume") or 0)
        if vol <= 0:
            return
        t = _hhmmss(tick.get("time"))
        price = float(tick.get("close") or tick.get("price") or st.last_price or 0)
        side = st.last_side
        with self._lock:
            tape = self.tapes.get(code)
            if tape is None:
                return
            tape.samples.append({"t": t, "p": price, "v": vol, "s": side})
            i = len(tape.samples) - 1
            # 竭盡（反轉那筆）：st.exhaust +1買盤竭盡 / -1賣盤竭盡
            if st.exhaust > 0:
                tape.events.append({
                    "t": t, "i": i, "type": EV_BUY_EXHAUST, "dir": 1,
                    "price": price, "count": st.exhaust_count,
                    "vol": st.exhaust_vol})
            elif st.exhaust < 0:
                tape.events.append({
                    "t": t, "i": i, "type": EV_SELL_EXHAUST, "dir": -1,
                    "price": price, "count": st.exhaust_count,
                    "vol": st.exhaust_vol})
            # 連買/連賣達門檻里程碑（每段僅記一次）
            if st.streak_count <= 1:
                tape._milestone_dir = 0      # 新段開始 → 重置里程碑旗標
            if (st.streak_dir != 0 and st.streak_count >= self.milestone
                    and tape._milestone_dir != st.streak_dir):
                tape._milestone_dir = st.streak_dir
                et = EV_BUY_STREAK if st.streak_dir > 0 else EV_SELL_STREAK
                tape.events.append({
                    "t": t, "i": i, "type": et, "dir": st.streak_dir,
                    "price": price, "count": st.streak_count,
                    "vol": st.streak_vol})

    def snapshot_all(self) -> list[dict]:
        """回傳各檔目前的即時快照（供監控中即時繪江波圖），依註冊順序。

        samples/events 以淺拷貝回傳（僅 append、不改既有元素，讀取安全且快）。
        """
        with self._lock:
            return [{
                "code": tape.code, "name": tape.name,
                "prev_close": tape.prev_close, "open": tape.open_price,
                "samples": tape.samples[:], "events": tape.events[:],
                "date": self.date, "started_at": self.started_at,
            } for tape in self.tapes.values()]

    def save(self, base_dir: str = RECORDS_DIR) -> list[str]:
        """把每檔（有樣本者）寫成 shenshou_{code}_{YYYYMMDD}.json；回寫出的路徑。"""
        os.makedirs(base_dir, exist_ok=True)
        date_c = self.date.replace("-", "")
        paths: list[str] = []
        with self._lock:
            tapes = list(self.tapes.values())
            for tape in tapes:
                if not tape.samples:
                    continue
                data = {
                    "code": tape.code, "name": tape.name, "date": self.date,
                    "big_lots": self.big_lots, "milestone": self.milestone,
                    "started_at": self.started_at,
                    "ended_at": tape.samples[-1]["t"],
                    **tape.to_dict(),
                }
                path = os.path.join(base_dir, f"shenshou_{tape.code}_{date_c}.json")
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False)
                    paths.append(path)
                except Exception as e:  # noqa: BLE001
                    log.warning("save shenshou record %s failed: %s", path, e)
        return paths


def list_records(base_dir: str = RECORDS_DIR) -> list[dict]:
    """列出已錄製檔（供回放下拉）。回 [{path, code, name, date, events, samples}]，新到舊。"""
    out: list[dict] = []
    if not os.path.isdir(base_dir):
        return out
    for fn in os.listdir(base_dir):
        if not (fn.startswith("shenshou_") and fn.endswith(".json")):
            continue
        path = os.path.join(base_dir, fn)
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            out.append({
                "path": path, "code": d.get("code", ""),
                "name": d.get("name", ""), "date": d.get("date", ""),
                "events": len(d.get("events") or []),
                "samples": len(d.get("samples") or []),
            })
        except Exception as e:  # noqa: BLE001
            log.warning("read shenshou record %s failed: %s", path, e)
    out.sort(key=lambda r: (r["date"], r["code"]), reverse=True)
    return out


def load_record(path: str) -> dict | None:
    """讀單一錄製檔。"""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        log.warning("load shenshou record %s failed: %s", path, e)
        return None


_VOL_SURGE_MULT = 3.0      # 出量：竭盡段「每筆均量」≥ 此倍數 × 全場每筆均量
_TURN_CONFIRM_TICKS = 10   # 轉盤確認視窗：竭盡事件後 N 筆內外盤量比較


def backtest_exhaustion(record: dict, direction: str = "long",
                        require_volume: bool = False,
                        vol_mult: float = _VOL_SURGE_MULT,
                        require_turn: bool = False,
                        turn_ticks: int = _TURN_CONFIRM_TICKS,
                        close_at_end: bool = True) -> dict:
    """以「竭盡事件」配對回測，算勝率。

    - direction='long'（作多）：賣盤竭盡→買進，買盤竭盡→賣出。
    - direction='short'（作空）：買盤竭盡→放空，賣盤竭盡→回補。
    - require_volume=True：進場的竭盡事件需「出量」——該竭盡段每筆均量
      (vol/count) ≥ vol_mult × 全場每筆均量，才進場（出場不受此限）。
    - require_turn=True：出場的竭盡事件需「已轉盤」確認——事件後 turn_ticks 筆內，
      作多需內盤量>外盤量（轉內盤）、作空需外盤量>內盤量（轉外盤）才出場，且以
      確認視窗末筆價成交；未轉盤則不出場、續持等下一次。
    收盤仍有未平倉者以最後一筆成交價平倉計入（close_at_end）。

    回 {direction, require_volume, vol_mult, require_turn, count, wins, losses,
        win_rate(%), total_ret(%), avg_ret(%),
        trades:[{entry_t,entry_p,exit_t,exit_p,ret,reason}]}。
    """
    events = record.get("events") or []
    samples = record.get("samples") or []
    n = len(samples)
    is_long = direction != "short"
    entry_type = EV_SELL_EXHAUST if is_long else EV_BUY_EXHAUST
    exit_type = EV_BUY_EXHAUST if is_long else EV_SELL_EXHAUST

    # 全場每筆均量（出量基準）
    svols = [s.get("v", 0) for s in samples if (s.get("v", 0) or 0) > 0]
    avg_v = (sum(svols) / len(svols)) if svols else 0.0

    def _is_surge(e) -> bool:
        c = e.get("count") or 0
        v = e.get("vol") or 0
        rate = (v / c) if c else v      # 竭盡段每筆均量
        return avg_v > 0 and rate >= vol_mult * avg_v

    def _confirm_turn(i):
        """出場竭盡事件後 turn_ticks 筆內外盤量比較，確認是否已轉盤。
        作多需轉內盤(內>外)、作空需轉外盤(外>內)。回確認點索引或 None。"""
        seg = samples[i:i + turn_ticks]
        if not seg:
            return None
        inner = sum((s.get("v", 0) or 0) for s in seg
                    if (s.get("s", 0) or 0) < 0)
        outer = sum((s.get("v", 0) or 0) for s in seg
                    if (s.get("s", 0) or 0) > 0)
        turned = (inner > outer) if is_long else (outer > inner)
        return min(i + turn_ticks - 1, n - 1) if turned else None

    evs = sorted(
        (e for e in events if e.get("type") in (entry_type, exit_type)),
        key=lambda e: e.get("i", 0))

    def _ret(ep, xp):
        if not ep:
            return 0.0
        return (xp - ep) / ep * 100 if is_long else (ep - xp) / ep * 100

    trades: list[dict] = []
    pos = None
    for e in evs:
        if pos is None:
            if e.get("type") == entry_type and (
                    not require_volume or _is_surge(e)):
                pos = e
        elif e.get("type") == exit_type:
            if require_turn:
                j = _confirm_turn(e.get("i", 0))
                if j is None:
                    continue      # 尚未轉盤 → 不出場，續持
                xt, xp = samples[j].get("t"), samples[j].get("p")
                reason = "竭盡+轉盤出場"
            else:
                xt, xp = e.get("t"), e.get("price")
                reason = "竭盡出場"
            ep = pos.get("price")
            trades.append({"entry_t": pos.get("t"), "entry_p": ep,
                           "exit_t": xt, "exit_p": xp,
                           "ret": round(_ret(ep, xp), 3), "reason": reason})
            pos = None
    # 收盤前平倉（仍持倉者以最後一筆成交價結算）
    if pos is not None and close_at_end and samples:
        ep = pos.get("price")
        xp = samples[-1].get("p")
        trades.append({"entry_t": pos.get("t"), "entry_p": ep,
                       "exit_t": samples[-1].get("t"), "exit_p": xp,
                       "ret": round(_ret(ep, xp), 3), "reason": "收盤平倉"})

    count = len(trades)
    wins = sum(1 for t in trades if t["ret"] > 0)
    losses = sum(1 for t in trades if t["ret"] < 0)
    total_ret = sum(t["ret"] for t in trades)
    return {
        "direction": direction,
        "require_volume": require_volume, "vol_mult": vol_mult,
        "require_turn": require_turn,
        "count": count, "wins": wins, "losses": losses,
        "win_rate": round(wins / count * 100, 1) if count else 0.0,
        "total_ret": round(total_ret, 2),
        "avg_ret": round(total_ret / count, 3) if count else 0.0,
        "trades": trades,
    }
