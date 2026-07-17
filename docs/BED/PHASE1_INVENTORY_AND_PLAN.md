# BED 空方發動偵測 — Phase 1 現況盤點與整合計畫

> Bearish Event Detection（BED）。本文件**只做盤點與整合方案，未修改任何執行程式**。
> 一句話結論：**BED 不需另開架構。它是既有 MEDE 引擎的「空方特化 + 缺口補強」**——
> MEDE 的偵測器本來就是方向感知（direction ±1），狀態機已含 BEAR_TRIGGER/
> BEAR_CONTINUATION/EXHAUSTION_DOWN/FAILED_BREAKOUT_DOWN。要補的是 VWAP/結構類
> 偵測器、Outcome/回測引擎、以及回測與觀察 UI。

---

## 0. 三個必須先對齊的前提（規劃書 vs 現實）

規劃書預設了一個 **Web 前端**技術棧，但本專案是 **Python 桌面 App**。這會改寫 UI 章節：

| 規劃書假設 | 專案現實 | 因應 |
|---|---|---|
| lightweight-charts（JS 圖表庫） | **matplotlib**（`FigureCanvasTkAgg` 嵌入 tkinter，多子圖 sharex，見 `microstructure_view.py:820+`） | 沿用 matplotlib 多面板同步圖，不引入 JS 圖庫 |
| SSE / WebSocket / API 即時推送 | **無 Web 層**。手刻 MVVM：`ObservableProperty.__set__` 在工作緒觸發 → View `self.after(0,…)` 回主緒 | 即時更新走既有 Observable 模式，不建 SSE/WS |
| 前端框架、元件庫 | **customtkinter** + `ttk.Treeview`（clam 主題） | 沿用；表格用 Treeview |

第二個必須對齊的重點：**專案已有「兩套」微結構引擎**，別再造第三套——

| 引擎 | 位置 | 內容 | 現用於 |
|---|---|---|---|
| **MicrostructureEngine**（舊） | `services/microstructure_service.py` | OBI / VPIN / 大單 / 起漲起跌點，**level-1** | 「大單追蹤」分頁 + `microstructure_backtest.py` 回測 |
| **MedeEngine**（新，本人近期完成） | `services/mede/` | 14 個方向感知偵測器 + Fusion + 狀態機 + 事件 + 重播，**五檔** | 「Tick 發動偵測」分頁 |

**BED 建在 MEDE 上**（五檔、方向感知、已有事件/狀態機/重播）；**回測的成本/績效/淨值機制向
`microstructure_backtest.py` 借用**（它已有滑價/手續費/稅/勝率/PF/MDD/期望值/防未來函數）。
兩引擎第一版**不合併**（風險高），但 BED 不重寫回測數學。

第三個重點：**歷史逐筆只有 level-1**。`shioaji_service.get_historical_ticks` 用 `api.ticks()`，
官方只給最佳一檔買賣價量（docstring 已註明）。因此：
- **即時錄製日**（`mede_data/mede_YYYYMMDD.sqlite`）→ 有完整五檔。
- **未錄製的過去日**（api.ticks 回補）→ 只有 L1 → 依賴五檔的偵測器**必須降級**。
- 規劃書要求的「Tick-only 降級、報告標示、不補造五檔」在此是**強制**、非選配。

---

## 1. 現況盤點（對應規劃書 §一 的 13 點）

1. **語言/框架/目錄**：Python 3.10+，customtkinter 桌面 MVVM，無前後端分離。
   `services/`（I/O + 純計算）、`viewmodels/`、`views/`、`docs/`。入口 `main.py` → `views/main_window.py`（11 分頁 CTkTabview）。
2. **Shioaji 登入/訂閱/重連**：`services/shioaji_service.py`。
   `login/is_logged_in`、`subscribe_quote(code,on_tick,on_bidask)`（同時訂 Tick+BidAsk v1）、
   `unsubscribe_quote/unsubscribe_all_quotes`、`get_historical_ticks(code,date)`。
   **分派**：`_tick_listeners[code]=cb` / `_bidask_listeners[code]=cb`（**每代碼一個 listener**）。
   多檔可並存（實測同時訂 1815+2374），但**同一代碼只能一個消費者**（大單追蹤與 BED 同時看同一檔會互相覆蓋）。
   斷線重連：MEDE `recorder_service` 有看門狗（停滯→重新訂閱）；shioaji_service 本身無串流自動重連。
3. **Tick/BidAsk/Kbar/基本資料表**：日頻在 MSSQL（`StockDailySummary`/`BrokerDailyStats`/`InstiDailyTrade`/
   `StockHolderDistribution`/`MarginDailyTrade`）。**逐筆不進 MSSQL**——存每日一檔 SQLite
   （`raw_tick`/`raw_bidask`/`mede_data_quality`/`mede_event`/`mede_state_transition`）。**無 Kbar 表**。
4. **股票池/訂閱管理/單股觀察**：`config.json` 存清單；「大單追蹤」= 單股即時觀察（OBI/VPIN/大單）；
   MEDE `RecorderService` = 多股（≤5）即時錄製。無獨立「訂閱管理器」類別，訂閱狀態散在各 VM。
5. **策略/訊號/回測/重播**：
   - `strategy_eval_service.py`（日頻突破回測 + 策略篩選）。
   - **`microstructure_backtest.py`（事件驅動 tick 回測，可直接參考/借用）**：滑價/手續費/稅/勝率/PF/MDD/期望值/淨值曲線/多空對稱/防未來函數。
   - MEDE `replay.py`（`TickReplayEngine.replay`/`replay_detect`，與即時共用引擎、依 seq 決定性）。
6. **API/SSE/WebSocket/前端即時**：**無**。即時更新一律 Observable + `after(0)`。
7. **lightweight-charts**：**無**。用 matplotlib（`microstructure_view` 已有 3 面板 sharex 大圖）。
8. **UI 分頁/元件/樣式/表格**：CTkTabview、customtkinter、`ttk.Treeview`（**統一 clam 主題**，剛修過全域主題衝突）。
9. **Tick Size**：**有，且重複**——`services/mede/tick_size.py:tw_tick_size` 與 `microstructure_backtest.py:41 tw_tick_size` 內容相同（可去重）。
10. **VWAP/均價/大盤**：`feature_engine` 已算 `vwap`、`recent_high/low`；**但無 swing high/low 結構、無 VWAP-rejection/Lower-High 邏輯**。**無任何大盤/指數資料來源**。
11. **交易成本/滑價/損益**：`microstructure_backtest._net_return`（手續費 0.001425×折3、稅 0.0015、滑價 tick）；`strategy_eval` 亦有成本。可直接沿用公式。
12. **DB 是否適合大量 Tick**：MSSQL 不存 tick；SQLite/日 + WAL（storage 抽象層可換 Parquet/DuckDB 而不動上層）。目前量級可行；跨多檔多日大回測再評估 DuckDB。
13. **連次量/成交方向/吸收/主動買賣**：**MEDE 已具備**——`TradeSide`(內外盤)、`aggressive_flow`、`absorption`（剛修誤觸）、`consec_buy/sell`、`trade_imbalance`、`ofi_shock`、`replenishment`、`queue_collapse` 全可重用。

---

## 2. MEDE 現有偵測器 → BED 規劃書對照（§六、§七）

**可直接重用（空方＝ direction −1）— 11 項已存在：**

| BED 規劃 | MEDE 現有偵測器 | 備註 |
|---|---|---|
| 7.1 TradeBurst | `trade_burst` | ✓ |
| 7.2 VolumeBurst | `volume_burst` | ✓ |
| 7.3 AggressiveSellFlow | `aggressive_flow`(dir−1) | ✓ |
| 7.4 BidQueueCollapse | `queue_collapse`(dir−1) | ✓ 已區分消耗/撤單/移價 |
| 7.5 AskReplenishment | `replenishment` | ✓ 命名為「疑似補量」 |
| 7.6 SellAbsorption | `absorption`(dir−1) | ✓ 剛修誤觸（守價+補量必要條件）|
| 7.7 OFIShock | `ofi_shock` | ✓ L1+MLOFI |
| 7.8 LiquidityVacuumDown | `liquidity_vacuum`(dir−1) | ✓ 依賴五檔 → 歷史 L1 降級 |
| 7.9 SellSweep | `sweep`(dir−1) | ✓ |
| 7.10 BreakoutFailure | `failed_breakout` | ✓ |
| 7.11 Exhaustion | `exhaustion` | ✓ |

**需新增（VWAP/結構類）— 6 項：**

| BED 規劃 | 需新增 | 依賴 |
|---|---|---|
| 6.1 拉高失敗 RallyFailure | 新偵測器 | 開盤價、VWAP、近高 |
| 6.2 跌破 VWAP VwapBreak | 新偵測器 | VWAP 斜率、跌破速度 |
| 6.3 反彈不過 VWAP VwapRejection | 新偵測器 | VWAP、反彈量能 |
| 6.4 Lower High | 新偵測器 | **需 feature_engine 新增 swing high/low（含確認延遲，防未來函數）** |
| 6.5 跌破微結構低點 StructureBreak | 新偵測器 | swing low |
| 6.6 下跌有效/反彈無效 DirectionalEfficiency | 新偵測器 | 分段量價效率 |

---

## 3. 建議整合位置 / 檔案清單

### 3.1 新增檔案
```
services/mede/detectors/
  rally_failure.py          # 6.1
  vwap_break.py             # 6.2
  vwap_rejection.py         # 6.3
  lower_high.py             # 6.4
  structure_break.py        # 6.5
  directional_efficiency.py # 6.6
services/mede/
  outcome.py                # Outcome Engine：MFE/MAE/forward returns(1..60s)/first-touch/結果分類
  backtest.py               # BedBacktester：用 MedeEngine 重播產生事件 → 進出場 → 借 microstructure_backtest 的成本/績效
docs/BED/PHASE1_INVENTORY_AND_PLAN.md   # 本文件
（測試 fixtures 置於 scratchpad，不進 repo；本專案慣例無 tests 目錄）
```

### 3.2 修改檔案
```
services/mede/feature_engine.py   # 新增 swing high/low（可設定確認筆數/時間）、VWAP 斜率、開盤價、分段效率所需累加
services/mede/config.py           # BED 專屬門檻 + swing 確認參數 + Outcome/回測參數（沿用集中設定慣例）
services/mede/enums.py            # StateType 補 CANDIDATE（規劃書 §五有、MEDE 目前 WATCH→TRIGGER 缺此中間態）
services/mede/fusion.py           # 空方分數細分：structure/trade/orderbook/veto_score（目前只有 bull/bear 總分+類別）
services/mede/state_machine.py    # 導入 CANDIDATE、trigger_reasons/veto_reasons/detector_snapshot 落地欄位對齊
services/mede/detectors/__init__.py  # 註冊 6 個新偵測器
services/mede/detection_service.py   # 加「跑回測 + Outcome」入口（run 已有；補 backtest 呼叫）
services/mede/tick_size.py        # 與 microstructure_backtest 去重（讓後者 import 前者）
viewmodels/mede_viewmodel.py      # 即時觀察擴充 + 回測控制/績效觀察屬性
views/mede_view.py                # 觀察 UI（多面板圖/五檔表/detector 表/事件表）+ 回測 UI（績效卡/淨值/明細/重播）
```

### 3.3 新增資料表（延用 per-day SQLite，storage 抽象層）
```
mede_event            已存在 → 補欄位：structure_score/trade_score/orderbook_score/veto_score/data_mode(full|l1)
mede_state_transition 已存在 → 已含 from/to/time/ref_price/reason（對齊即可）
bed_outcome           新：event_id, ret_1s..ret_60s, mfe, mae, t_to_mfe, t_to_mae,
                          first_touch, first_touch_time, continued, failed, outcome(WIN/LOSS/NEUTRAL/AMBIGUOUS/INVALID)
bed_backtest_trade    新：date, code, pattern, direction, trigger/entry/exit/stop_price, bear_score,
                          mfe, mae, gross_pnl, net_pnl, holding_ms, outcome, exit_reason, data_mode
```
（MSSQL 不動；逐筆/事件/回測全留 SQLite。）

---

## 4. 前後端資料流（桌面版）

**即時觀察**：
```
Shioaji quote callback → shioaji_service 正規化(+received_at_ns,+seq)
  → RecorderService 佇列 → RawWriter 批次寫 SQLite（不阻塞 callback）
  → 同時餵 MedeEngine(FeatureEngine→Detectors→Fusion→StateMachine)
  → 產生 snapshot/fusion/event/state → VM ObservableProperty
  → View.after(0) 更新：matplotlib 圖(降採樣/1s 聚合) + 五檔表(只更異動格) + detector 表 + 事件表
```
**歷史回測**：
```
資料源二選一：
  (a) mede_data SQLite（有五檔，full 模式）
  (b) shioaji get_historical_ticks（L1，l1 模式，降級停用五檔偵測器）
  → TickReplayEngine.replay_detect(MedeEngine，依 seq 決定性)
  → 事件 → BedBacktester 進出場(訊號確認延遲/滑價/成本/冷卻/每日上限/同波合併)
  → OutcomeEngine(MFE/MAE/forward returns) → 落地 bed_backtest_trade / bed_outcome
  → VM → View：績效卡 / 淨值曲線 / 分數-勝率 / MFE-MAE / 時段 / Pattern 比較 / 明細 / 事件重播
```
即時與回測**共用同一** FeatureEngine/Detectors/Fusion/StateMachine（規劃書硬性要求，MEDE 已符合）。

---

## 5. 開發順序（對應規劃書 §二十一，依現況調整）

- **Phase 1（本文件）**：盤點 + 方案。✅
- **Phase 2 資料蒐集**：**大多已完成**（MEDE 錄製/佇列/RawWriter/資料品質/看門狗）。僅補：資料品質不合格→暫停正式事件的旗標串接。
- **Phase 3 共用 Feature**：**擴充** feature_engine——swing high/low（確認延遲）、VWAP 斜率、開盤價、分段效率累加；確認即時==重播一致。
- **Phase 4 Detector**：新增 6 個 VWAP/結構偵測器；11 個既有偵測器做空方向情境驗證。
- **Phase 5 Score+狀態機**：fusion 空方分數細分（structure/trade/orderbook/veto）；state_machine 補 CANDIDATE + reasons/snapshot 落地。
- **Phase 6 Outcome+回測**：新增 `outcome.py` + `backtest.py`（借 microstructure_backtest 成本/績效）。
- **Phase 7 即時觀察 UI**：控制列/狀態卡/多面板圖/五檔表/detector 表/事件表。
- **Phase 8 回測 UI**：參數/績效卡/淨值/分數-勝率/時段/Pattern/明細/事件重播。

---

## 6. 測試方式

- **一致性**：固定 Tick+BidAsk fixture，即時模擬與 replay 產生**相同事件**；同資料同參數重跑**結果一致**（MEDE 決定性已具備）。
- **防未來函數**：swing 用確認筆數/時間；z-score 用線上 RollingZ（不看未來）；成交用當下對手檔+滑價。
- **降級**：只有 L1 的歷史日 → 自動 l1 模式、停用五檔偵測器、報告標示、不與 full 模式混比。
- **邊界**：斷線重連、佇列滿載、DB 短暫失敗、跨日、同波多次觸發、VWAP 假站回、Lower High 確認延遲、同 tick 同碰停利停損。
- **UI**：單股切換不殘留、圖表時間軸同步、表格增量更新、事件點擊定位、重播播放控制。
- 桌面版「截圖驗證」用既有做法：啟動真實 MainWindow → 切分頁 → matplotlib/Treeview 截圖（本 session 已驗證此法可行）。

---

## 7. 已知限制（務必納入驗收前提）

1. **歷史五檔不存在**：未錄製的過去日只有 L1 → Queue Collapse/Replenishment/OFI L2-5/Liquidity Vacuum 等**無法在歷史回測完整驗證**，只能用「即時錄製日」驗。**分點式回補一樣不適用**（見 [[backfill-data-source-limits]]）。
2. **無大盤資料**：規劃書「大盤急拉 veto」目前**無資料源**，需另接指數即時/歷史，否則此 veto 標記為未實作。
3. **同代碼單一 listener**：大單追蹤與 BED 同時觀察**同一檔**會互相覆蓋訂閱；需錯開代碼，或後續加真正的多消費者 dispatcher。
4. **門檻未校準**：MEDE 14 偵測器門檻僅合成資料驗證邏輯，**尚未用真實資料校準**（見 [[mede-phase5-status]]）；BED 新偵測器同樣需真實日校準後才可談勝率。
5. **分批回補**：現有回測無分批成交；第一版保留介面、標未完成。
6. **兩引擎並存**：BED 用 MEDE、回測借 microstructure_backtest；第一版不合併兩引擎。

---

## 8. 待你核准的決策點

1. **確認 BED = MEDE 空方擴充**（不新開架構、不引入 Web/JS 圖庫）。
2. **UI 就地擴充 MEDE 分頁** vs **另開「空方發動偵測」分頁**（規劃書要求後者；建議同一套引擎、新分頁承載 BED 的即時/回測兩模式，共用 core）。
3. **大盤 veto**：本期先標未實作，或同時接指數資料源？
4. 核准後我才進入 **Phase 2/3**（feature_engine 擴充 + 資料品質旗標），逐 Phase 回報。
