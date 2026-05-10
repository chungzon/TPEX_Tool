"""TWSE broker buy/sell data — HTTP + OCR captcha + CSV download.

Flow:
1. GET bsMenu.aspx → extract ViewState + captcha image
2. OCR captcha with ddddocr
3. POST form → get JS redirect with StkNo & RecCount
4. GET bsContent.aspx?StkNo=X&RecCount=Y (no v=t) → download CSV
5. Parse CSV into BrokerDataResult
"""

from __future__ import annotations

import logging
import random
import re
import time

from services.broker_data_service import BrokerDataResult, BrokerRecord

log = logging.getLogger(__name__)

_ocr = None
try:
    import ddddocr
    _ocr = ddddocr.DdddOcr(show_ad=False)
except Exception:
    log.warning("ddddocr not available for TWSE captcha")

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
]

_QUERY_DELAY_MIN = 4.0
_QUERY_DELAY_MAX = 8.0
_LONG_BREAK_EVERY = 10
_LONG_BREAK_MIN = 15.0
_LONG_BREAK_MAX = 30.0


class TwseBrokerService:
    """Download TWSE broker data via HTTP + OCR + CSV."""

    BSR_URL = "https://bsr.twse.com.tw/bshtm/bsMenu.aspx"
    BSR_CSV = "https://bsr.twse.com.tw/bshtm/bsContent.aspx"

    def __init__(self, cdp_port: int = 9232):
        self._session = None
        self._ready = False
        self._consecutive_errors = 0
        self._query_count = 0

    def _new_session(self):
        import requests
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": random.choice(_USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.7",
            "Referer": self.BSR_URL,
        })

    def _ensure_session(self):
        if self._session is None:
            self._new_session()

    async def initialize(self, on_status=None):
        _s = on_status or (lambda _: None)
        self._ensure_session()
        self._ready = True
        _s("TWSE BSR 就緒")

    async def download(self, stock_code: str,
                       on_status=None) -> BrokerDataResult:
        _s = on_status or (lambda _: None)
        if not self._ready:
            await self.initialize(on_status)
        if _ocr is None:
            raise RuntimeError("ddddocr 未安裝")

        import asyncio as _aio

        max_tries = 10
        for attempt in range(1, max_tries + 1):
            try:
                _s(f"查詢 {stock_code}（第{attempt}次）...")
                result = self._fetch(stock_code)
                if result and result.records:
                    self._consecutive_errors = 0
                    self._query_count += 1
                    _s(f"完成！{len(result.records)} 筆")

                    # Pacing
                    if self._query_count % _LONG_BREAK_EVERY == 0:
                        wait = random.uniform(_LONG_BREAK_MIN, _LONG_BREAK_MAX)
                        _s(f"⏸ 休息 {wait:.0f}s...")
                        await _aio.sleep(wait)
                    else:
                        await _aio.sleep(random.uniform(
                            _QUERY_DELAY_MIN, _QUERY_DELAY_MAX))
                    return result
            except _CaptchaWrong:
                await _aio.sleep(random.uniform(1.5, 3.0))
                continue
            except Exception as e:
                self._consecutive_errors += 1
                log.warning("TWSE %s attempt %d: %s", stock_code, attempt, e)
                if "403" in str(e):
                    _s("被封鎖，等待 60 秒...")
                    await _aio.sleep(60)
                    self._new_session()
                    self._consecutive_errors = 0
                elif self._consecutive_errors >= 3:
                    await _aio.sleep(random.uniform(10, 20))
                    self._new_session()
                    self._consecutive_errors = 0
                else:
                    await _aio.sleep(random.uniform(3, 6))

        raise RuntimeError(f"TWSE {stock_code} 查詢失敗（已重試 {max_tries} 次）")

    def _fetch(self, stock_code: str) -> BrokerDataResult:
        """Full flow: captcha → POST → get redirect → download CSV → parse."""
        self._ensure_session()
        s = self._session

        # 1. GET page
        time.sleep(random.uniform(0.5, 1.5))
        resp = s.get(self.BSR_URL, timeout=15)
        resp.raise_for_status()
        html = resp.text

        vs = self._field(html, "__VIEWSTATE")
        vsg = self._field(html, "__VIEWSTATEGENERATOR")
        ev = self._field(html, "__EVENTVALIDATION")
        if not vs:
            raise RuntimeError("無 ViewState")

        # 2. Captcha
        time.sleep(random.uniform(0.5, 1.5))
        cap = re.search(r"src=['\"]?(CaptchaImage\.aspx\?guid=[^'\">\s]+)", html)
        if not cap:
            raise RuntimeError("找不到驗證碼")
        img = s.get("https://bsr.twse.com.tw/bshtm/" + cap.group(1), timeout=10)
        if len(img.content) < 500:
            raise RuntimeError("驗證碼圖片異常")
        code = _ocr.classification(img.content).upper()
        if len(code) != 5:
            raise _CaptchaWrong(f"OCR len={len(code)}")

        # 3. POST
        time.sleep(random.uniform(1.0, 2.5))
        form = {
            "__EVENTTARGET": "", "__EVENTARGUMENT": "", "__LASTFOCUS": "",
            "__VIEWSTATE": vs, "__VIEWSTATEGENERATOR": vsg,
            "__EVENTVALIDATION": ev,
            "RadioButton_Normal": "RadioButton_Normal",
            "TextBox_Stkno": stock_code,
            "CaptchaControl1": code,
            "btnOK": "查詢",
        }
        resp2 = s.post(self.BSR_URL, data=form, timeout=15)
        resp2.raise_for_status()
        post_html = resp2.text

        if "驗證碼錯誤" in post_html:
            raise _CaptchaWrong("驗證碼錯誤")

        # 4. Extract redirect → get CSV URL params
        redir = re.search(r"location\.href='([^']+)'", post_html)
        if not redir:
            raise _CaptchaWrong("無 redirect URL")

        # Parse StkNo and RecCount from redirect
        redir_url = redir.group(1)
        stk_m = re.search(r"StkNo=(\w+)", redir_url)
        rec_m = re.search(r"RecCount=(\d+)", redir_url)
        if not stk_m or not rec_m:
            raise RuntimeError(f"無法解析 redirect: {redir_url}")

        stk_no = stk_m.group(1)
        rec_count = rec_m.group(1)

        # 4b. Get trade date + stock name from the HTML version
        time.sleep(random.uniform(0.3, 0.8))
        html_url = f"{self.BSR_CSV}?v=t&StkNo={stk_no}&RecCount={rec_count}"
        resp_html = s.get(html_url, timeout=30)
        html_text = resp_html.text
        # Extract date and name
        trade_date = ""
        stock_name = ""
        dm = re.search(r"(\d{4}/\d{2}/\d{2})", html_text)
        if dm:
            trade_date = dm.group(1)
        nm = re.search(rf"{stock_code}(?:&nbsp;|\s)+([^<&\s]+)", html_text)
        if nm:
            stock_name = nm.group(1).strip()

        # 5. Download CSV (without v=t → returns CSV not HTML)
        time.sleep(random.uniform(0.5, 1.0))
        csv_url = f"{self.BSR_CSV}?StkNo={stk_no}&RecCount={rec_count}"
        log.info("TWSE CSV: %s", csv_url)
        resp3 = s.get(csv_url, timeout=30)
        resp3.raise_for_status()
        csv_text = resp3.content.decode("big5", errors="replace")

        # 6. Parse CSV
        result = self._parse_csv(csv_text, stock_code)
        if trade_date:
            result.trade_date = trade_date
        if stock_name:
            result.stock_name = stock_name
        return result

    @staticmethod
    def _field(html: str, name: str) -> str:
        m = re.search(rf'id="{name}"[^>]*value="([^"]*)"', html)
        if m:
            return m.group(1)
        m = re.search(rf'name="{name}"[^>]*value="([^"]*)"', html)
        return m.group(1) if m else ""

    @staticmethod
    def _parse_csv(csv_text: str, stock_code: str) -> BrokerDataResult:
        """Parse BSR CSV into BrokerDataResult.

        CSV format:
          Line 0: 券商買賣股票成交價量資訊
          Line 1: 股票代碼,="2330"
          Line 2: 序號,券商,價格,買進股數,賣出股數,,序號,券商,價格,買進股數,賣出股數
          Line 3+: data (two records per line separated by ,,)
        """
        result = BrokerDataResult(stock_code=stock_code)
        lines = csv_text.strip().split("\n")

        if len(lines) < 3:
            return result

        # Parse stock code from line 1
        code_m = re.search(r'="?(\d+)"?', lines[1])
        if code_m:
            result.stock_code = code_m.group(1)

        # Parse data rows (from line 3 onward)
        for line in lines[3:]:
            line = line.strip()
            if not line:
                continue

            # Each line has two records separated by ,,
            # Format: seq,broker,price,buy,sell,,seq,broker,price,buy,sell
            halves = line.split(",,")
            for half in halves:
                half = half.strip()
                if not half:
                    continue
                parts = half.split(",")
                if len(parts) < 5:
                    continue

                seq = parts[0].strip()
                if not seq or not seq.replace(",", "").isdigit():
                    continue

                broker = parts[1].strip().replace("\u3000", " ")
                price = parts[2].strip()
                buy = parts[3].strip().replace(",", "")
                sell = parts[4].strip().replace(",", "")

                # Extract broker code + name
                bm = re.match(r"^(\d{4}[A-Za-z]?)\s*(.*)", broker)
                if bm:
                    broker_name = f"{bm.group(1)} {bm.group(2)}".strip()
                else:
                    broker_name = broker

                # Set stock name from first broker record
                if not result.stock_name and bm:
                    pass  # name not in CSV per-record

                result.records.append(BrokerRecord(
                    seq=seq,
                    broker_name=broker_name,
                    price=price,
                    buy_volume=buy,
                    sell_volume=sell,
                ))

        log.info("TWSE CSV parsed: %s -> %d records", stock_code, len(result.records))
        return result

    async def shutdown(self):
        if self._session:
            self._session.close()
            self._session = None
        self._ready = False

    async def reset_for_next_query(self):
        import asyncio
        await asyncio.sleep(random.uniform(1.0, 2.0))
        if self._query_count > 0 and self._query_count % 50 == 0:
            self._new_session()


class _CaptchaWrong(Exception):
    pass
