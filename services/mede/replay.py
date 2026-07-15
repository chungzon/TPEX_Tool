"""TickReplayEngine — 用與即時模式**完全相同**的 FeatureEngine 重播歷史原始資料。

排序：以 `seq`（全域遞增序＝原始接收/internal_sequence 順序）為準，
      這正是即時模式處理事件的順序 → 保證「即時模擬 == 重播」（決定性）。
      視窗時間則用 exchange_time（不看牆鐘）。
禁止未來函數：每筆快照只用當下與過去。
Tick-only（無 BidAsk）：book/OFI 特徵維持預設、bidask_available=False，並自動略過相依偵測（後階段）。
"""

from __future__ import annotations

import time as _time

from services.mede.feature_engine import FeatureEngine, parse_exchange_ns
from services.mede.storage import Storage


class TickReplayEngine:
    def __init__(self, storage: Storage, tick_size_fn,
                 time_windows=None, event_windows=None):
        self._storage = storage
        self._tsf = tick_size_fn
        self._tw = time_windows
        self._ew = event_windows

    def load_events(self, code: str, trade_date: str):
        """回傳 (events, has_bidask)。events = [(seq, kind, data)] 依 seq 排序。"""
        ticks = self._storage.read_ticks(code, trade_date)
        bas = self._storage.read_bidasks(code, trade_date)
        events = []
        for t in ticks:
            events.append((int(t.get("seq") or 0), "tick", t))
        for b in bas:
            events.append((int(b.get("seq") or 0), "bidask", b))
        events.sort(key=lambda e: e[0])
        return events, bool(bas)

    def replay(self, code: str, trade_date: str, on_snapshot=None,
               speed: str = "fastest", stop_flag=None):
        """重播並回傳 (snapshots, has_bidask)。
        speed: 'fastest' | 'realtime' | 數字倍速字串（如 '4'）。
        on_snapshot(snapshot, tick_row)：每筆成交後回呼（供偵測/診斷）。"""
        events, has_ba = self.load_events(code, trade_date)
        fe = FeatureEngine(self._tsf, time_windows=self._tw, event_windows=self._ew)
        snaps = []
        try:
            mult = 0.0 if speed == "fastest" else (
                1.0 if speed == "realtime" else max(float(speed), 1e-6))
        except ValueError:
            mult = 0.0
        prev_ns = None

        for seq, kind, data in events:
            if stop_flag is not None and stop_flag.is_set():
                break
            if mult > 0:
                cur_ns = parse_exchange_ns(data.get("exchange_time", ""))
                if prev_ns is not None and cur_ns is not None and cur_ns >= prev_ns:
                    _time.sleep(min((cur_ns - prev_ns) / 1e9 / mult, 2.0))
                if cur_ns is not None:
                    prev_ns = cur_ns

            if kind == "tick":
                fe.on_tick({
                    "code": data.get("code", ""), "time": data.get("exchange_time", ""),
                    "close": data.get("close"), "volume": data.get("volume"),
                    "tick_type": data.get("tick_type", 0), "seq": data.get("seq", 0)})
                snap = fe.snapshot()
                snaps.append(snap)
                if on_snapshot:
                    on_snapshot(snap, data)
            else:
                fe.on_bidask({
                    "code": data.get("code", ""), "time": data.get("exchange_time", ""),
                    "bid_price": data.get("bid_price", []),
                    "bid_volume": data.get("bid_volume", []),
                    "ask_price": data.get("ask_price", []),
                    "ask_volume": data.get("ask_volume", []),
                    "seq": data.get("seq", 0)})
        return snaps, has_ba
