"""Browser automation service — connects to the real system Chrome via CDP.

Supports multiple instances on different CDP ports for parallel downloads.

Lifecycle safety:
- launch() kills any leftover Chrome on the same debug port before starting.
- close() terminates Chrome and cleans up, with a force-kill fallback.
- atexit handler cleans up all tracked instances.
"""

from __future__ import annotations

import atexit
import asyncio
import os
import random
import signal
import subprocess
import tempfile
import time
from typing import Any

from playwright.async_api import async_playwright, Browser, Page


# ---------------------------------------------------------------------------
# Browser discovery
# ---------------------------------------------------------------------------
_BROWSER_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

# Track all active Chrome PIDs for cleanup
_active_pids: set[int] = set()


def _find_browser() -> str:
    for p in _BROWSER_PATHS:
        if os.path.isfile(p):
            return p
    raise FileNotFoundError("找不到 Chrome 或 Edge 瀏覽器")


def _kill_port(port: int):
    """Kill any process listening on the given port."""
    try:
        out = subprocess.check_output(
            f'netstat -ano | findstr "LISTENING" | findstr ":{port}"',
            shell=True, text=True, stderr=subprocess.DEVNULL,
        )
        for line in out.strip().splitlines():
            parts = line.split()
            pid = int(parts[-1])
            if pid > 0:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
    except (subprocess.CalledProcessError, Exception):
        pass


def _cleanup_all():
    """atexit handler: kill all Chrome instances we launched."""
    for pid in list(_active_pids):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    _active_pids.clear()


atexit.register(_cleanup_all)


class BrowserService:
    """Launch the real system browser and control it through CDP.

    Each instance uses its own CDP port and user-data directory,
    allowing multiple parallel Chrome instances.
    """

    TPEX_BROKER_URL = (
        "https://www.tpex.org.tw/zh-tw/mainboard/trading/info/brokerBS.html"
    )
    API_ENDPOINT = "/www/zh-tw/afterTrading/brokerBS"

    def __init__(self, cdp_port: int = 9222):
        self._cdp_port = cdp_port
        self._playwright = None
        self._browser: Browser | None = None
        self._page: Page | None = None
        self._chrome_proc: subprocess.Popen | None = None
        self._user_data_dir: str | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def launch(self):
        """Start the system browser with remote-debugging and connect via CDP."""
        # Safety: kill any leftover Chrome on the same port
        _kill_port(self._cdp_port)

        browser_exe = _find_browser()

        self._user_data_dir = os.path.join(
            tempfile.gettempdir(),
            f"tpex_tool_chrome_{self._cdp_port}",
        )
        os.makedirs(self._user_data_dir, exist_ok=True)

        self._chrome_proc = subprocess.Popen(
            [
                browser_exe,
                f"--remote-debugging-port={self._cdp_port}",
                f"--user-data-dir={self._user_data_dir}",
                "--window-position=-32000,-32000",
                "--window-size=1280,850",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                "--disable-client-side-phishing-detection",
                "--disable-default-apps",
                "--disable-hang-monitor",
                "--disable-popup-blocking",
                "--disable-prompt-on-repost",
                "--disable-sync",
                "--metrics-recording-only",
                "--safebrowsing-disable-auto-update",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _active_pids.add(self._chrome_proc.pid)

        await asyncio.sleep(2.0)

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.connect_over_cdp(
            f"http://127.0.0.1:{self._cdp_port}"
        )

        contexts = self._browser.contexts
        ctx = contexts[0] if contexts else await self._browser.new_context()
        pages = ctx.pages
        self._page = pages[0] if pages else await ctx.new_page()

    async def close(self):
        """Shutdown browser gracefully; force-kill if Chrome hangs."""
        try:
            if self._browser:
                await self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass

        if self._chrome_proc:
            pid = self._chrome_proc.pid
            self._chrome_proc.terminate()
            try:
                self._chrome_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._chrome_proc.kill()
                self._chrome_proc.wait(timeout=3)
            self._chrome_proc = None
            _active_pids.discard(pid)

        self._browser = None
        self._playwright = None
        self._page = None

    # ------------------------------------------------------------------
    # Page interaction
    # ------------------------------------------------------------------

    async def navigate_to_broker_page(self):
        await self._page.goto(
            self.TPEX_BROKER_URL, wait_until="domcontentloaded", timeout=30000,
        )
        await self._page.wait_for_selector('input[name="code"]', timeout=15000)
        await asyncio.sleep(random.uniform(2.0, 3.5))

    async def _human_type(self, selector: str, text: str):
        el = self._page.locator(selector)
        await el.click()
        await asyncio.sleep(random.uniform(0.4, 0.9))
        await el.fill("")
        for ch in text:
            base_delay = random.uniform(80, 250)
            if random.random() < 0.15:
                base_delay += random.uniform(200, 500)
            await el.press_sequentially(ch, delay=base_delay)
        await asyncio.sleep(random.uniform(0.3, 1.0))

    async def _wait_for_turnstile(self, timeout: float = 45.0):
        page = self._page
        start = time.time()
        while time.time() - start < timeout:
            token = await page.evaluate("""
                () => {
                    const el = document.querySelector(
                        'input[name="cf-turnstile-response"]'
                    );
                    return el ? el.value : '';
                }
            """)
            if token:
                return token
            await asyncio.sleep(0.5)
        raise TimeoutError("Turnstile 驗證逾時，請稍後再試")

    async def fetch_broker_data(self, stock_code: str) -> dict[str, Any]:
        """Fetch broker data with auto-retry and Turnstile refresh."""
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                return await self._fetch_once(stock_code)
            except TimeoutError:
                if attempt < max_retries:
                    await self._refresh_page()
                else:
                    raise TimeoutError(
                        f"API 回應逾時（已重試 {max_retries} 次）"
                    )
            except Exception:
                if attempt < max_retries:
                    await asyncio.sleep(random.uniform(2.0, 5.0))
                else:
                    raise

    async def _fetch_once(self, stock_code: str) -> dict[str, Any]:
        page = self._page
        captured: dict[str, Any] = {}
        event = asyncio.Event()

        async def _on_response(response):
            if self.API_ENDPOINT in response.url and response.status == 200:
                try:
                    body = await response.json()
                    captured.update(body)
                    event.set()
                except Exception:
                    pass

        page.on("response", _on_response)
        try:
            await self._human_type('input[name="code"]', stock_code)
            await self._wait_for_turnstile()
            await asyncio.sleep(random.uniform(0.5, 1.2))

            submit_btn = page.locator('#tables-form button[type="submit"]')
            await submit_btn.click()

            try:
                await asyncio.wait_for(event.wait(), timeout=30.0)
            except asyncio.TimeoutError:
                raise TimeoutError("API 回應逾時")
            return captured
        finally:
            page.remove_listener("response", _on_response)

    async def _refresh_page(self):
        try:
            await self._page.goto(
                self.TPEX_BROKER_URL,
                wait_until="domcontentloaded", timeout=30000,
            )
            await self._page.wait_for_selector(
                'input[name="code"]', timeout=15000,
            )
            await asyncio.sleep(random.uniform(3.0, 5.0))
        except Exception:
            pass

    async def reset_for_next_query(self):
        await asyncio.sleep(random.uniform(2.0, 5.0))

        if random.random() < 0.4:
            await self._page.evaluate(
                "window.scrollBy(0, %d)" % random.randint(-200, 300))
            await asyncio.sleep(random.uniform(0.5, 1.5))

        code_input = self._page.locator('input[name="code"]')
        await code_input.click()
        await asyncio.sleep(random.uniform(0.3, 0.7))
        await code_input.fill("")
        await asyncio.sleep(random.uniform(1.0, 2.5))
