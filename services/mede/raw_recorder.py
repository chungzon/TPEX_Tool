"""RawRecorder — 即時 Tick/BidAsk 錄製。

流程：Shioaji socket 緒 → on_tick/on_bidask 只做「非阻塞入列」→ 單一 writer 緒
批次落地到 storage，並更新 ring buffer 與資料品質計數。callback 不碰 DB、不阻塞。
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from collections import deque

from services.mede.enums import StreamKind, TradeSide, DataQualityStatus
from services.mede.storage import Storage

log = logging.getLogger(__name__)


class _CodeQuality:
    __slots__ = ("tick_count", "bidask_count", "unknown_dir_count",
                 "out_of_order_count", "reconnect_count", "first_tick_time",
                 "last_tick_time", "last_seq", "max_gap_ms", "_last_recv_ns")

    def __init__(self):
        self.tick_count = 0
        self.bidask_count = 0
        self.unknown_dir_count = 0
        self.out_of_order_count = 0
        self.reconnect_count = 0
        self.first_tick_time = ""
        self.last_tick_time = ""
        self.last_seq = -1
        self.max_gap_ms = 0.0
        self._last_recv_ns = 0


class RawRecorder:
    def __init__(self, storage: Storage, config):
        self._storage = storage
        self._cfg = config
        self._q: queue.Queue = queue.Queue(maxsize=config.queue_maxsize)
        self._stop = threading.Event()
        self._writer: threading.Thread | None = None
        self._date: str = ""
        self.dropped_count = 0                     # 佇列滿而丟棄（全域）
        self.last_tick_recv_ns = 0
        self.last_bidask_recv_ns = 0
        self._q_hi_watermark = 0
        # per-code
        self._quality: dict[str, _CodeQuality] = {}
        self._ring_tick: dict[str, deque] = {}
        self._ring_bidask: dict[str, deque] = {}
        self._lock = threading.Lock()

    # ---------------- socket 緒：僅入列（快） ----------------
    def on_tick(self, data: dict) -> None:
        self.last_tick_recv_ns = data.get("received_at_ns", 0)
        try:
            self._q.put_nowait((StreamKind.TICK, data))
        except queue.Full:
            self.dropped_count += 1

    def on_bidask(self, data: dict) -> None:
        self.last_bidask_recv_ns = data.get("received_at_ns", 0)
        try:
            self._q.put_nowait((StreamKind.BIDASK, data))
        except queue.Full:
            self.dropped_count += 1

    def note_reconnect(self, code: str = "*") -> None:
        q = self._quality.setdefault(code, _CodeQuality())
        q.reconnect_count += 1

    # ---------------- writer 緒：批次落地 ----------------
    def start(self, trade_date: str) -> None:
        self._date = trade_date
        self._storage.open(trade_date)
        self._stop.clear()
        self._writer = threading.Thread(target=self._run, name="mede-writer",
                                        daemon=True)
        self._writer.start()

    def _run(self) -> None:
        cfg = self._cfg
        while not self._stop.is_set():
            batch = self._drain(cfg.write_batch_size, cfg.write_flush_interval_s)
            if batch:
                self._flush(batch)
        # 收尾：清空剩餘佇列
        rest = self._drain(10 ** 9, 0.0)
        if rest:
            self._flush(rest)

    def _drain(self, max_items: int, max_wait_s: float) -> list:
        out = []
        deadline = time.monotonic() + max_wait_s
        try:
            out.append(self._q.get(timeout=max_wait_s))
        except queue.Empty:
            return out
        while len(out) < max_items:
            try:
                out.append(self._q.get_nowait())
            except queue.Empty:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.005)
        qs = self._q.qsize()
        if qs > self._q_hi_watermark:
            self._q_hi_watermark = qs
        return out

    def _flush(self, batch: list) -> None:
        tick_rows, ba_rows = [], []
        for kind, d in batch:
            code = d.get("code", "")
            # 跨日：以成交時間日期為準，切換 storage 檔並保存前一日品質
            rec_date = (d.get("time") or "")[:10]
            if rec_date and rec_date != self._date and "-" in rec_date:
                self._roll_to(rec_date)
            if kind == StreamKind.TICK:
                tick_rows.append(self._pack_tick(d))
                self._update_tick_quality(code, d)
            else:
                ba_rows.append(self._pack_bidask(d))
                self._update_bidask_quality(code, d)
        try:
            if tick_rows:
                self._storage.write_ticks(tick_rows)
            if ba_rows:
                self._storage.write_bidasks(ba_rows)
        except Exception:
            log.exception("storage write failed")

    def _roll_to(self, new_date: str) -> None:
        """跨日：保存舊日品質、開新檔、重置 per-day 狀態。"""
        self.save_quality()
        with self._lock:
            self._quality.clear()
            self._ring_tick.clear()
            self._ring_bidask.clear()
        self._date = new_date
        self._storage.open(new_date)

    @staticmethod
    def _f(x, d=0.0):
        try:
            return float(x)
        except (TypeError, ValueError):
            return d

    def _pack_tick(self, d: dict) -> tuple:
        return (d.get("code", ""), d.get("time", ""), d.get("received_at_ns", 0),
                d.get("seq", 0), self._f(d.get("close")), self._f(d.get("volume")),
                self._f(d.get("total_volume")), self._f(d.get("avg_price")),
                int(d.get("tick_type", 0) or 0))

    def _pack_bidask(self, d: dict) -> tuple:
        return (d.get("code", ""), d.get("time", ""), d.get("received_at_ns", 0),
                d.get("seq", 0), json.dumps(d.get("bid_price", [])),
                json.dumps(d.get("bid_volume", [])), json.dumps(d.get("ask_price", [])),
                json.dumps(d.get("ask_volume", [])))

    def _update_tick_quality(self, code: str, d: dict) -> None:
        with self._lock:
            q = self._quality.setdefault(code, _CodeQuality())
            q.tick_count += 1
            if TradeSide.from_tick_type(d.get("tick_type", 0)) is TradeSide.UNKNOWN:
                q.unknown_dir_count += 1
            seq = int(d.get("seq", 0) or 0)
            if q.last_seq >= 0 and seq < q.last_seq:
                q.out_of_order_count += 1
            q.last_seq = seq
            t = d.get("time", "")
            if not q.first_tick_time:
                q.first_tick_time = t
            q.last_tick_time = t
            recv = int(d.get("received_at_ns", 0) or 0)
            if q._last_recv_ns and recv > q._last_recv_ns:
                gap_ms = (recv - q._last_recv_ns) / 1e6
                if gap_ms > q.max_gap_ms:
                    q.max_gap_ms = gap_ms
            q._last_recv_ns = recv
            rb = self._ring_tick.setdefault(
                code, deque(maxlen=self._cfg.ring_buffer_ticks))
            rb.append(d)

    def _update_bidask_quality(self, code: str, d: dict) -> None:
        with self._lock:
            q = self._quality.setdefault(code, _CodeQuality())
            q.bidask_count += 1
            rb = self._ring_bidask.setdefault(
                code, deque(maxlen=self._cfg.ring_buffer_bidask))
            rb.append(d)

    # ---------------- 品質保存 / 狀態 ----------------
    def _status_of(self, q: _CodeQuality) -> str:
        if q.tick_count < self._cfg.min_ticks_valid_day:
            return DataQualityStatus.INVALID.value
        ratio = q.unknown_dir_count / max(q.tick_count, 1)
        if ratio > self._cfg.max_unknown_dir_ratio or q.out_of_order_count:
            return DataQualityStatus.DEGRADED.value
        return DataQualityStatus.OK.value

    def save_quality(self) -> None:
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        chash = self._cfg.config_hash()
        with self._lock:
            items = list(self._quality.items())
        for code, q in items:
            try:
                self._storage.write_quality({
                    "trade_date": self._date, "code": code,
                    "tick_count": q.tick_count, "bidask_count": q.bidask_count,
                    "unknown_dir_count": q.unknown_dir_count,
                    "dropped_count": self.dropped_count,
                    "out_of_order_count": q.out_of_order_count,
                    "reconnect_count": q.reconnect_count,
                    "max_gap_ms": round(q.max_gap_ms, 2),
                    "first_tick_time": q.first_tick_time,
                    "last_tick_time": q.last_tick_time,
                    "status": self._status_of(q), "config_hash": chash,
                    "saved_at": now})
            except Exception:
                log.exception("save_quality failed for %s", code)

    def status(self) -> dict:
        now_ns = time.time_ns()
        with self._lock:
            per_code = {c: {"ticks": q.tick_count, "bidask": q.bidask_count,
                            "unknown": q.unknown_dir_count,
                            "out_of_order": q.out_of_order_count,
                            "max_gap_ms": round(q.max_gap_ms, 1),
                            "status": self._status_of(q),
                            "last_tick_time": q.last_tick_time}
                        for c, q in self._quality.items()}
        return {
            "queue_size": self._q.qsize(),
            "queue_hi_watermark": self._q_hi_watermark,
            "dropped_count": self.dropped_count,
            "tick_lag_ms": (now_ns - self.last_tick_recv_ns) / 1e6
                           if self.last_tick_recv_ns else None,
            "bidask_lag_ms": (now_ns - self.last_bidask_recv_ns) / 1e6
                             if self.last_bidask_recv_ns else None,
            "writer_alive": bool(self._writer and self._writer.is_alive()),
            "per_code": per_code,
        }

    def get_ring(self, code: str):
        with self._lock:
            return (list(self._ring_tick.get(code, [])),
                    list(self._ring_bidask.get(code, [])))

    def stop(self) -> None:
        """安全收尾：停 writer、清空佇列、保存品質、關 storage。"""
        self._stop.set()
        if self._writer:
            self._writer.join(timeout=10)
        self.save_quality()
        self._storage.close()
