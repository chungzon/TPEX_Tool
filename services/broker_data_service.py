"""Service that orchestrates broker data download via the stealth browser."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable

from services.browser_service import BrowserService


@dataclass
class BrokerRecord:
    seq: str
    broker_name: str
    price: str
    buy_volume: str
    sell_volume: str


@dataclass
class BrokerDataResult:
    stock_code: str = ""
    stock_name: str = ""
    trade_date: str = ""
    total_trades: str = ""
    total_amount: str = ""
    total_volume: str = ""
    open_price: str = ""
    high_price: str = ""
    low_price: str = ""
    close_price: str = ""
    records: list[BrokerRecord] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class BrokerDataService:
    """High-level service for downloading broker buy/sell data."""

    def __init__(self, cdp_port: int = 9222):
        self._browser_svc = BrowserService(cdp_port=cdp_port)
        self._ready = False
        self._consecutive_errors = 0

    async def initialize(self, on_status: Callable[[str], None] | None = None):
        """Launch browser and navigate to the target page."""
        _status = on_status or (lambda _: None)
        _status("正在啟動瀏覽器...")
        await self._browser_svc.launch()
        _status("正在載入 TPEX 頁面...")
        await self._browser_svc.navigate_to_broker_page()
        _status("瀏覽器就緒")
        self._ready = True
        self._consecutive_errors = 0

    async def _restart_browser(self, on_status: Callable[[str], None] | None = None):
        """Kill and relaunch the browser to recover from stuck state."""
        _status = on_status or (lambda _: None)
        _status("瀏覽器異常，正在重新啟動...")
        try:
            await self._browser_svc.close()
        except Exception:
            pass
        self._ready = False
        await asyncio.sleep(3.0)
        await self.initialize(on_status)

    async def download(
        self,
        stock_code: str,
        on_status: Callable[[str], None] | None = None,
    ) -> BrokerDataResult:
        """Download broker data for a given stock code."""
        _status = on_status or (lambda _: None)

        if not self._ready:
            await self.initialize(on_status)

        _status(f"正在查詢 {stock_code} 分點資料...")
        try:
            raw = await self._browser_svc.fetch_broker_data(stock_code)
        except Exception as e:
            self._consecutive_errors += 1
            # If too many consecutive errors, browser is probably stuck
            if self._consecutive_errors >= 3:
                _status(f"連續 {self._consecutive_errors} 次失敗，重啟瀏覽器...")
                await self._restart_browser(on_status)
                self._consecutive_errors = 0
                # One more try with fresh browser
                _status(f"正在重新查詢 {stock_code}...")
                raw = await self._browser_svc.fetch_broker_data(stock_code)
            else:
                raise

        if raw.get("stat") != "ok":
            msg = raw.get("stat", "未知錯誤")
            raise RuntimeError(f"查詢失敗：{msg}")

        # Success — reset error counter
        self._consecutive_errors = 0

        result = self._parse(raw, stock_code)

        # Reset form for next query
        _status("查詢完成，正在重置表單...")
        await self._browser_svc.reset_for_next_query()

        _status(f"完成！共 {len(result.records)} 筆券商分點資料")
        return result

    async def shutdown(self):
        await self._browser_svc.close()
        self._ready = False

    @staticmethod
    def _parse(raw: dict[str, Any], stock_code: str) -> BrokerDataResult:
        tables = raw.get("tables", [])
        result = BrokerDataResult(stock_code=stock_code, raw=raw)

        # Table 0: summary
        # Actual format: [日期, 股票名, 筆數, 金額, 股數, ?, 開盤, 最高, 最低, 收盤]
        if len(tables) > 0 and tables[0].get("data"):
            row = tables[0]["data"][0]
            result.trade_date = str(row[0]) if len(row) > 0 else ""
            result.stock_name = str(row[1]) if len(row) > 1 else ""
            result.total_trades = str(row[2]) if len(row) > 2 else ""
            result.total_amount = str(row[3]) if len(row) > 3 else ""
            result.total_volume = str(row[4]) if len(row) > 4 else ""
            result.open_price = str(row[6]) if len(row) > 6 else ""
            result.high_price = str(row[7]) if len(row) > 7 else ""
            result.low_price = str(row[8]) if len(row) > 8 else ""
            result.close_price = str(row[9]) if len(row) > 9 else ""

        # Table 1: broker details
        if len(tables) > 1 and tables[1].get("data"):
            for row in tables[1]["data"]:
                if len(row) >= 5:
                    result.records.append(
                        BrokerRecord(
                            seq=str(row[0]),
                            broker_name=str(row[1]),
                            price=str(row[2]),
                            buy_volume=str(row[3]),
                            sell_volume=str(row[4]),
                        )
                    )

        return result
