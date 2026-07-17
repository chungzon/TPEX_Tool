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
    # --- Phase 4 batch2（價格/委託簿動態相關）---
    breakout_lookback_ticks: int = 100  # 突破：近 N 筆高低
    velocity_window_ms: int = 1000      # 價速/區間 參考視窗
    sweep_min_ticks: int = 3            # 掃單：短時間跨價位數門檻
    queue_collapse_ratio: float = 0.6   # (消耗+撤單)/前量 ≥ 此 → 掛單崩塌
    replenishment_ratio: float = 2.0    # 累積消耗/顯示量 ≥ 此 → 疑似補單/隱藏流動性
    absorption_min_volume: float = 40.0  # 吸收：窗內主動量門檻(張)
    absorption_max_price_ticks: float = 1.0  # 吸收：價格移動 ≤ 此(tick)
    absorption_min_trades: int = 5       # 吸收：窗內成交筆數門檻（避免單一大單誤判）

    # --- BED 空方結構（Phase 3 feature 擴充）---
    swing_reversal_ticks: float = 3.0    # ZigZag 反轉確認門檻（tick）
    swing_confirm_ticks: int = 3         # swing 確認最少筆數（防未來函數）
    vwap_slope_window_ms: int = 3000     # VWAP 斜率參考視窗
    spread_expansion_ratio: float = 2.0  # 流動性真空：spread ≥ 基準×此
    failed_breakout_timeout_ms: int = 3000  # 假突破：突破後多久內反向跌回才算
    exhaustion_fade_ratio: float = 0.4   # 衰竭：成交速度/OFI 低於峰值×此
    exhaustion_timeout_ms: int = 5000    # 衰竭觀察期
    momentum_min_categories: int = 4     # 動能點火：至少幾類不同證據共振
    detector_enabled: dict = field(default_factory=dict)   # name -> bool（預設全開）
    detector_weights: dict = field(default_factory=dict)   # name -> weight

    # --- Phase 5 融合 / 狀態機 ---
    spread_max_ticks: float = 4.0            # 必要條件：點差不得超過此
    bull_trigger_score: float = 120.0        # 加權多方分門檻
    bear_trigger_score: float = 120.0
    trigger_min_detector_categories: int = 3  # 觸發需至少幾類不同證據
    veto_absorption_score: float = 50.0      # 反向吸收分數過高 → 否決
    continuation_min_score: float = 40.0     # 延續門檻
    cooldown_ms: int = 5000                  # 觸發後冷卻
    merge_window_ms: int = 3000              # 同波同向事件合併窗
    max_events_per_stock_per_day: int = 200
    pattern_enabled: dict = field(default_factory=dict)

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
