# MEDE — Tick 市場發動偵測引擎 · 操作手冊（USAGE）

> 對象：本工具的使用者。說明「怎麼錄、怎麼偵測、怎麼看結果、怎麼調參」。
> 定位：**事件偵測**（Event Detection），只產生候選事件供人工檢視，**不下單、不發委託**。
> 分頁位置：主視窗 →「**Tick 發動偵測**」分頁（`views/mede_view.py`）。

整個流程分兩段，界線很清楚：

| 階段 | 何時做 | 需要連線？ | 產出 |
|---|---|---|---|
| **① 錄製** | **盤中**（09:00–13:30） | ✅ 需登入永豐正式環境 | `mede_data/mede_YYYYMMDD.sqlite`（raw_tick / raw_bidask） |
| **② 偵測** | 盤後、隔天、任何時候 | ❌ 離線即可 | `mede_event` / `mede_state_transition` 表 + 分頁事件表格 |

---

## 0. 啟動前準備

```powershell
python main.py
```

- Python 3.10+、本機 MSSQL（`127.0.0.1:1433`，db `TSE`）、Chrome。
- `requirements.txt` 不完整，另需手動安裝：
  ```powershell
  pip install shioaji ddddocr scipy numpy requests
  ```
- 即時逐筆行情**必須用永豐「正式環境」帳號**（模擬環境沒有逐筆）。金鑰放在 `config.json`（gitignored）。

---

## 1. 連線永豐（錄製的前提）

MEDE 分頁**本身不做登入**，它與「下單 / 大單追蹤」**共用同一條 Shioaji 連線**。

1. 先到「下單 / 大單追蹤」分頁登入永豐**正式環境**，確認連線成功。
2. 再切回「**Tick 發動偵測**」分頁。

> 未登入時按「開始錄製」會顯示：`尚未連線永豐（即時行情需正式環境登入）`。

---

## 2. 盤中錄製原始逐筆資料

> ⚠️ **只能盤中錄，且五檔委託簿事後無法還原**——不從當下開始累積就永遠沒有。錯過的交易日**補不回來**（與分點明細相同的限制）。

分頁**上半部**操作：

1. **股票代碼**欄輸入標的，空白或逗號分隔，**最多 5 檔**（Shioaji 訂閱上限）。例：`1815 2330 2317`
2. 按 **開始錄製**（綠色鈕）。
3. 下方「即時錄製狀態」會逐秒刷新：
   - **狀態**（錄製中／停止）、**標的**、**交易日**、**盤中**（是／否）
   - **佇列**、**丟棄**（callback→writer 佇列滿的丟棄數，正常應為 0）
   - **Tick 延遲 / 五檔延遲**、**Writer**（寫入緒是否存活）
   - **每檔資料品質**表：Tick 數、五檔數、未知方向、亂序、最大間隔、品質標記
4. 收盤或要停時按 **停止**——會 flush 緩衝、保存資料品質、關檔。

**資料品質標記**：
- `ok`（綠）：正常。
- `degraded`（橘）：有缺口／延遲／亂序，或未知方向比例偏高（> 30%），但仍可用。
- `invalid`（紅）：當日 Tick 數過少（< 200），不建議納入正式分析。

**落地位置**：`mede_data/mede_YYYYMMDD.sqlite`，每交易日一個檔（WAL 模式，可邊寫邊讀）。
盤中若資料停滯，看門狗（每 5s 檢查）會自動重新訂閱。

---

## 3. 事後跑偵測（離線，不需連線）

分頁**下半部「🎯 發動偵測結果」**面板。這段對**已錄製的檔案**重播偵測，隨時可做。

1. **交易日**下拉：自動列出 `mede_data/` 內所有已錄製的日子（新→舊）。
   按 **⟳ 重整** 重新掃描（例如剛錄完一天）。
2. 選日期後，**代碼**下拉自動帶出當天有錄到 Tick 的股票。
3. 選一檔 → 按 **跑偵測**。
4. 系統以**與即時完全相同**的引擎重播那天逐筆（依 `seq`，決定性），跑
   `FeatureEngine → 14 偵測器 → 融合 → 狀態機`，把**候選事件**列在表格。

事件表格欄位：

| 欄位 | 意義 |
|---|---|
| **時間** | 事件的交易所時間（HH:MM:SS.mmm） |
| **事件** | `BULL_TRIGGER`/`BEAR_TRIGGER`（多／空發動）、`EXHAUSTION_*`（衰竭）、`FAILED_BREAKOUT_*`（假突破）… |
| **方向** | ▲多（綠）／▼空（紅） |
| **分數** | 融合加權分（多／空證據加權總分） |
| **信心** | 0–1 |
| **觸發價** | 事件當下成交價 |
| **型態** | 命中的規則型態（如 `A_bull_momentum_start`） |
| **主要理由** | 白話說明（哪幾類證據共振） |

事件同時寫入該日 SQLite 的 `mede_event` 表；重跑同日同股會**覆蓋**（冪等，以 `event_id` 為主鍵）。狀態轉移記於 `mede_state_transition`。

---

## 4. 調參（校準門檻）

所有門檻集中在 `services/mede/config.py` 的 `MedeConfig`（**不散落程式碼**）。常用旋鈕：

| 參數 | 作用 | 預設 |
|---|---|---|
| `trade_burst_zscore` / `volume_burst_zscore` | 爆量／爆筆敏感度（z-score） | 3.0 |
| `flow_imbalance_threshold` | 主動流失衡門檻 | 0.55 |
| `bull_trigger_score` / `bear_trigger_score` | 觸發所需加權總分 | 120 |
| `trigger_min_detector_categories` | 觸發需幾類不同證據共振 | 3 |
| `absorption_min_volume` / `absorption_min_trades` | 吸收：主動量／成交筆數門檻 | 40 / 5 |
| `cooldown_ms` / `merge_window_ms` | 冷卻期／同波同向合併窗（避免一波洗出一堆事件） | 5000 / 3000 |
| `spread_max_ticks` | 點差過大則否決（必要條件） | 4.0 |

改完存檔 → 回偵測面板重跑即可生效。每次偵測都會記 `config_hash`，可追溯是哪組參數產生的結果。

---

## 5. 重要限制與心智模型

- **錄製＝線上、偵測＝離線**：偵測不需要 Shioaji，可反覆對歷史檔重跑、比較不同參數。
- **只出候選事件，不下單**：這是研究／檢視工具。
- **決定性**：同一份資料 + 同一組參數 → 每次結果完全一致（依 `seq` 重播，不看牆鐘）。
- **五檔無法補**：只有 Tick 沒有五檔時，依賴委託簿的偵測（OFI、吸收、掛單崩塌等）會自動略過或退回純 Tick 邏輯。
- **門檻尚未用真實資料校準**：目前預設值僅以合成資料驗證「邏輯正確」，**合成資料不能用來定門檻**。
  正式使用前，建議挑 1–3 檔活潑股票錄一整天真實資料，盤後在偵測面板反覆重跑、對照當天走勢，再回頭調 `MedeConfig`。

---

## 一句話流程

> **盤中**：登入永豐 → MEDE 分頁輸代碼 → 開始錄製 → 收盤停止。
> **盤後**：同分頁下方選日期／代碼 → 跑偵測 → 看事件表 → 不準就改 `config.py` 門檻再重跑。

---

## 附：程式進入點（給要接手的人）

- 錄製：`services/mede/recorder_service.py`（`RecorderService.start/stop`）+ `raw_recorder.py` + `storage.py`。
- 偵測呼叫端：`services/mede/detection_service.py`
  （`DetectionService.list_dates / list_recorded / run / run_all / read_events`）。
- 引擎：`engine.py`（`MedeEngine`）→ `feature_engine.py` → `detectors/` → `fusion.py` → `state_machine.py`。
- 重播：`replay.py`（`TickReplayEngine.replay_detect`，與即時共用引擎）。
- UI：`views/mede_view.py` + `viewmodels/mede_viewmodel.py`。
- 落地表：`mede_event`、`mede_state_transition`（DDL 在 `storage.py`）。
