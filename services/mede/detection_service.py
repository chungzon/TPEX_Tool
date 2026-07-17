"""DetectionService — MEDE Phase 5 對外呼叫端。

把「已錄製的逐筆資料」跑過完整偵測管線並落地：
    raw_tick / raw_bidask ──TickReplayEngine.replay_detect──▶ MedeEngine
        ──▶ FeatureEngine → Detectors → FusionEngine → EventStateMachine
        ──▶ events / transitions ──▶ SqliteStorage.write_events / write_transitions

決定性：以 seq 排序重播，與即時模式吃同一份事件序 → 結果可重現。
不下單，只產生候選事件供人工/後續回測檢視。
"""

from __future__ import annotations

import glob
import logging
import os
from dataclasses import dataclass, field

from services.mede.config import MedeConfig
from services.mede.storage import SqliteStorage
from services.mede.replay import TickReplayEngine
from services.mede.tick_size import tw_tick_size

log = logging.getLogger(__name__)


@dataclass
class DetectionRun:
    code: str
    trade_date: str
    tick_count: int
    bidask_available: bool
    event_count: int
    transition_count: int
    config_hash: str
    persisted: bool
    events: list = field(default_factory=list)        # list[Event]
    transitions: list = field(default_factory=list)   # list[Transition]

    def summary(self) -> str:
        ba = "含委託簿" if self.bidask_available else "純Tick"
        return (f"{self.code} {self.trade_date}｜{self.tick_count} ticks（{ba}）"
                f"→ 事件 {self.event_count}、轉移 {self.transition_count}"
                f"｜{'已存' if self.persisted else '未存'}")


class DetectionService:
    """對已錄製資料執行偵測管線。無狀態；可重複呼叫，重跑同日同股會覆蓋 events。"""

    def __init__(self, config: MedeConfig | None = None,
                 storage: SqliteStorage | None = None):
        self.config = config or MedeConfig()
        self._storage = storage or SqliteStorage(self.config.storage_dir)

    def list_dates(self) -> list[str]:
        """掃描 storage_dir，列出有錄製檔的交易日（yyyy-mm-dd，新→舊）。"""
        pat = os.path.join(self.config.storage_dir, "mede_*.sqlite")
        dates = []
        for p in glob.glob(pat):
            stem = os.path.basename(p)[len("mede_"):-len(".sqlite")]
            if len(stem) == 8 and stem.isdigit():
                dates.append(f"{stem[:4]}-{stem[4:6]}-{stem[6:]}")
        return sorted(dates, reverse=True)

    def list_recorded(self, trade_date: str) -> list[str]:
        """列出當日有錄到 tick 的股票代碼。"""
        return self._storage.read_codes(trade_date)

    def run(self, code: str, trade_date: str, *, persist: bool = True,
            on_event=None, stop_flag=None) -> DetectionRun:
        """對單股跑完整偵測；persist=True 時把 events/transitions 寫入當日 SQLite。"""
        engine = TickReplayEngine(self._storage, tw_tick_size)
        events, transitions, has_ba = engine.replay_detect(
            code, trade_date, self.config, on_event=on_event, stop_flag=stop_flag)
        tick_count = len(self._storage.read_ticks(code, trade_date))

        persisted = False
        if persist and (events or transitions):
            self._storage.open(trade_date)
            self._storage.write_events(events)
            self._storage.write_transitions(transitions)
            persisted = True

        run = DetectionRun(
            code=code, trade_date=trade_date, tick_count=tick_count,
            bidask_available=has_ba, event_count=len(events),
            transition_count=len(transitions),
            config_hash=self.config.config_hash(), persisted=persisted,
            events=events, transitions=transitions)
        log.info("MEDE detect: %s", run.summary())
        return run

    def run_all(self, trade_date: str, *, persist: bool = True,
                on_event=None, stop_flag=None) -> list[DetectionRun]:
        """對當日所有錄到的股票逐一偵測。"""
        runs = []
        for code in self.list_recorded(trade_date):
            if stop_flag is not None and stop_flag.is_set():
                break
            runs.append(self.run(code, trade_date, persist=persist,
                                  on_event=on_event, stop_flag=stop_flag))
        return runs

    def read_events(self, code: str, trade_date: str) -> list[dict]:
        """讀回已落地的偵測事件（供 UI/回測檢視）。"""
        return self._storage.read_events(code, trade_date)

    def backtest(self, code: str, trade_date: str, params=None):
        """對已錄製資料跑 BED 事件驅動回測，回傳 BedBacktestResult。"""
        from services.mede.backtest import BedBacktester
        return BedBacktester(self.config, params).run(self._storage, code, trade_date)
