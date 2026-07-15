# MEDE Raw Data Schema & Streaming（Phase 2）

## Shioaji 串流（`services/shioaji_service.py`）
- 同時訂閱 **Tick + BidAsk（v1）**：`subscribe_quote(code, on_tick, on_bidask)`。
- callback **依股票代碼分派**（`_tick_listeners` / `_bidask_listeners` dict），支援多檔、多消費者 → MEDE 與「大單追蹤」可並存、互不干擾（同代碼則後者覆蓋）。
- 每筆事件加：`received_at_ns`（系統收到時間, `time.time_ns()`）、`seq`（全域遞增序，供重播排序）。
- `simtrade` / `intraday_odd` 直接丟棄。
- callback 僅做輕量正規化與分派；**錄製消費者只入列、不碰 DB**（見 raw_recorder）。

## 錄製管線
`Shioaji socket 緒` → `RawRecorder.on_tick/on_bidask`（`put_nowait` 入列，佇列滿→`dropped_count++`）
→ 單一 **writer 緒** 批次（`write_batch_size` / `write_flush_interval_s`）落地 `Storage`
→ 同步更新 ring buffer（供後續 Feature Engine）與資料品質計數。跨日自動換檔並保存前一日品質。停止時安全 flush。

## 逐筆資料層（`services/mede/storage.py`，SQLite，每交易日一檔 `mede_data/mede_YYYYMMDD.sqlite`）
### `raw_tick`
| 欄位 | 型別 | 說明 |
|---|---|---|
| code | TEXT | 股票代碼 |
| exchange_time | TEXT | 交易所時間（Shioaji datetime 字串） |
| received_at_ns | INTEGER | 系統收到時間（ns） |
| seq | INTEGER | 全域遞增序（重播排序用） |
| close | REAL | 成交價 |
| volume | REAL | 單筆量（張） |
| total_volume | REAL | 當日累積量 |
| avg_price | REAL | 均價 |
| tick_type | INTEGER | 1外盤/2內盤/0未知（→ `TradeSide`） |

### `raw_bidask`（五檔，`bid/ask_price/volume` 以 JSON 陣列存）
`code, exchange_time, received_at_ns, seq, bid_price, bid_volume, ask_price, ask_volume`

### `mede_data_quality`（每日每股一列）
`trade_date, code, tick_count, bidask_count, unknown_dir_count, dropped_count, out_of_order_count, reconnect_count, max_gap_ms, first_tick_time, last_tick_time, status(ok/degraded/invalid), config_hash, saved_at`

> 資料品質：`tick_count < min_ticks_valid_day` → **invalid**；`unknown 比例 > 門檻` 或 `out_of_order>0` → **degraded**。invalid/degraded 之日後續不納入正式回測統計。

## 設定（`services/mede/config.py::MedeConfig`；存於 `config.json["mede"]`）
`tracked_symbols, max_symbols(5), storage_dir, write_batch_size, write_flush_interval_s, queue_maxsize, ring_buffer_*, stale_seconds, watchdog_interval_s, market_open/close, max_unknown_dir_ratio, min_ticks_valid_day, algorithm_version, parameter_version` — 每次以 `config_hash()` 記錄。

## 斷線重連（`recorder_service.py` 看門狗）
盤中每 `watchdog_interval_s` 檢查 `tick_lag_ms`；停滯超過 `stale_seconds` 且已登入 → 重新 `subscribe_quote` 並 `note_reconnect`。（第一版為 watchdog 式；session-event 式重連待強化。）
