"""MEDE — Tick 市場發動偵測引擎 (Market Event Detection Engine).

Phase 2：原始資料蒐集（Tick + 五檔 BidAsk 錄製、批次落地、資料品質、斷線重連）。
後續階段：Feature Engine / Detectors / Fusion / State Machine / Outcome / Replay。

設計決策（見 docs/MEDE/PHASE1_INVENTORY_AND_PLAN.md）：
- 逐筆資料層：SQLite 起步（storage.Storage 抽象，可換 Parquet+DuckDB）。
- 全新嚴謹引擎，重用既有 tw_tick_size / 分方向等原子元件；大單追蹤分頁不動。
- Shioaji 已改為「依代碼分派 + received_at_ns + seq」，MEDE 與大單追蹤可並存。
"""
