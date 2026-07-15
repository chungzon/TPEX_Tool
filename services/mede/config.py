"""MEDE 集中設定 — 所有門檻/參數集中於此，不散落程式碼；每次錄製/回測保存 config_hash。

Phase 2 只放「錄製相關」欄位；Detector/Fusion/Outcome 參數於後續階段擴充。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, fields


@dataclass
class MedeConfig:
    # --- 追蹤標的 ---
    tracked_symbols: list[str] = field(default_factory=list)
    max_symbols: int = 5                 # 第一版訂閱檔數上限（Shioaji 訂閱限制）

    # --- 逐筆資料層（SQLite 起步）---
    storage_dir: str = "mede_data"       # 每交易日一檔 mede_YYYYMMDD.sqlite
    write_batch_size: int = 500          # 批次寫入筆數
    write_flush_interval_s: float = 1.0  # 批次寫入最長間隔
    queue_maxsize: int = 200_000         # callback→writer 佇列上限（滿則丟棄並計數）

    # --- 記憶體 ring buffer（供後續 Feature Engine；Phase 2 先保留）---
    ring_buffer_ticks: int = 8_000
    ring_buffer_bidask: int = 8_000

    # --- 斷線重連 / 資料品質 ---
    stale_seconds: float = 20.0          # 盤中資料停滯超過此秒 → 觸發重連檢查
    watchdog_interval_s: float = 5.0
    market_open: str = "09:00:00"
    market_close: str = "13:30:00"
    max_unknown_dir_ratio: float = 0.30  # unknown 方向比例超過 → 標 DEGRADED
    min_ticks_valid_day: int = 200       # 當日 tick 數低於此 → 標 INVALID

    # --- Phase 4 偵測器 ---
    minimum_warmup_ticks: int = 100     # 暖身不足不觸發
    baseline_window: int = 300          # z-score 基準樣本數
    trade_burst_zscore: float = 3.0
    volume_burst_zscore: float = 3.0
    flow_imbalance_threshold: float = 0.55
    ofi_shock_zscore: float = 2.5
    book_imbalance_threshold: float = 0.5
    burst_window_label: str = "1s"      # burst/flow 主要參考視窗
    detector_enabled: dict = field(default_factory=dict)   # name -> bool（預設全開）
    detector_weights: dict = field(default_factory=dict)   # name -> weight（Phase 5 融合）

    # --- 版本 ---
    algorithm_version: str = "mede-0.1"
    parameter_version: str = "p4-0.1"

    def config_hash(self) -> str:
        raw = json.dumps(asdict(self), sort_keys=True, ensure_ascii=False)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]

    @classmethod
    def from_dict(cls, d: dict | None) -> "MedeConfig":
        d = d or {}
        valid = {f.name for f in fields(cls)}
        clean = {k: v for k, v in d.items() if k in valid}
        try:
            return cls(**clean)
        except (TypeError, ValueError):
            return cls()

    def to_dict(self) -> dict:
        return asdict(self)
