"""MEDE 逐筆資料層 — 抽象介面 + SQLite 實作（每交易日一檔）。

storage 介面刻意抽象，日後量大可換 Parquet+DuckDB 而不動上層。
所有寫入由 raw_recorder 的「單一 writer 緒」呼叫；讀取供 replay。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from abc import ABC, abstractmethod

log = logging.getLogger(__name__)


class Storage(ABC):
    """逐筆資料層抽象。實作需保證：同日重複開啟不清資料；批次寫入；可讀回供重播。"""

    @abstractmethod
    def open(self, trade_date: str) -> None: ...

    @abstractmethod
    def write_ticks(self, rows: list[tuple]) -> None: ...

    @abstractmethod
    def write_bidasks(self, rows: list[tuple]) -> None: ...

    @abstractmethod
    def write_quality(self, row: dict) -> None: ...

    @abstractmethod
    def read_ticks(self, code: str, trade_date: str) -> list[dict]: ...

    @abstractmethod
    def read_bidasks(self, code: str, trade_date: str) -> list[dict]: ...

    @abstractmethod
    def close(self) -> None: ...


# tuple 欄位順序（writer 依此打包，減少 dict 開銷）
TICK_COLS = ("code", "exchange_time", "received_at_ns", "seq",
             "close", "volume", "total_volume", "avg_price", "tick_type")
BIDASK_COLS = ("code", "exchange_time", "received_at_ns", "seq",
               "bid_price", "bid_volume", "ask_price", "ask_volume")


class SqliteStorage(Storage):
    """每交易日一個 SQLite 檔：mede_YYYYMMDD.sqlite。WAL 模式利於邊寫邊讀。"""

    def __init__(self, storage_dir: str = "mede_data"):
        self._dir = storage_dir
        self._conn: sqlite3.Connection | None = None
        self._date: str = ""
        self._path: str = ""

    def _db_path(self, trade_date: str) -> str:
        return os.path.join(self._dir, f"mede_{trade_date.replace('-', '')}.sqlite")

    def open(self, trade_date: str) -> None:
        if self._conn is not None and self._date == trade_date:
            return
        self.close()
        os.makedirs(self._dir, exist_ok=True)
        self._date = trade_date
        self._path = self._db_path(trade_date)
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        c = self._conn
        c.execute("""
            CREATE TABLE IF NOT EXISTS raw_tick (
                code TEXT, exchange_time TEXT, received_at_ns INTEGER,
                seq INTEGER, close REAL, volume REAL, total_volume REAL,
                avg_price REAL, tick_type INTEGER)""")
        c.execute("""
            CREATE TABLE IF NOT EXISTS raw_bidask (
                code TEXT, exchange_time TEXT, received_at_ns INTEGER,
                seq INTEGER, bid_price TEXT, bid_volume TEXT,
                ask_price TEXT, ask_volume TEXT)""")
        c.execute("""
            CREATE TABLE IF NOT EXISTS mede_data_quality (
                trade_date TEXT, code TEXT, tick_count INTEGER,
                bidask_count INTEGER, unknown_dir_count INTEGER,
                dropped_count INTEGER, out_of_order_count INTEGER,
                reconnect_count INTEGER, max_gap_ms REAL,
                first_tick_time TEXT, last_tick_time TEXT,
                status TEXT, config_hash TEXT, saved_at TEXT,
                PRIMARY KEY (trade_date, code))""")
        c.execute("""
            CREATE TABLE IF NOT EXISTS mede_event (
                event_id TEXT PRIMARY KEY, code TEXT, event_time_ns INTEGER,
                seq INTEGER, event_type TEXT, direction INTEGER, score REAL,
                confidence REAL, trigger_price REAL, reasons TEXT,
                matched_patterns TEXT, detector_scores TEXT,
                consolidated_trigger_id TEXT, parameter_version TEXT,
                algorithm_version TEXT)""")
        c.execute("""
            CREATE TABLE IF NOT EXISTS mede_state_transition (
                code TEXT, from_state TEXT, to_state TEXT, t_ns INTEGER,
                reason TEXT, reference_price REAL)""")
        c.execute("CREATE INDEX IF NOT EXISTS ix_tick_code ON raw_tick(code, seq)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_ba_code ON raw_bidask(code, seq)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_event_code ON mede_event(code, seq)")
        c.commit()

    def write_ticks(self, rows: list[tuple]) -> None:
        if not rows or self._conn is None:
            return
        self._conn.executemany(
            f"INSERT INTO raw_tick ({','.join(TICK_COLS)}) "
            f"VALUES ({','.join('?' * len(TICK_COLS))})", rows)
        self._conn.commit()

    def write_bidasks(self, rows: list[tuple]) -> None:
        if not rows or self._conn is None:
            return
        self._conn.executemany(
            f"INSERT INTO raw_bidask ({','.join(BIDASK_COLS)}) "
            f"VALUES ({','.join('?' * len(BIDASK_COLS))})", rows)
        self._conn.commit()

    def write_quality(self, row: dict) -> None:
        if self._conn is None:
            return
        cols = ("trade_date", "code", "tick_count", "bidask_count",
                "unknown_dir_count", "dropped_count", "out_of_order_count",
                "reconnect_count", "max_gap_ms", "first_tick_time",
                "last_tick_time", "status", "config_hash", "saved_at")
        vals = tuple(row.get(k) for k in cols)
        self._conn.execute(
            f"INSERT OR REPLACE INTO mede_data_quality ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))})", vals)
        self._conn.commit()

    _EVENT_COLS = ("event_id", "code", "event_time_ns", "seq", "event_type",
                   "direction", "score", "confidence", "trigger_price", "reasons",
                   "matched_patterns", "detector_scores", "consolidated_trigger_id",
                   "parameter_version", "algorithm_version")

    def write_events(self, events: list) -> None:
        """events: list[Event]。以 JSON 存 reasons/patterns/detector_scores。"""
        if not events or self._conn is None:
            return
        rows = []
        for e in events:
            rows.append((e.event_id, e.code, e.event_time_ns, e.seq, e.event_type,
                         e.direction, e.score, e.confidence, e.trigger_price,
                         json.dumps(e.reasons, ensure_ascii=False),
                         json.dumps(e.matched_patterns, ensure_ascii=False),
                         json.dumps(e.detector_scores, ensure_ascii=False),
                         e.consolidated_trigger_id, e.parameter_version,
                         e.algorithm_version))
        self._conn.executemany(
            f"INSERT OR REPLACE INTO mede_event ({','.join(self._EVENT_COLS)}) "
            f"VALUES ({','.join('?' * len(self._EVENT_COLS))})", rows)
        self._conn.commit()

    def write_transitions(self, trans: list) -> None:
        if not trans or self._conn is None:
            return
        rows = [(t.code, t.from_state, t.to_state, t.t_ns, t.reason,
                 t.reference_price) for t in trans]
        self._conn.executemany(
            "INSERT INTO mede_state_transition "
            "(code, from_state, to_state, t_ns, reason, reference_price) "
            "VALUES (?,?,?,?,?,?)", rows)
        self._conn.commit()

    def read_events(self, code: str, trade_date: str) -> list[dict]:
        self.open(trade_date)
        cur = self._conn.execute(
            f"SELECT {','.join(self._EVENT_COLS)} FROM mede_event "
            f"WHERE code=? ORDER BY seq", (code,))
        out = []
        for r in cur.fetchall():
            d = dict(zip(self._EVENT_COLS, r))
            for k in ("reasons", "matched_patterns", "detector_scores"):
                d[k] = json.loads(d[k]) if d[k] else None
            out.append(d)
        return out

    def read_codes(self, trade_date: str) -> list[str]:
        """回傳當日 raw_tick 中有錄到資料的股票代碼（供偵測重播列舉）。"""
        if not os.path.exists(self._db_path(trade_date)):
            return []
        self.open(trade_date)
        cur = self._conn.execute(
            "SELECT DISTINCT code FROM raw_tick WHERE code IS NOT NULL ORDER BY code")
        return [r[0] for r in cur.fetchall()]

    def read_ticks(self, code: str, trade_date: str) -> list[dict]:
        self.open(trade_date)
        cur = self._conn.execute(
            f"SELECT {','.join(TICK_COLS)} FROM raw_tick WHERE code=? ORDER BY seq",
            (code,))
        return [dict(zip(TICK_COLS, r)) for r in cur.fetchall()]

    def read_bidasks(self, code: str, trade_date: str) -> list[dict]:
        self.open(trade_date)
        cur = self._conn.execute(
            f"SELECT {','.join(BIDASK_COLS)} FROM raw_bidask WHERE code=? ORDER BY seq",
            (code,))
        out = []
        for r in cur.fetchall():
            d = dict(zip(BIDASK_COLS, r))
            for k in ("bid_price", "bid_volume", "ask_price", "ask_volume"):
                d[k] = json.loads(d[k]) if d[k] else []
            out.append(d)
        return out

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.commit()
                self._conn.close()
            except Exception:
                log.exception("sqlite close failed")
            self._conn = None
