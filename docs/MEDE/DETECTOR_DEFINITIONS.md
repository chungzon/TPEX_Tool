# MEDE Detector Definitions（Phase 4，14/14）

每個偵測器：獨立 class、吃統一 `FeatureSnapshot`、輸出 `DetectorResult`
(direction ±1/0、score 0-100、confidence 0-1、is_triggered、reasons、raw_metrics、parameter_version)。
可設定/啟停(`detector_enabled`)、可單獨回測、附單元測試、**不直接下單**。
BidAsk 相依者在 Tick-only 自動 idle。z-score 一律「先算(對過去)再納入基準」避免自我膨脹。

| # | 偵測器 | 目的 / 方向 | 觸發條件（核心） | 需 BidAsk |
|---|---|---|---|---|
| 1 | **trade_burst** | 成交筆數暴增 | 短窗成交筆數 z ≥ `trade_burst_zscore` | 否 |
| 2 | **volume_burst** | 成交量暴增 + 方向集中 | 短窗量 z ≥ `volume_burst_zscore` | 否 |
| 3 | **aggressive_flow** | 主動買/賣連續性 | 多時間窗 imbalance 同向且 `|imb|≥flow_imbalance_threshold` | 否 |
| 4 | **book_imbalance_shift** | 五檔由平衡轉偏 | `|L5 imbalance| ≥ book_imbalance_threshold`（+變化速度） | 是 |
| 5 | **ofi_shock** | OFI/MLOFI 突偏單向 | OFI z ≥ `ofi_shock_zscore` 且 MLOFI 各檔一致 ≥60%（**真實 OFI，非買五減賣五**） | 是 |
| 6 | **breakout** | 突破近 N 筆高低 | 現價越過前高/低 + 主動流同向確認（+VWAP） | 否 |
| 7 | **sweep** | 短時間跨多價位掃單 | 速度窗價格區間 ≥ `sweep_min_ticks` + 主動流同向 | 否 |
| 8 | **absorption** | 大量主動成交未推動價 | 窗內主動量 ≥ `absorption_min_volume` 且價動 ≤ `absorption_max_price_ticks`；賣方吸收→-1、買方吸收→+1 | 否(補量加分) |
| 9 | **queue_collapse** | 最佳掛量崩塌 | (消耗+撤單)/前量 ≥ `queue_collapse_ratio` 或最佳價移位；**區分成交/撤單/移價** | 是 |
| 10 | **liquidity_vacuum** | 單側流動性消失 | 點差 ≥ 基準×`spread_expansion_ratio` 或缺口≥2 且該側明顯薄 | 是 |
| 11 | **replenishment** | 同價反覆補單 | 累積消耗/顯示 ≥ `replenishment_ratio`；標「**疑似補單/隱藏流動性**」非 Iceberg | 是 |
| 12 | **failed_breakout** | 假突破 | 突破後 `failed_breakout_timeout_ms` 內反向跌回突破價 + 反向流/對側吸收；訊號方向與原突破相反 | 否(對側補量加分) |
| 13 | **exhaustion** | 動能由強轉弱 | 曾偵測強動能(rate z 高)後，rate 與 OFI 皆 < 峰值×`exhaustion_fade_ratio`、價速轉弱、價守極值；方向相反 | 否 |
| 14 | **momentum_ignition** | 動能同步加速點火 | **≥`momentum_min_categories` 類**證據同向共振(burst/flow/OFI/queue collapse/加速…)且無否決(spread異常)；**不得單一條件** | 部分(OFI/queue 需五檔) |

## 否決 / 限制
- BidAsk 相依偵測器在 **Tick-only** 資料自動停用(idle)並標示;不得補造五檔。
- z-score 型偵測器需暖身 `minimum_warmup_ticks` + `baseline_window` 樣本才觸發。
- momentum_ignition 為候選共振，非最終狀態；最終狀態(TRIGGERED/CONTINUATION…)由 Phase 5 Fusion + State Machine 決定。
- 目前以**合成資料**驗證觸發正確性;真實勝率須待實際錄製 + Phase 6 outcome 統計。

## 測試
單元測試(scratchpad test_mede_p4{,b,c}.py)：每個偵測器「平靜期不觸發、對應情境觸發且方向正確」，並驗證註冊表啟停與 Tick-only idle。
