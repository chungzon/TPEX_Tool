# MEDE — Tick 市場發動偵測引擎 · Phase 1 現況盤點與整合計畫

> Market Event Detection Engine（MEDE）。本文件只做「現況盤點 + 整合計畫」，**未修改任何執行程式**。
> 定位：**事件偵測**（Event Detection），非「每筆 Tick 預測」，第一版**不接正式下單**。

---

## A. 現況盤點（回答指定問題）

### 1. 語言 / 框架 / 啟動 / 目錄
- **Python 3.10+**，桌面 GUI（`customtkinter`）。**不是 web 專案**，沒有前後端、沒有 SSE/WebSocket/Redis/FastAPI。
- 架構 = 手刻 **MVVM**：`viewmodels/BaseViewModel` + `ObservableProperty`（`__set__` 觸發 callback）；長工作用 `threading.Thread(daemon=True)`；View 用 `self.after(0, ...)` 回主緒。
- 啟動：`python main.py` → `views/main_window.py`（10 個分頁的 `CTkTabview`）。
- 目錄：`services/`（I/O 與純計算）、`viewmodels/`、`views/`、`models/`（幾乎空）。

### 2. Shioaji 登入 / 憑證 / 訂閱 / 斷線重連 / callback（`services/shioaji_service.py`）
- 登入 `login(api_key, secret_key, person_id, ca_passwd, simulation)`；CA 憑證僅下單需要，行情/歷史不需。
- **即時訂閱**：`subscribe_quote(code, on_tick, on_bidask)` → 同時 `quote.subscribe(Tick)` 與 `quote.subscribe(BidAsk)`（皆 v1）。✅ 已同時訂 Tick+BidAsk。
- callback：`_ensure_quote_callbacks()` 只註冊一次 `set_on_tick_stk_v1_callback(_handle_tick)` / `set_on_bidask_stk_v1_callback(_handle_bidask)`；`_handle_*` 把 Shioaji 物件正規化成 dict 後轉呼叫 **單一** `_on_tick_cb` / `_on_bidask_cb`。
- ⚠️ **關鍵限制**：`_on_tick_cb`/`_on_bidask_cb` 是**單一 callback 欄位**，`subscribe_quote` 一呼叫就覆蓋 → **目前只能有一個消費者、一檔股票**。多檔／多消費者（MEDE 與大單追蹤並存）**需先加分派器（dispatcher）**。
- ⚠️ **沒有串流斷線重連**：`shioaji_service` 無 reconnect；也沒有 `received_at_ns` 時戳。
- `simtrade` / `intraday_odd` 在 `_handle_*` 直接丟棄。

### 3. 股票池 / 訂閱 / 即時行情 / 背景 worker
- **沒有正式股票池管理器**。即時只在「大單追蹤」分頁單檔追蹤；`_subscribed:set` 記已訂閱代碼。
- 併發：一律 `threading.Thread(daemon=True)` + `ObservableProperty`；**無 queue/worker 框架、無 Redis**。
- 微觀結構 VM 已有 ~0.4s 節流刷新緒（`REFRESH_INTERVAL`），把 snapshot 推 UI（本 session 才加了 `_tick_history` 記憶體暫存供畫圖，未落地）。

### 4. 資料庫 / 行情表
- **MSSQL（`pymssql`）**，`127.0.0.1:1433`、db `TSE`，帳密**硬編碼** in `DbService.__init__`。
- 表：`StockDailySummary`、`BrokerDailyStats`、`InstiDailyTrade`、`StockHolderDistribution`、`MarginDailyTrade`。
- ❌ **沒有任何 Tick / Kbar / BidAsk 表**；無逐筆資料層。

### 5. Tick 欄位 / 時間精度 / 方向 / 交易日
- 即時 dict：`code, time(datetime字串), close, avg_price, high, low, volume, total_volume, tick_type(1外盤/2內盤/0未知), simtrade, intraday_odd`。
- 歷史 `get_historical_ticks()`：另有 `ts`（**奈秒 epoch，台北牆鐘當 UTC 編碼**）與 level-1 `bid_price/ask_price/bid_volume/ask_volume`。
- 方向 = `tick_type`（**magic number，無 enum**）。交易日 = 由 `time`/`ts` 推。

### 6. 五檔 BidAsk 是否保存
- **即時有**（`subscribe` BidAsk → `_handle_bidask` 給 5 檔陣列），但**完全沒有落地保存**。
- **歷史沒有**：`api.ticks()` 只夾帶「成交當下的 level-1」買賣價量，**沒有獨立 BidAsk 事件流、沒有五檔歷史**。→ 完全印證前提：**五檔消失 / 補單 / Queue Collapse / MLOFI 事件必須從現在起自行錄製，事後無法還原**。

### 7. 回測是否支援 Tick 級順序
- ✅ `services/microstructure_backtest.py::MicrostructureBacktester` 依序重放歷史 Tick，先 `engine.on_bidask`（用 level-1）再 `engine.on_tick`，同一引擎產生訊號。但**只有成交 Tick，無真實五檔歷史**。

### 8. 台股 Tick Size
- ✅ `services/microstructure_backtest.py::tw_tick_size(price)`（<10→0.01…≥1000→5）。可重用。

### 9. 策略參數 / 功能頁 / API / 資料模型 / 訊號紀錄
- 參數：`config.json` ← `ConfigService.get/set`；以 dataclass 表示（`MicroConfig`、`BacktestParams`），`config_hash` **尚無**。
- 功能頁：10 分頁（見 `main_window.py`）。無對外 API（桌面）。
- 訊號紀錄：微觀結構 VM 內記憶體 `alert_log` / `_alert_records` / `_point_history`；**無 DB 訊號表**。

### 10. 「連次量」等可重用序列分析（`services/microstructure_service.py`）
- ✅ `TickVolumeBucket`（VPIN 量桶）：`consec_buy_buckets/consec_sell_buckets`（連續量桶）、動能點火。
- ✅ `LargeOrderMonitor`：大單、`attack_streak/down_streak`（連續大單跳檔＝起漲/起跌）、疑似補單/冰山。
- ✅ `OrderBookTracker`：五檔 **OBI（靜態帳面失衡 obi5）** + 蓄勢。**注意：這是 OBI，非 OFI**（未用前後兩筆 BidAsk 變化）。

### 11. 每日單股上限 / 冷卻 / 訊號提供者 / 強制重跑
- `SchedulerService`（每日 Timer、`last_run_time`）；`BatchDownloadViewModel` 跳過已存在（等同強制重跑用 MERGE 覆蓋）。
- 策略側有 `sar_exit_consecutive`、`sar_skip_open_minutes` 等；**無正式「每股每日事件上限 / 事件冷卻 / 合併窗」機制**。

### 12. 適合大量 Tick 重播的資料層
- ❌ **沒有** DuckDB / Parquet / SQLite / PostgreSQL；只有 MSSQL（網路連線、逐列 insert，**不適合逐筆高頻寫入/重播**）。
- 有 `numpy` / `scipy`（已用、未列 requirements）。**需新增本地逐筆資料層**。

---

## B. 現況總結（對 MEDE 的意義）
1. 既有 `MicrostructureEngine` 是**雛形 MEDE**：已有 OBI/VPIN/大單/起漲跌/冰山 + 同引擎回測。可**重用其基礎元件**（`tw_tick_size`、tick 分方向、五檔維護、量桶連續、回測重放框架）。
2. 但**達不到本規格的嚴謹度**：OBI≠OFI、Queue Collapse 未分「成交/撤單/移價」、無狀態機、無 MFE/MAE/First-Touch、無事件落地、無 config_hash、無資料品質、無多股併行、無斷線重連、無 BidAsk 錄製。
3. **最關鍵三缺口**（決定先後）：① Shioaji 單一 callback → 需多股分派器；② 無 BidAsk 錄製與逐筆資料層；③ 無串流重連 + `received_at_ns`。→ 這正是 spec 的 **Phase 2 最優先**。

---

## C. 建議架構（增量整合，不另開專案）
在既有 app 內新增自成一體的 `services/mede/` 套件與一個新分頁，**重用**既有 Shioaji 連線、`ConfigService`、`tw_tick_size`。

```
services/mede/
  enums.py          # TradeSide / EventType / DetectorName / StateType（取代 magic number）
  config.py         # MedeConfig(dataclass) + config_hash()；集中所有門檻
  quote_hub.py      # 訂閱分派器：多股、多消費者，加 received_at_ns、seq
  raw_recorder.py   # per-stock ring buffer → 批次寫入 storage（非阻塞）
  storage.py        # 逐筆資料層抽象（SQLite 或 Parquet/DuckDB，可抽換）
  feature_engine.py # FeatureSnapshot（deque/ring buffer 增量統計；OFI/MLOFI 真實計算）
  detectors/        # 每個 Detector 一檔，輸入 FeatureSnapshot → DetectorResult
  fusion.py         # EventFusionEngine（Weighted + Rule Pattern）
  state_machine.py  # 每股獨立 IDLE→WATCH→TRIGGERED→CONTINUATION→EXHAUSTION/FAILED→COOLDOWN
  patterns.py       # Pattern Library（可設定/停用/回測）
  outcome.py        # First-Touch / MFE / MAE / 固定時間報酬 / 成交模擬（滑價+費稅）
  replay.py         # TickReplayEngine：用同一組 feature/detector/fusion/state 重播
  stats.py          # 勝率/期望值/PF/分組統計
viewmodels/mede_viewmodel.py
views/mede_view.py        # 新分頁「Tick 發動偵測」（即時 + 回測子頁）
```

- **重用既有連線**：`main_window` 已把 `trading_vm._sj` 共用給 micro/settings。MEDE 也接同一 `ShioajiService`，但**必經 `quote_hub` 分派**（解決單 callback）。
- **UI 推送**：沿用 MVVM——每股 engine 累積狀態、刷新緒推 **snapshot**（非每筆 Tick）給 UI，對應 spec「只傳摘要」。
- **兩層時間**：交易所 `exchange_timestamp`（Shioaji datetime）+ 系統 `received_at_ns`；重播依 `(exchange_ts, seq)` 排序。

---

## D. 預計新增 / 修改檔案
**新增**：上列 `services/mede/*`、`viewmodels/mede_viewmodel.py`、`views/mede_view.py`，以及 `docs/MEDE/*.md`（ARCHITECTURE、SHIOAJI_STREAMING、DETECTOR_DEFINITIONS…）。
**修改（最小）**：
- `services/shioaji_service.py`：加 **quote dispatcher**（依 code 分派、支援多消費者）、`received_at_ns`、**串流重連**。（保留現有 `subscribe_quote` 相容，內部改走 hub。）
- `views/main_window.py`：新增分頁 + `_on_close()` 呼叫 `mede_vm.shutdown()`。
- `requirements.txt`：補列實際相依（`numpy/scipy/shioaji`…）+ 選定的資料層（若用 DuckDB/pyarrow）。

## E. 預計新增資料表 / 資料層
**不使用 MSSQL 存逐筆**（網路+逐列 insert 太慢）。獨立本地逐筆層（`storage.py` 抽象，先 **SQLite 每日檔**起步，量大再換 **Parquet + DuckDB**）：
- `raw_tick`、`raw_bidask`（含 `exchange_ts, received_at_ns, seq`）
- `mede_event`（event_id, stock, event_time, event_type, direction, score, confidence, trigger_price, detector_scores(json), reasons, parameter_version, algorithm_version, raw_data_start/end, config_hash）
- `mede_outcome`（max_favorable/adverse_ticks, time_to_mfe/mae, first_touch_*, return_after_{1,3,5,10,15,30,60}s, exit_price, gross/net_return, outcome_label）
- `mede_state_transition`、`mede_data_quality`、`mede_backtest_run`
- 既有 MSSQL 保留給日/分點資料，**互不干擾**。

## F. 整合位置
- Shioaji：`ShioajiService`（共用連線）＋新 `quote_hub`。
- 參數：`ConfigService`（`config.json` 增 `mede` 區塊）。
- Tick Size / 分方向 / 五檔維護 / 量桶連續：重用 `microstructure_service` 與 `microstructure_backtest` 的原子元件（以組合方式，不改其行為，避免動到「大單追蹤」分頁）。
- UI：新分頁（第 11 頁），圖表可重用本 session 建的 matplotlib 基礎。

## G. 實作階段（對應 spec Phase 2–8；每階段完成回報）
- **P2 原始資料蒐集**：quote_hub 多股分派 + `received_at_ns` + ring buffer + raw_recorder 批次寫 + 斷線重連 + 資料品質監控。**（先把 Tick+BidAsk 錄起來最重要）**
- **P3 Replay + Feature**：同一份資料在「即時模擬」與「Replay」產生**相同 FeatureSnapshot**（含真實 OFI/MLOFI）。
- **P4 Detectors**：逐一實作 + 單元測試（先不做 Fusion）。BidAsk 依賴型 detector 在 Tick-only 資料**自動停用**並標示。
- **P5 Fusion + State Machine**：候選/觸發/延續/衰竭/失敗/冷卻 + 否決條件 + 合併窗。
- **P6 Outcome + 統計**：First-Touch / MFE / MAE / 固定時間報酬 / 成交模擬（滑價+費稅）/ 勝率/期望值/PF/分組。
- **P7 UI 兩頁**：即時追蹤頁 + 回測頁（重用現有前端框架）。
- **P8 Meta Model**：前七階段有足夠樣本、baseline 過**樣本外**驗證後，才做 LightGBM（只對候選事件訓練）。

## H. 測試方式
- 單元：Tick Size、tick_type↔enum、Trade/Book Imbalance、**OFI/MLOFI**、各 Detector、Fusion/Veto、狀態機轉移、Cooldown/Merge、MFE/MAE/First-Touch、成本、跨日重置、亂序/重複、Tick-only 自動停用。
- 整合：以**錄製的 Tick+BidAsk fixture**，驗「即時模擬＝Replay＝重跑」三者結果一致（決定性）；模擬斷線/queue 滿/DB 暫停寫/只有 Tick/跨日/同波多觸發/停利停損同 Tick。
- **沒有實際執行不宣稱通過；沒有真實資料不宣稱已驗證勝率；缺 BidAsk 一律標 Tick-only。**

## I. 已知限制（先講清楚）
1. **歷史 BidAsk 不存在** → 錄製起始日**之前**只能 Tick-only 回測，五檔類 detector 停用；完整微結構事件僅能從錄製日起前瞻驗證。
2. Shioaji **單 callback 需先重構**才能多股/多消費者並存（否則會與「大單追蹤」互搶）。
3. Shioaji **訂閱數與盤中查詢有官方上限** → 重用訂閱、限制追蹤檔數、盤中不得用 `api.ticks()` 輪詢。
4. 桌面 app 無真 async；用 thread+queue；UI 只吃 snapshot。
5. `pymssql` 網路 DB 不適合逐筆 → 另建本地層。
6. 現有 `OrderBookTracker` 的是 **OBI（靜態）非 OFI**；MEDE 需另做真實 OFI/MLOFI（前後 BidAsk 變化），**不可沿用 OBI 冒充**。

## J. 關鍵決策（已拍板，2026-07-15）
1. **逐筆資料層**：✅ **SQLite 起步 + `storage.py` 抽象層**（stdlib、零依賴、最快開始錄製；每交易日一檔；量大再換 Parquet+DuckDB，介面不變）。
2. **引擎策略**：✅ **MEDE 全新嚴謹引擎**——重用 `tw_tick_size`/tick 分方向/五檔維護等**原子元件**，但 Detector / 真實 OFI・MLOFI / 狀態機 / Outcome 全部新做；**既有「大單追蹤」分頁完全不動**。
3. **Shioaji 重構**：✅ 在 `shioaji_service` 加**多股 quote dispatcher + 串流重連 + `received_at_ns`**，保持現有 `subscribe_quote` 相容（大單追蹤不受影響）。
4. **追蹤檔數上限**：✅ 第一版 **1–5 檔**起步（符合 Shioaji 訂閱限制、確保 per-stock 併行不互阻）。

> Phase 2 起點：`quote_hub`（多股分派）+ `raw_recorder`（ring buffer→批次寫）+ `storage`（SQLite）+ 斷線重連 + 資料品質監控 —— **先把 Tick+BidAsk 穩定錄下來**。
