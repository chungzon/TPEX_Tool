from __future__ import annotations

import asyncio
import random
import threading
from datetime import datetime

from viewmodels.base_viewmodel import BaseViewModel, ObservableProperty
from services.broker_data_service import BrokerDataService
from services.db_service import DbService, _normalize_date

# Batch pacing: random delay between queries to look human
_DELAY_MIN = 5.0    # seconds
_DELAY_MAX = 12.0
# Every N queries, take a longer break
_LONG_BREAK_EVERY = 8
_LONG_BREAK_MIN = 20.0
_LONG_BREAK_MAX = 45.0

# Parallel workers
_NUM_WORKERS = 3
_BASE_CDP_PORT = 9222


class BatchDownloadViewModel(BaseViewModel):
    """ViewModel for batch downloading multiple stocks into DB."""

    status_text = ObservableProperty("就緒")
    is_downloading = ObservableProperty(False)
    progress = ObservableProperty(0.0)
    progress_text = ObservableProperty("")   # e.g. "3 / 10 (30%)"
    log_text = ObservableProperty("")        # running log
    error_text = ObservableProperty("")
    trade_date_text = ObservableProperty("")  # 目前 API 回傳的交易日期

    def __init__(self, num_workers: int = _NUM_WORKERS):
        super().__init__()
        self._num_workers = num_workers
        self._db_svc = DbService()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._cancel = False
        # Shared counters (accessed from async tasks on the same loop)
        self._done = 0
        self._skipped = 0
        self._errors = 0
        self._completed = 0   # total completed (done + skipped + errors)
        self._total = 0
        self._lock = asyncio.Lock()

    def _ensure_loop(self):
        if self._loop is None or not self._loop.is_running():
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
            self._thread.start()

    def start_batch(self, stock_codes_text: str, skip_existing: bool = True):
        """Parse stock codes (comma / newline / space separated) and start."""
        if self.is_downloading:
            return

        codes = self._parse_codes(stock_codes_text)
        if not codes:
            self.error_text = "請輸入至少一個股票代碼"
            return

        self.error_text = ""
        self.is_downloading = True
        self.progress = 0.0
        self.log_text = ""
        self.trade_date_text = ""
        self._cancel = False
        self._done = 0
        self._skipped = 0
        self._errors = 0
        self._completed = 0
        self._total = len(codes)

        self._ensure_loop()
        asyncio.run_coroutine_threadsafe(
            self._do_parallel_batch(codes, skip_existing), self._loop,
        )

    def cancel(self):
        self._cancel = True

    @staticmethod
    def _get_today_str() -> str:
        return datetime.now().strftime("%Y-%m-%d")

    # ----------------------------------------------------------------
    # Parallel batch
    # ----------------------------------------------------------------

    async def _do_parallel_batch(self, codes: list[str], skip_existing: bool):
        total = len(codes)
        n_workers = min(self._num_workers, total)
        self._log(f"共 {total} 檔股票，使用 {n_workers} 個 Worker 平行下載\n")

        try:
            self._db_svc.ensure_tables()
            self._log("資料庫連線成功，資料表已就緒\n")
            self._log("─" * 40 + "\n")

            # Split codes into chunks for each worker
            chunks: list[list[str]] = [[] for _ in range(n_workers)]
            for i, code in enumerate(codes):
                chunks[i % n_workers].append(code)

            # Create worker tasks
            tasks = []
            broker_svcs: list[BrokerDataService] = []
            for w_id in range(n_workers):
                port = _BASE_CDP_PORT + w_id
                svc = BrokerDataService(cdp_port=port)
                broker_svcs.append(svc)
                tasks.append(
                    self._worker(w_id, svc, chunks[w_id], skip_existing, total)
                )

            # Run all workers concurrently
            await asyncio.gather(*tasks, return_exceptions=True)

            # Summary
            self.status_text = f"完成！成功 {self._done}/{total} 檔"
            self._log("─" * 40 + "\n")
            self._log(f"下載結束，成功 {self._done}/{total} 檔")
            if self._skipped > 0:
                self._log(f"（已存在跳過 {self._skipped} 檔）")
            if self._errors > 0:
                self._log(f"（錯誤 {self._errors} 檔）")
            self._log("\n")

        except Exception as e:
            self.error_text = str(e)
            self.status_text = f"錯誤：{e}"
            self._log(f"\n致命錯誤：{e}\n")
        finally:
            # Shutdown all browser instances
            for svc in broker_svcs:
                try:
                    await svc.shutdown()
                except Exception:
                    pass
            self.is_downloading = False

    async def _worker(
        self, w_id: int, broker_svc: BrokerDataService,
        codes: list[str], skip_existing: bool, total: int,
    ):
        """One worker: downloads its chunk of stocks sequentially."""
        tag = f"W{w_id + 1}"
        batch_trade_date: str | None = None

        for i, code in enumerate(codes):
            if self._cancel:
                self._log(f"[{tag}] 已取消\n")
                break

            self.status_text = f"[{tag}] 下載 {code}"

            try:
                def on_status(msg: str, _t=tag, _c=code):
                    self.status_text = f"[{_t}][{_c}] {msg}"

                result = await broker_svc.download(code, on_status=on_status)

                api_date = _normalize_date(result.trade_date)

                # Detect trade date on first result
                if batch_trade_date is None:
                    batch_trade_date = api_date
                    today = self._get_today_str()
                    self.trade_date_text = batch_trade_date
                    if batch_trade_date != today:
                        self._log(
                            f"[{tag}] API 交易日期：{batch_trade_date}"
                            f"（系統日期：{today}）\n"
                        )

                if api_date != batch_trade_date:
                    self._log(
                        f"[{tag}] 交易日期變更：{batch_trade_date} → {api_date}\n"
                    )
                    batch_trade_date = api_date
                    self.trade_date_text = batch_trade_date

                # Check skip
                if skip_existing and self._db_svc.stock_exists(code, result.trade_date):
                    self._log(
                        f"[{tag}][{code}] {result.stock_name}"
                        f"（{api_date}）— 已存在，跳過\n"
                    )
                    self._skipped += 1
                else:
                    count = self._db_svc.save_result(result)
                    self._log(
                        f"[{tag}][{code}] {result.stock_name}"
                        f"（{api_date}）— 寫入 {count} 筆\n"
                    )
                    self._done += 1

            except Exception as e:
                self._log(f"[{tag}][{code}] 錯誤：{e}\n")
                self._errors += 1

            # Update progress
            self._completed += 1
            pct = int(self._completed / total * 100)
            self.progress = self._completed / total
            self.progress_text = f"{self._completed} / {total}（{pct}%）"

            # Anti-detection pacing (per worker)
            if i < len(codes) - 1 and not self._cancel:
                if (i + 1) % _LONG_BREAK_EVERY == 0:
                    wait = random.uniform(_LONG_BREAK_MIN, _LONG_BREAK_MAX)
                    self._log(f"  [{tag}] 長休息 {wait:.0f}s\n")
                else:
                    wait = random.uniform(_DELAY_MIN, _DELAY_MAX)
                await asyncio.sleep(wait)

    def _log(self, text: str):
        self.log_text = (self.log_text or "") + text

    def shutdown(self):
        self._cancel = True
        try:
            self._db_svc.close()
        except Exception:
            pass
        if self._loop and self._loop.is_running():
            # Shutdown is best-effort; browser cleanup happens in _do_parallel_batch finally
            pass

    @staticmethod
    def _parse_codes(text: str) -> list[str]:
        """Split input by comma, newline, or space; deduplicate preserving order."""
        import re
        parts = re.split(r"[,\s\n\r]+", text.strip())
        seen = set()
        result = []
        for p in parts:
            p = p.strip()
            if p and p not in seen:
                seen.add(p)
                result.append(p)
        return result
