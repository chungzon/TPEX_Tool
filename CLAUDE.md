# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Run

```powershell
python main.py        # launch the GUI (single entry point — no CLI, no tests)
```

Requires Python 3.10+, MSSQL running locally at `127.0.0.1:1433` (db `TSE`, user `TSE_USER`), and Chrome installed (Playwright drives the real system Chrome via CDP, not its bundled Chromium).

## Dependencies

`requirements.txt` is incomplete. Actually imported but NOT listed: `shioaji`, `ddddocr`, `scipy`, `numpy`, `requests`. Install these manually when bootstrapping a fresh env. `playwright install` is **not** needed — the browser_service connects to system Chrome over CDP.

## Architecture

MVVM with a hand-rolled observable pattern, GUI in customtkinter.

- **`views/`** — `CTkFrame` subclasses. Each calls `self.vm.bind(prop, cb)` then routes every UI update through `self.after(0, ...)` because ViewModel mutations happen on background threads.
- **`viewmodels/`** — extend `BaseViewModel` (`base_viewmodel.py`). Properties declared as `ObservableProperty(default)` class attributes auto-fire callbacks via `__set__` when the value changes. Long work runs in `threading.Thread(daemon=True)`; the VM mutates observables and the view's bound callback marshals back to the UI thread.
- **`services/`** — stateless or singleton helpers. Pure-compute services (`alpha_service`, `correlation_service`, `strategy_eval_service`) take dicts in / return dataclasses; I/O services (`db_service`, `browser_service`, `tdcc_service`, etc.) wrap DB or HTTP.
- **`main_window.py`** is the tab container and owns all VMs. Tab order matters because indexes are assigned positionally (`tab1`, `tab2`, ...) — when adding a tab, renumber everything below it and update `_on_close()` to call the new VM's `shutdown()`.

`models/` exists but is effectively empty — dataclasses live next to their service.

## Data flow & storage

Single MSSQL database (`TSE`), four tables (DDL in `services/db_service.py`):

- **`StockDailySummary`** — per stock per day OHLC/volume. Populated by `save_result()` (full broker download) and `save_daily_summary_batch()` (backfill from API).
- **`BrokerDailyStats`** — per stock per day per broker buy/sell/net. The **core** of the analysis features. Populated only by `save_result()` via the scraper.
- **`InstiDailyTrade`** — 三大法人 daily. Populated from TPEX `insti_service` and TWSE `twse_api_service.fetch_twse_insti_daily`.
- **`StockHolderDistribution`** — TDCC 集保戶股權分散表. `level` is stored as **string** `'1'..'17'`; the 散戶/中實戶/大戶 split is **not a column**, it's a convention baked into the SQL of `get_distribution_history*`: 散戶 = levels 1–5 (< 20 張), 中實戶 = 6–11, 大戶 = 12–15. Any new feature that filters by holder type must reuse the same grouping.

DB credentials are **hardcoded** in `DbService.__init__` defaults; sensitive `config.json` (Shioaji keys, stock lists) is gitignored.

`strategy_eval_service.py` is **dual-purpose** — it backs both the 效益評估 tab's backtest (`detect_breakout_signals`, `summarise`) and the 策略篩選 tab's strategy 3 screening (`find_imminent_crossovers`). They live together because they share the underlying concentration math (`_aggregate_by_date` + `_window_concentration`). Add new strategies in the same file only if they reuse this math; otherwise spin up a fresh service.

## Data source landscape

The hardest part of this codebase is the multitude of upstream quirks. Cheat sheet:

| Data | Market | Endpoint | Date param? | Notes |
|---|---|---|---|---|
| **分點明細** (broker-level) | TPEX (上櫃) | `afterTrading/brokerBS` via Playwright | **No** | Cloudflare Turnstile; scraper at `browser_service` + `broker_data_service`. Latest day only. |
| **分點明細** | TWSE (上市) | `bsr.twse.com.tw/bshtm/bsMenu.aspx` HTTP + OCR | **No** | Captcha via `ddddocr`; CSV in big5. Latest day only. Single-worker (`twse_broker_service`). |
| 每日行情 | TPEX | `afterTrading/otc?type=EW` | yes (`yyyy/mm/dd`) | `backfill_service.fetch_otc_daily` |
| 每日行情 | TWSE | `exchangeReport/MI_INDEX?type=ALLBUT0999` | yes (`yyyymmdd`) | **Use MI_INDEX, not STOCK_DAY_ALL** — the latter silently ignores `date` and always returns latest. `backfill_service.fetch_twse_daily` |
| 三大法人 | TPEX | `3insti/daily_trade/3itrade_hedge_result.php` (ROC date) | yes | `insti_service.fetch_insti_daily` |
| 三大法人 | TWSE | `rwd/zh/fund/T86` | yes | `twse_api_service.fetch_twse_insti_daily` |
| 集保分布 | both | TDCC OpenAPI | weekly only | `tdcc_service` |

**Key constraint — 分點 backfill is not possible.** Both TWSE BSR and TPEX brokerBS only serve the latest trading day. If the scheduler misses a day, that day's `BrokerDailyStats` is lost; the "補資料" tab can only restore `StockDailySummary` + `InstiDailyTrade`. See `memory/backfill-data-source-limits.md`.

## Anti-detection / pacing

Both scrapers are throttled and worth respecting when modifying.

- TPEX: `BatchDownloadViewModel` runs **3 parallel CDP browsers** on ports 9222/9223/9224, each with its own user-data dir. Random 5–12s delay between queries, 20–45s long break every 8 queries. Errors auto-relaunch the browser.
- TWSE: **single worker** on port 9230 — BSR's captcha bottleneck makes parallelism wasteful. Session is rotated every 50 queries; 403 → 60s cooldown + new session.
- All scrapers escalate to a full browser restart after consecutive failures; don't shorten the backoffs without reason.

## Scheduler

`SchedulerService` arms a daily `threading.Timer` at the configured `scheduler_time`. On fire it **reuses `BatchDownloadViewModel`** so pacing / DB writes / skip-existing logic stay in one place. `market="all"` runs OTC then TWSE sequentially. The "system 設定" tab's "立即執行" buttons share the same code path.

## Threading

Every long-running VM method (`load_*`, `start_*`, `_work`, …) spawns `threading.Thread(daemon=True)`. The `ObservableProperty.__set__` notifies on the worker thread; views must always wrap callbacks in `self.after(0, ...)` to touch tkinter safely. Don't add UI work to a callback without this wrapper.

## Gotchas

- `_normalize_date()` in `db_service.py` accepts both `yyyy-mm-dd` and ROC formats; broker scrapers return ROC, APIs return Western — always normalize before DB writes.
- `save_*` MERGE statements are idempotent; re-running a download for an existing date overwrites cleanly (no duplicates).
- Debug artifacts (`bsr_*.html`, `cap_*.png`, `captcha_*.png`, `*.log`) get dumped to repo root by the scrapers — already gitignored, leave them alone unless investigating a scraper bug.
- The TWSE scraper depends on `ddddocr` which pulls a large model on first use; first run after install is slow.
