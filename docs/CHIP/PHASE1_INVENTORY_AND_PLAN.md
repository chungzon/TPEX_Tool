# 籌碼波段選股與回測（CHIP）— Phase 1 現況盤點與整合計畫

> Chip-based Swing Selection & Backtest。持有週期 5～60 交易日（**日頻**，非 tick）。
> 一句話結論：**本功能約 65% 的資料存取與計算基礎已存在**——這個 App 本質就是分點/籌碼
> 分析工具。CHIP 應**融合既有零件成統一 chip_score + 波段訊號 + 多週期回測**，
> 不重建資料層。第一版採規則 + 加權評分，待累積歷史後再上 LightGBM。

---

## 0. 與既有功能的關係（務必不重複造）

CHIP 是**日頻籌碼**，與本 session 做的 BED/MEDE（tick 微結構）完全不同層次，資料源也不同：
- BED/MEDE：Shioaji 逐筆 → SQLite `mede_data`。
- CHIP：MSSQL 五表（分點/法人/集保/融資/日行情），**全部已有查詢方法**。

現有分點分析/策略相關分頁與服務是 CHIP 的直接基礎：
- 「分點分析」`broker_analysis_view`、「策略篩選」`strategy_view`、「效益評估」`strategy_eval_view`。
- `strategy_eval_service`（集中度數學 + 突破偵測 + 回測 + MA/乖離/帶寬）。
- `alpha_service`（分點 alpha / cluster）、`correlation_service`（分點-價格相關 + **連續同向 streak**）。
- `broker_tags`（**當沖/隔日沖/短線/波段分類** → 可排除隔日沖分點）。

---

## 1. 現況盤點（可重用清單）

### 1.1 資料表（MSSQL `TSE`，DDL 在 `db_service.py`）
| 表 | 內容 | CHIP 用途 |
|---|---|---|
| `StockDailySummary` | 日 OHLC/量/額 | 技術面（突破/均線/量增）、報酬計算 |
| `BrokerDailyStats` | 分點 買/賣/淨 量 + **avg_buy_price / avg_sell_price / avg_price** | 分點進出、集中度、**主力成本** |
| `InstiDailyTrade` | 三大法人日買賣超 | 外資/投信/自營 連續買賣超 |
| `StockHolderDistribution` | 集保 level 1–17（散戶1–5/中實6–11/大戶12–15） | 大戶持股比、籌碼集中度變化（**週頻**）|
| `MarginDailyTrade` | 融資融券 | 選配：散戶槓桿背景 |

### 1.2 現成查詢方法（`db_service.py`，直接重用）
- 分點：`get_broker_history_range` / `get_broker_trades_in_range` / `get_all_brokers_daily` / `get_brokers_summary` / `get_broker_daily`
- 價格：`get_all_prices_range` / `get_stock_prices` / `get_recent_volume`
- 集保：`get_distribution_history` / `get_holder_count_history_for_codes` / `get_distribution_summary_for_codes`
- 法人：`get_insti_history_range` / `get_insti_history`
- 融資：`get_margin_history_range`
- 輔助：`get_stock_names` / `get_prev_trading_date` / `get_stock_date_range`

### 1.3 現成計算（services，直接重用）
- `strategy_eval_service`：`_aggregate_by_date` / `_window_concentration`（主力集中度）、
  `detect_breakout_signals` + `summarise`（訊號 + 後續報酬回測骨架）、
  `_price_metrics`（MA 斜率 / 乖離 / 布林帶寬）、`find_imminent_crossovers`（候選掃描）。
- `correlation_service._calc_streaks`（連續同向長度 → 套用到法人/分點淨額即得「連續買賣超」）。
- `broker_tags.get_broker_tags` / `TAG_NEXT`（隔日沖）→ **排除隔日沖分點干擾**。
- `alpha_service`：分點行為 alpha / cluster（選配增強分點分數）。

### 1.4 UI 基礎
- customtkinter 分頁 + matplotlib（`broker_analysis_view` 已有籌碼圖表模式）+ `ttk.Treeview`（clam）。
- 觀察屬性 MVVM（`ObservableProperty` + `after(0)`）。**開機自動掃描要用 `self.after` 延後**（見近期修的 mainloop race）。

---

## 2. 需要新增（缺口）

| 缺口 | 說明 |
|---|---|
| **主力成本線** | 無。以 top-N 淨買分點的 `avg_buy_price` 依買量加權、滾動累計 → 主力平均成本 |
| **chip_score 融合** | 無統一分數。需把 5 大類籌碼 + 技術面加權成 0–100 |
| **法人連續買賣超** | 資料有、streak 邏輯有（correlation），需接到 InstiDailyTrade |
| **大戶集中度變化** | 集保查詢有，需算「大戶比上升 / 散戶比下降」趨勢分 |
| **波段多週期回測** | strategy_eval 有前向報酬骨架，需擴為 5/10/20/40/60 日**持有期**回測 + 勝率/期望值/MDD |
| **買進/出場訊號** | chip_score 達門檻 + 技術轉強 → 買；轉弱 / 跌破主力成本 / 趨勢反轉 → 出 |
| **UI 分頁** | 單股籌碼趨勢圖 / 主力成本線 / 大戶變化 / 法人累積 / 分點籌碼表 / chip_score 趨勢 / 訊號表 / 回測結果 |

---

## 3. chip_score 設計（0–100，規則 + 加權；權重後續可由 LightGBM 學）

各分量各自正規化 0–100，加權合成 `chip_score`：

| 分量 | 來源 | 偏多（高分）方向 |
|---|---|---|
| 主力集中度趨勢 | `_window_concentration` 短>長且上升 | 集中且遞增 |
| 分點淨買（排隔日沖） | BrokerDailyStats net − 隔日沖分點 | 波段/主力分點連續淨買 |
| 主力成本相對價 | 現價 vs 主力成本 | 價 ≥ 主力成本且成本上移 |
| 大戶/散戶結構 | StockHolderDistribution | 大戶比升、散戶比降 |
| 法人連續買賣超 | InstiDailyTrade streak | 外資/投信連續淨買 |
| 技術面確認 | `_price_metrics` | 突破整理區 + 站上 MA20/60 + 量增 |

- **買進候選**：`chip_score ≥ buy_threshold`（預設 65）**且**技術面轉強（突破 or 站上中期均線 + 量增）。
- **出場**：`chip_score` 跌破 exit_threshold、或**跌破主力成本**、或月線轉下/趨勢反轉。
- 權重集中於一個 `ChipConfig`（比照 MedeConfig 慣例，全參數可調 + 可回測）。

---

## 4. 檔案清單

### 新增
```
services/chip_swing_service.py     # 核心：資料組裝 + 主力成本 + 6 分量 + chip_score
                                   #      + 買/出場訊號 + 5/10/20/40/60 日持有回測 + 績效
services/chip_config.py            # ChipConfig 集中參數（權重/門檻/窗口）
viewmodels/chip_swing_viewmodel.py
views/chip_swing_view.py
docs/CHIP/PHASE1_INVENTORY_AND_PLAN.md   # 本文件
```

### 修改
```
views/main_window.py               # 新增分頁「籌碼波段」+ shutdown
services/db_service.py             # 若需要：補一支「分點買均價 × 買量 範圍查詢」便利方法
                                   #（優先重用 get_broker_trades_in_range，多半不用改）
```

### 資料表
- 第一版 **compute-on-demand**（比照 strategy_eval，不落地）。
- 選配後續：`ChipSignal`（訊號歷史）/ `ChipBacktestTrade`（回測明細）落地，供 LightGBM 訓練樣本累積。

---

## 5. 前後端資料流（桌面）
```
選股票 + 區間 → chip_swing_service：
  db_service 五表查詢 → 分量計算(集中度/分點淨買排隔日沖/主力成本/大戶/法人streak/技術面)
  → 逐日 chip_score → 買/出場訊號 → 多週期持有回測 → 績效
  → VM ObservableProperty → View.after(0)：
     籌碼趨勢圖(價+主力成本線) / 大戶變化 / 法人累積 / 分點表 / chip_score 趨勢 /
     訊號表 / 5·10·20·40·60 日回測(勝率/平均報酬/期望值/最大回撤)
```

---

## 6. 實作階段（規則優先，AI 後置）
- **Phase 1（本文件）**：盤點 + 方案。
- **Phase 2**：`chip_swing_service` 資料組裝 + 主力成本 + 6 分量特徵（重用既有計算）。
- **Phase 3**：`chip_score` 加權融合 + 買/出場訊號（規則 + 加權，**不用 AI**）。
- **Phase 4**：5/10/20/40/60 日持有回測 + 勝率/平均報酬/期望值/最大回撤。
- **Phase 5**：UI 分頁（趨勢圖/主力成本線/大戶/法人/分點表/chip_score/訊號/回測）。
- **Phase 6（待歷史足量）**：LightGBM 學習各籌碼特徵權重，取代/校準加權（保留規則版可回退）。

---

## 7. 已知限制
1. **集保（大戶）為週頻**：TDCC 每週一次 → 大戶持股/集中度分量更新頻率低於日頻，需以「最近一次」向前填。
2. **分點明細不可回補**：`BrokerDailyStats` 若排程漏日則永久缺（見 [[backfill-data-source-limits]]）→ chip_score 該日退化，需標示。
3. **主力成本品質**依 `avg_buy_price` 爬蟲品質；部分日/分點可能為 NULL，需容錯。
4. **量能單位不一致**：`total_volume` 來源不同單位（見 [[stockdailysummary-volume-unit-ambiguity]]），技術面「量增」用相對比較、不用絕對值。
5. **LightGBM 後置**：需先累積足量已標記訊號（Phase 3 可開始落地 ChipSignal 當訓練樣本）。
6. **隔日沖分點名單**為靜態表（`broker_tags`），涵蓋率有限，非完全精準。

---

## 8. 待你核准的決策點
1. 確認 **CHIP = 融合既有籌碼零件 + 新增主力成本/chip_score/波段回測**，不新建資料層、不重複分點/法人/集保存取。
2. UI **另開「籌碼波段」新分頁**（建議），承載單股籌碼分析 + 回測。
3. 第一版 **compute-on-demand**（不落地）vs 同時落地 `ChipSignal`（為日後 LightGBM 存樣本）——建議先 on-demand，Phase 3 起選擇性落地。
4. 核准後我進 Phase 2（chip_swing_service 資料組裝 + 主力成本 + 分量），逐 Phase 回報。
