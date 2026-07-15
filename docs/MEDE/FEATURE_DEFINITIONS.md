# MEDE Feature Definitions（Phase 3）

`services/mede/feature_engine.py`：由 Tick/BidAsk 事件**增量**計算，供即時與重播共用。
決定性：視窗以 **exchange time** 為準（非牆鐘）；重播依 **seq**（＝接收/internal_sequence）順序，
故「即時模擬 == 重播」（已驗證 500 筆快照完全一致）。

## 視窗
- 時間窗（預設 1s / 3s / 5s；可設）：deque + running sum 增量淘汰，不掃全歷史。
- 事件窗（預設 20t / 50t）：定長 deque + running sum。

## A. 成交方向特徵（每時間窗 / 事件窗）
`buy_vol, sell_vol, unk_vol, buy_cnt, sell_cnt`、
`trade_imbalance = (buy_vol−sell_vol)/(buy_vol+sell_vol)`、
`count_imbalance = (buy_cnt−sell_cnt)/(buy_cnt+sell_cnt)`、
`avg_buy_size, avg_sell_size, largest_buy, largest_sell`。
全域：`consec_buy / consec_sell`（連續主動方向）。方向由 `TradeSide.from_tick_type`（1外盤/2內盤/0未知）。

## B. 委託簿 / OFI 特徵（每筆 BidAsk 更新）
- `best_bid, best_ask, spread_ticks`、`bid_qty1, ask_qty1`
- `l1_imbalance = (bq1−aq1)/(bq1+aq1)`、`l5_imbalance = (Σbq−Σaq)/(Σbq+Σaq)`（**靜態帳面失衡，非 OFI**）
- `weighted_bid_depth, weighted_ask_depth`（近檔權重 1..0.2）
- `bid_liquidity_gap, ask_liquidity_gap`（相鄰檔位價差，以 tick 計）
- **OFI / MLOFI（真實 Order Flow Imbalance）**——依 Cont–Kukanov–Stoikov，用**前後兩筆 BidAsk** 的最佳價與掛量變化：
  ```
  單檔 l：ΔW = (Pb>Pb0? qb : Pb==Pb0? qb−qb0 : −qb0)
         ΔV = (Pa<Pa0? qa : Pa==Pa0? qa−qa0 : −qa0)
         OFI_l = ΔW − ΔV
  ```
  `ofi_l1 = OFI_1`、`mlofi = [OFI_1..OFI_5]`、`integrated_mlofi = Σ`、各時間窗另存 `ofi_sum`（窗內 OFI 累積）。
  **嚴禁**用「買五總量 − 賣五總量」冒充 OFI。

## Tick-only（無 BidAsk 歷史）
`bidask_available=False`，book/OFI 維持預設 0；後階段 BidAsk 相依偵測器自動停用並標示。

## 重播（`services/mede/replay.py`）
`TickReplayEngine.replay(code, date, speed)`：`fastest / realtime / Nx`；讀 storage → 依 seq 餵同一 FeatureEngine → 每筆成交產出 FeatureSnapshot；`stop_flag` 可中止。
