"""隔日沖監控 — 事件錄製 / 回放。

監控中把各標的的即時彙總（現價、自營賣壓、認購/認售淨）逐秒記成時間序列，並在條件
成立時記事件，供收盤後回放（價格走勢 + 自營賣壓 + 事件標記）。

事件：
- sell_pressure：自營賣壓突破門檻（≥ _SP_TH，上緣觸發）。
- call_dump    ：認購權證被大量倒賣（區間認購內盤賣量增 ≥ _BURST）。
- put_load     ：認售權證大量買進（區間認售外盤買量增 ≥ _BURST）。
- below_cost   ：股價跌破主力成本（由上而下穿越，一次）。

純資料 + JSON I/O；每檔一個檔案 nextday_{code}_{YYYYMMDD}.json。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime

log = logging.getLogger(__name__)

RECORDS_DIR = "nextday_data"
_SP_TH = 200        # 自營賣壓門檻（張）
_BURST = 100        # 認購倒賣 / 認售買進 區間爆量門檻（張）

EV_SELL_PRESSURE = "sell_pressure"    # 自營賣壓突破
EV_CALL_DUMP = "call_dump"            # 認購被倒賣
EV_PUT_LOAD = "put_load"              # 認售大量買進
EV_BELOW_COST = "below_cost"          # 跌破主力成本

EV_LABEL = {
    EV_SELL_PRESSURE: "自營賣壓",
    EV_CALL_DUMP: "認購倒賣",
    EV_PUT_LOAD: "認售買進",
    EV_BELOW_COST: "破主力成本",
}


@dataclass
class Tape:
    code: str
    name: str = ""
    main_cost: float = 0.0
    prev_close: float = 0.0
    samples: list = field(default_factory=list)   # [{t, p, sp, cn, pn}]
    events: list = field(default_factory=list)     # [{t, i, type, price, sp}]
    _prev: dict | None = None
    _below_fired: bool = False

    def to_dict(self) -> dict:
        return {"code": self.code, "name": self.name,
                "main_cost": self.main_cost, "prev_close": self.prev_close,
                "samples": self.samples, "events": self.events}


class NextDayRecorder:
    """一次監控 session 的事件錄製器（多檔）。"""

    def __init__(self, date: str = "", sp_th: int = _SP_TH, burst: int = _BURST):
        self.date = date or datetime.now().strftime("%Y-%m-%d")
        self.sp_th = sp_th
        self.burst = burst
        self.tapes: dict[str, Tape] = {}
        self.started_at = datetime.now().strftime("%H:%M:%S")
        self._lock = threading.Lock()

    def register(self, code: str, name: str = "", main_cost: float = 0.0,
                 prev_close: float = 0.0) -> None:
        with self._lock:
            self.tapes[code] = Tape(code=code, name=name or code,
                                    main_cost=main_cost or 0.0,
                                    prev_close=prev_close or 0.0)

    def on_update(self, code: str, snap: dict) -> None:
        """吃一筆即時彙總（_emit_monitor 的 row）；記樣本並偵測事件。"""
        p = float(snap.get("price") or 0.0)
        if p <= 0:
            return
        t = datetime.now().strftime("%H:%M:%S")
        sp = int(snap.get("sell_pressure") or 0)
        cs = int(snap.get("call_sell") or 0)
        pb = int(snap.get("put_buy") or 0)
        with self._lock:
            tape = self.tapes.get(code)
            if tape is None:
                return
            tape.samples.append({"t": t, "p": p, "sp": sp,
                                 "cn": int(snap.get("call_net") or 0),
                                 "pn": int(snap.get("put_net") or 0)})
            i = len(tape.samples) - 1
            prev = tape._prev
            if prev is not None:
                if prev["sp"] < self.sp_th <= sp:
                    self._ev(tape, i, t, EV_SELL_PRESSURE, p, sp)
                if cs - prev["cs"] >= self.burst:
                    self._ev(tape, i, t, EV_CALL_DUMP, p, sp)
                if pb - prev["pb"] >= self.burst:
                    self._ev(tape, i, t, EV_PUT_LOAD, p, sp)
                if (tape.main_cost and not tape._below_fired
                        and prev["p"] >= tape.main_cost > p):
                    self._ev(tape, i, t, EV_BELOW_COST, p, sp)
                    tape._below_fired = True
            tape._prev = {"sp": sp, "cs": cs, "pb": pb, "p": p}

    @staticmethod
    def _ev(tape, i, t, typ, price, sp):
        tape.events.append({"t": t, "i": i, "type": typ,
                            "price": round(price, 2), "sp": sp})

    def save(self, base_dir: str = RECORDS_DIR) -> list[str]:
        os.makedirs(base_dir, exist_ok=True)
        date_c = self.date.replace("-", "")
        paths: list[str] = []
        with self._lock:
            for tape in list(self.tapes.values()):
                if not tape.samples:
                    continue
                data = {"date": self.date, "started_at": self.started_at,
                        "ended_at": tape.samples[-1]["t"],
                        "sp_th": self.sp_th, **tape.to_dict()}
                path = os.path.join(base_dir, f"nextday_{tape.code}_{date_c}.json")
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False)
                    paths.append(path)
                except Exception as e:  # noqa: BLE001
                    log.warning("save nextday record %s failed: %s", path, e)
        return paths


def list_records(base_dir: str = RECORDS_DIR) -> list[dict]:
    """列出已錄製檔（供回放下拉）。新到舊。"""
    out: list[dict] = []
    if not os.path.isdir(base_dir):
        return out
    for fn in os.listdir(base_dir):
        if not (fn.startswith("nextday_") and fn.endswith(".json")):
            continue
        path = os.path.join(base_dir, fn)
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            out.append({"path": path, "code": d.get("code", ""),
                        "name": d.get("name", ""), "date": d.get("date", ""),
                        "events": len(d.get("events") or []),
                        "samples": len(d.get("samples") or [])})
        except Exception as e:  # noqa: BLE001
            log.warning("read nextday record %s failed: %s", path, e)
    out.sort(key=lambda r: (r["date"], r["code"]), reverse=True)
    return out


def load_record(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # noqa: BLE001
        log.warning("load nextday record %s failed: %s", path, e)
        return None
