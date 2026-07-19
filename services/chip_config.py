"""ChipConfig — 籌碼波段（CHIP）集中設定：窗口 / 權重 / 門檻。

比照 MedeConfig 慣例，所有可調參數集中於此，不散落程式碼。第一版為規則 + 加權；
detector 權重(weights)日後可由 LightGBM 學習後覆蓋（保留規則版可回退）。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ChipConfig:
    # --- 主力 / 集中度 ---
    top_n_brokers: int = 15              # 主力家數（集中度 / 主力成本）
    conc_short_days: int = 5             # 短期集中度窗口
    conc_long_days: int = 15             # 長期集中度窗口
    cost_window_days: int = 20           # 主力成本累計窗口（top 淨買分點買均價加權）
    exclude_daytrade_brokers: bool = True  # 排除隔日沖分點干擾

    # --- 法人連續買賣超 ---
    insti_streak_min: int = 3            # 連續淨買達此天數視為轉強

    # --- 大戶 / 散戶結構（集保，週頻）---
    holder_trend_weeks: int = 4          # 大戶比趨勢回看週數

    # --- 技術面 ---
    ma_mid: int = 20                     # 中期均線
    ma_long: int = 60                    # 長期均線
    breakout_lookback: int = 20          # 突破整理區：近 N 日高點
    vol_ma_days: int = 20                # 量能均值窗口
    vol_increase_ratio: float = 1.3      # 量增：當日量 ≥ 均量 × 此

    # --- chip_score 分量權重（合計會正規化）---
    weights: dict = field(default_factory=lambda: {
        "concentration": 1.0,    # 主力集中度趨勢
        "broker_net": 1.0,       # 分點淨買（排隔日沖）
        "cost_vs_price": 1.0,    # 主力成本相對價
        "holder": 0.8,           # 大戶/散戶結構
        "insti": 1.0,            # 法人連續買賣超
        "technical": 1.2,        # 技術面確認
    })

    # --- 訊號門檻 ---
    buy_threshold: float = 65.0          # chip_score 達此 + 技術轉強 → 買進候選
    exit_threshold: float = 45.0         # chip_score 跌破此 → 轉弱出場
    exit_below_cost: bool = True         # 跌破主力成本 → 出場
    exit_on_trend_reversal: bool = True  # 月線(MA20)轉下 → 出場

    # --- 回測持有週期（交易日）---
    holding_periods: tuple = (5, 10, 20, 40, 60)

    # --- 版本 ---
    algorithm_version: str = "chip-0.1"
    parameter_version: str = "chip-p1"
