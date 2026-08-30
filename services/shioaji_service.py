"""Shioaji trading service — wraps Sinopac securities API."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

log = logging.getLogger(__name__)


class ShioajiService:
    """Manages Shioaji connection, quotes, and order execution."""

    def __init__(self):
        self._api = None
        self._logged_in = False
        self._stock_account = None
        self._lock = threading.Lock()
        self._order_callbacks: list[Callable] = []
        # Real-time quote streaming (tick / bidask)
        # 依股票代碼分派 → 支援多檔、多消費者（大單追蹤與 MEDE 可並存）。
        self._quote_cb_set = False
        self._tick_listeners: dict[str, Callable] = {}
        self._bidask_listeners: dict[str, Callable] = {}
        self._quote_seq = 0            # 全域遞增序號（供重播排序）
        self._subscribed: set[str] = set()

    @property
    def is_logged_in(self) -> bool:
        return self._logged_in

    @property
    def api(self):
        return self._api

    def login(self, api_key: str, secret_key: str,
              person_id: str = "", ca_passwd: str = "",
              simulation: bool = True,
              on_status: Callable[[str], None] | None = None):
        """Login to Shioaji and activate CA for trading."""
        _status = on_status or (lambda _: None)
        try:
            import shioaji as sj

            env_txt = "測試環境" if simulation else "正式環境"
            _status(f"正在連線永豐金（{env_txt}）...")
            self._api = sj.Shioaji(simulation=simulation)
            self._api.login(
                api_key=api_key,
                secret_key=secret_key,
                fetch_contract=True,
                contracts_timeout=30000,
                subscribe_trade=True,
            )

            # Activate CA certificate for order placement
            if person_id and ca_passwd:
                _status("正在啟用憑證...")
                import os
                # Try common CA file locations
                ca_candidates = [
                    rf"C:\ekey\551\{person_id}\S\Sinopac.pfx",
                    rf"D:\Projects\Sinopac.pfx",
                    os.path.join(os.path.dirname(__file__), "..", "Sinopac.pfx"),
                ]
                ca_path = None
                for p in ca_candidates:
                    if os.path.isfile(p):
                        ca_path = p
                        break
                if not ca_path:
                    _status("找不到憑證檔案 Sinopac.pfx")
                    log.warning("CA file not found in: %s", ca_candidates)
                    self._logged_in = True
                    return True
                self._api.activate_ca(
                    ca_path=ca_path,
                    ca_passwd=ca_passwd,
                    person_id=person_id,
                )
                log.info("CA activated for %s", person_id)

            self._logged_in = True

            # Set order callback
            self._api.set_order_callback(self._on_order_event)

            accts = self._api.list_accounts()
            log.info("Shioaji login OK. Accounts: %d", len(accts))
            for i, a in enumerate(accts):
                log.info("  Account[%d]: %s id=%s broker=%s signed=%s",
                         i, type(a).__name__,
                         getattr(a, 'account_id', '?'),
                         getattr(a, 'broker_id', '?'),
                         getattr(a, 'signed', '?'))

            # Find stock account by type name
            stock_acct = self._api.stock_account
            if stock_acct is None:
                import shioaji as _sj
                for a in accts:
                    if isinstance(a, _sj.account.StockAccount):
                        stock_acct = a
                        break
                # If still None, might be FutureAccount only
                if stock_acct is None and accts:
                    stock_acct = accts[0]
                    log.warning("No StockAccount found, using first: %s",
                                type(stock_acct).__name__)

            acct_id = getattr(stock_acct, 'account_id', '未知') if stock_acct else '無帳號'
            self._stock_account = stock_acct
            _status(f"登入成功！帳號：{acct_id}")
            return True
        except Exception as e:
            self._logged_in = False
            log.exception("Shioaji login failed")
            _status(f"登入失敗：{e}")
            return False

    def logout(self):
        if self._api and self._logged_in:
            try:
                self.unsubscribe_all_quotes()
            except Exception:
                pass
            try:
                self._api.logout()
            except Exception:
                pass
        self._logged_in = False
        self._api = None
        self._quote_cb_set = False
        self._subscribed.clear()
        self._tick_listeners.clear()
        self._bidask_listeners.clear()

    # ---- Contract lookup ----

    def get_stock_contract(self, stock_code: str):
        """Get a stock contract by code."""
        if not self._logged_in:
            return None
        try:
            return self._api.Contracts.Stocks[stock_code]
        except Exception:
            return None

    def get_snapshot(self, stock_code: str) -> dict | None:
        """Get current snapshot for a stock."""
        if not self._logged_in:
            return None
        contract = self.get_stock_contract(stock_code)
        if not contract:
            return None
        try:
            snaps = self._api.snapshots([contract], timeout=10000)
            if snaps:
                s = snaps[0]
                return {
                    "code": s.code,
                    "close": s.close,
                    "open": s.open,
                    "high": s.high,
                    "low": s.low,
                    "volume": s.volume,
                    "total_volume": s.total_volume,
                    "buy_price": s.buy_price,
                    "sell_price": s.sell_price,
                    "change_price": s.change_price,
                    "change_type": s.change_type,
                }
        except Exception as e:
            log.warning("Snapshot failed for %s: %s", stock_code, e)
        return None

    def get_snapshots(self, codes: list[str]) -> dict[str, dict]:
        """批次快照（供隔日沖監控取權證參考價/量）。回 {code: {close, change_price,
        total_volume, volume}}。未登入或失敗回已取得部分。"""
        if not self._logged_in or not self._api:
            return {}
        contracts = []
        for c in codes:
            ct = self.get_stock_contract(c)
            if ct is not None:
                contracts.append(ct)
        if not contracts:
            return {}
        out: dict[str, dict] = {}
        try:
            for s in self._api.snapshots(contracts, timeout=15000):
                out[s.code] = {
                    "close": getattr(s, "close", 0),
                    "change_price": getattr(s, "change_price", 0),
                    "total_volume": getattr(s, "total_volume", 0),
                    "volume": getattr(s, "volume", 0),
                }
        except Exception as e:  # noqa: BLE001
            log.warning("get_snapshots failed: %s", e)
        return out

    # ---- Real-time quote streaming (tick / bidask) ----

    def _ensure_quote_callbacks(self):
        """Register the STK v1 tick / bidask callbacks once."""
        if self._quote_cb_set or not self._api:
            return
        self._api.quote.set_on_tick_stk_v1_callback(self._handle_tick)
        self._api.quote.set_on_bidask_stk_v1_callback(self._handle_bidask)
        self._quote_cb_set = True

    def _handle_tick(self, exchange, tick):
        """Shioaji TickSTKv1 → plain dict → 依代碼分派給該檔的監聽者。

        stamps received_at_ns（系統收到時間）與 seq（全域遞增序）供 MEDE 錄製/重播。
        callback 需輕量（僅入列），勿在此阻塞 socket 緒。"""
        code = getattr(tick, "code", "")
        cb = self._tick_listeners.get(code)
        if not cb:
            return
        try:
            self._quote_seq += 1
            data = {
                "code": code,
                "time": str(getattr(tick, "datetime", "")),
                "close": getattr(tick, "close", 0),
                "avg_price": getattr(tick, "avg_price", 0),
                "high": getattr(tick, "high", 0),
                "low": getattr(tick, "low", 0),
                "volume": getattr(tick, "volume", 0),
                "total_volume": getattr(tick, "total_volume", 0),
                # 1=外盤(買方觸發) 2=內盤(賣方觸發) 0=無法判定
                "tick_type": getattr(tick, "tick_type", 0),
                "simtrade": getattr(tick, "simtrade", 0),
                "intraday_odd": getattr(tick, "intraday_odd", 0),
                "received_at_ns": time.time_ns(),
                "seq": self._quote_seq,
            }
            # 忽略試撮 / 盤中零股，避免污染微觀結構統計
            if data["simtrade"] or data["intraday_odd"]:
                return
            cb(data)
        except Exception:
            log.exception("tick handler failed")

    def _handle_bidask(self, exchange, bidask):
        """Shioaji BidAskSTKv1 → plain dict → 依代碼分派給該檔的監聽者。"""
        code = getattr(bidask, "code", "")
        cb = self._bidask_listeners.get(code)
        if not cb:
            return
        try:
            self._quote_seq += 1
            data = {
                "code": code,
                "time": str(getattr(bidask, "datetime", "")),
                "bid_price": list(getattr(bidask, "bid_price", []) or []),
                "bid_volume": list(getattr(bidask, "bid_volume", []) or []),
                "ask_price": list(getattr(bidask, "ask_price", []) or []),
                "ask_volume": list(getattr(bidask, "ask_volume", []) or []),
                "simtrade": getattr(bidask, "simtrade", 0),
                "intraday_odd": getattr(bidask, "intraday_odd", 0),
                "received_at_ns": time.time_ns(),
                "seq": self._quote_seq,
            }
            if data["simtrade"] or data["intraday_odd"]:
                return
            cb(data)
        except Exception:
            log.exception("bidask handler failed")

    def subscribe_quote(self, stock_code: str,
                        on_tick: Callable[[dict], None] | None = None,
                        on_bidask: Callable[[dict], None] | None = None) -> bool:
        """訂閱某檔股票的即時逐筆成交(Tick) + 五檔委買賣(BidAsk)。

        注意：即時行情串流需在**正式環境**登入才會推送；模擬(測試)環境不供行情。
        """
        if not self._logged_in or not self._api:
            return False
        import shioaji as sj

        contract = self.get_stock_contract(stock_code)
        if not contract:
            return False
        try:
            if on_tick:
                self._tick_listeners[stock_code] = on_tick
            if on_bidask:
                self._bidask_listeners[stock_code] = on_bidask
            self._ensure_quote_callbacks()
            self._api.quote.subscribe(
                contract, quote_type=sj.constant.QuoteType.Tick,
                version=sj.constant.QuoteVersion.v1)
            self._api.quote.subscribe(
                contract, quote_type=sj.constant.QuoteType.BidAsk,
                version=sj.constant.QuoteVersion.v1)
            self._subscribed.add(stock_code)
            log.info("Subscribed tick+bidask for %s", stock_code)
            return True
        except Exception:
            log.exception("subscribe_quote failed for %s", stock_code)
            return False

    def unsubscribe_quote(self, stock_code: str) -> None:
        """取消某檔股票的行情訂閱。"""
        if not self._logged_in or not self._api:
            return
        import shioaji as sj
        contract = self.get_stock_contract(stock_code)
        if not contract:
            return
        for qt in (sj.constant.QuoteType.Tick, sj.constant.QuoteType.BidAsk):
            try:
                self._api.quote.unsubscribe(
                    contract, quote_type=qt, version=sj.constant.QuoteVersion.v1)
            except Exception:
                log.warning("unsubscribe %s %s failed", stock_code, qt)
        self._tick_listeners.pop(stock_code, None)
        self._bidask_listeners.pop(stock_code, None)
        self._subscribed.discard(stock_code)

    def unsubscribe_all_quotes(self) -> None:
        for code in list(self._subscribed):
            self.unsubscribe_quote(code)

    # ---- Historical tick data (for backtest) ----

    def get_historical_ticks(self, stock_code: str, date: str) -> list[dict]:
        """取得某檔股票某日的逐筆成交資料（含 level-1 買賣價量）供回測用。

        date 格式 'YYYY-MM-DD'。回傳依時間排序的 tick dict list。
        注意：永豐歷史 ticks 僅提供第一檔（best bid/ask），非五檔。
        """
        if not self._logged_in or not self._api:
            return []
        contract = self.get_stock_contract(stock_code)
        if not contract:
            return []
        try:
            t = self._api.ticks(contract, date, timeout=60000)
            n = len(t.ts)
            out: list[dict] = []
            for i in range(n):
                out.append({
                    "ts": t.ts[i],
                    "close": float(t.close[i]),
                    "volume": float(t.volume[i]),
                    "bid_price": float(t.bid_price[i]),
                    "bid_volume": float(t.bid_volume[i]),
                    "ask_price": float(t.ask_price[i]),
                    "ask_volume": float(t.ask_volume[i]),
                    "tick_type": int(t.tick_type[i]),
                })
            log.info("Fetched %d historical ticks for %s %s", n, stock_code, date)
            return out
        except Exception:
            log.exception("get_historical_ticks failed for %s %s", stock_code, date)
            return []

    # ---- Intraday minute bars (for live 走勢圖) ----

    def get_intraday_kbars(self, stock_code: str, date: str) -> list[dict]:
        """某檔某日 1 分 K（含量）。date 格式 'YYYY-MM-DD'。

        盤中呼叫回到目前為止已成形的分 K，供即時走勢圖用（可重覆輪詢延伸）。
        回 [{time(HH:MM), open, high, low, close, volume}]，依時間升冪。
        kbars.ts 為 epoch 奈秒；轉本地時間取 HH:MM。
        """
        if not self._logged_in or not self._api:
            return []
        contract = self.get_stock_contract(stock_code)
        if not contract:
            return []
        try:
            from datetime import datetime as _dt
            kb = self._api.kbars(contract, start=date, end=date, timeout=30000)
            ts = list(kb.ts)
            out: list[dict] = []
            for i in range(len(ts)):
                hhmm = _dt.fromtimestamp(ts[i] / 1e9).strftime("%H:%M")
                out.append({
                    "time": hhmm,
                    "open": float(kb.Open[i]),
                    "high": float(kb.High[i]),
                    "low": float(kb.Low[i]),
                    "close": float(kb.Close[i]),
                    "volume": float(kb.Volume[i]),
                })
            out.sort(key=lambda x: x["time"])
            return out
        except Exception:
            log.exception("get_intraday_kbars failed for %s %s", stock_code, date)
            return []

    def get_kbars_range(self, stock_code: str, start: str,
                        end: str) -> list[dict]:
        """區間 1 分 K（可跨日），供分 K 均線/扣抵值計算的往前抓取。

        start/end 格式 'YYYY-MM-DD'（含頭尾）。回 [{date, time(HH:MM),
        minute_of_day(當日第幾分), open, high, low, close, volume}] 依時序升冪。
        盤中呼叫今日區間回到目前為止已成形的分 K。
        """
        if not self._logged_in or not self._api:
            return []
        contract = self.get_stock_contract(stock_code)
        if not contract:
            return []
        try:
            from datetime import datetime as _dt
            kb = self._api.kbars(contract, start=start, end=end, timeout=30000)
            ts = list(kb.ts)
            out: list[dict] = []
            for i in range(len(ts)):
                d = _dt.fromtimestamp(ts[i] / 1e9)
                out.append({
                    "date": d.strftime("%Y-%m-%d"),
                    "time": d.strftime("%H:%M"),
                    "minute_of_day": d.hour * 60 + d.minute,
                    "open": float(kb.Open[i]),
                    "high": float(kb.High[i]),
                    "low": float(kb.Low[i]),
                    "close": float(kb.Close[i]),
                    "volume": float(kb.Volume[i]),
                })
            out.sort(key=lambda x: (x["date"], x["time"]))
            return out
        except Exception:
            log.exception("get_kbars_range failed for %s %s~%s",
                          stock_code, start, end)
            return []

    # ---- Order ----

    def place_order(
        self,
        stock_code: str,
        action: str,          # "Buy" or "Sell"
        price: float,
        quantity: int,
        price_type: str = "LMT",    # LMT or MKT
        order_type: str = "ROD",     # ROD, IOC, FOK
        order_cond: str = "Cash",    # Cash, MarginTrading, ShortSelling
        order_lot: str = "Common",   # Common, Odd, IntradayOdd
    ) -> dict:
        """Place a stock order. Returns order result dict."""
        if not self._logged_in:
            return {"error": "尚未登入"}

        import shioaji as sj

        contract = self.get_stock_contract(stock_code)
        if not contract:
            return {"error": f"找不到股票 {stock_code}"}

        try:
            action_enum = sj.constant.Action.Buy if action == "Buy" else sj.constant.Action.Sell

            price_type_map = {
                "LMT": sj.constant.StockPriceType.LMT,
                "MKT": sj.constant.StockPriceType.MKT,
            }
            order_type_map = {
                "ROD": sj.constant.OrderType.ROD,
                "IOC": sj.constant.OrderType.IOC,
                "FOK": sj.constant.OrderType.FOK,
            }
            cond_map = {
                "Cash": sj.constant.StockOrderCond.Cash,
                "MarginTrading": sj.constant.StockOrderCond.MarginTrading,
                "ShortSelling": sj.constant.StockOrderCond.ShortSelling,
            }
            lot_map = {
                "Common": sj.constant.StockOrderLot.Common,
                "Odd": sj.constant.StockOrderLot.Odd,
                "IntradayOdd": sj.constant.StockOrderLot.IntradayOdd,
            }

            order = self._api.Order(
                action=action_enum,
                price=price,
                quantity=quantity,
                price_type=price_type_map.get(price_type, sj.constant.StockPriceType.LMT),
                order_type=order_type_map.get(order_type, sj.constant.OrderType.ROD),
                order_cond=cond_map.get(order_cond, sj.constant.StockOrderCond.Cash),
                order_lot=lot_map.get(order_lot, sj.constant.StockOrderLot.Common),
                account=self._stock_account,
            )

            trade = self._api.place_order(
                contract=contract,
                order=order,
                timeout=5000,
            )

            log.info("Order placed: %s %s %s @ %s x%d",
                     action, stock_code, price_type, price, quantity)

            lot_unit = {"Common": "張", "Odd": "股(盤後零股)",
                        "IntradayOdd": "股(盤中零股)"}
            return {
                "success": True,
                "stock_code": stock_code,
                "action": action,
                "price": price,
                "quantity": quantity,
                "unit": lot_unit.get(order_lot, "張"),
                "status": str(trade.status.status) if trade.status else "Sent",
                "order_id": trade.status.id if trade.status else "",
            }
        except Exception as e:
            log.exception("Order failed: %s", e)
            return {"error": str(e)}

    # ---- Account info ----

    def get_positions(self) -> list[dict]:
        """Get current positions (stock or futures depending on account type)."""
        if not self._logged_in:
            return []
        try:
            import shioaji as _sj
            if isinstance(self._stock_account, _sj.account.StockAccount):
                positions = self._api.list_positions(
                    account=self._stock_account,
                    timeout=10000,
                )
            else:
                positions = self._api.list_positions(
                    account=self._api.futopt_account,
                    timeout=10000,
                )
            return [
                {
                    "code": p.code,
                    "direction": str(p.direction),
                    "quantity": p.quantity,
                    "price": p.price,
                    "last_price": p.last_price,
                    "pnl": p.pnl,
                }
                for p in positions
            ]
        except Exception as e:
            log.warning("Get positions failed: %s", e)
            return []

    def get_balance(self) -> dict | None:
        """Get account balance (stock) or margin (futures)."""
        if not self._logged_in:
            return None
        try:
            import shioaji as _sj
            if isinstance(self._stock_account, _sj.account.StockAccount):
                bal = self._api.account_balance(
                    account=self._stock_account,
                    timeout=10000,
                )
                return {
                    "balance": bal.acc_balance,
                    "date": bal.date,
                    "status": bal.status,
                }
            else:
                # Futures account — use margin instead
                margin = self._api.margin(
                    account=self._stock_account,
                    timeout=10000,
                )
                return {
                    "balance": margin.available_margin,
                    "date": "",
                    "status": f"可用保證金（期貨帳戶）",
                }
        except Exception as e:
            log.warning("Get balance failed: %s", e)
            return None

    def get_open_orders(self) -> list[dict]:
        """Get today's order list. Updates status first to fetch from exchange."""
        if not self._logged_in:
            return []
        try:
            # update_status syncs order state from exchange
            self._api.update_status(timeout=10000)
            trades = self._api.list_trades()
            log.info("list_trades returned %d trades", len(trades))

            status_map = {
                "PendingSubmit": "傳送中",
                "PreSubmitted": "預約已接受",
                "Submitted": "已委託",
                "PartFilled": "部分成交",
                "Filled": "全部成交",
                "Cancelled": "已取消",
                "Failed": "委託失敗",
                "Inactive": "未啟用",
            }
            action_map = {
                "Action.Buy": "買進",
                "Action.Sell": "賣出",
            }

            lot_unit_map = {
                "Common": "張", "Odd": "股(盤後)",
                "IntradayOdd": "股(盤中)",
            }

            result = []
            for t in trades:
                raw_status = str(t.status.status) if t.status else ""
                raw_action = str(t.order.action) if t.order else ""
                deal_qty = 0
                deal_price = 0.0
                if t.status:
                    deal_qty = getattr(t.status, 'deal_quantity', 0) or 0
                    deal_price = getattr(t.status, 'modified_price', 0) or 0

                # Detect lot type
                raw_lot = str(getattr(t.order, 'order_lot', 'Common')) if t.order else "Common"
                # raw_lot may be like "StockOrderLot.Common"
                lot_key = raw_lot.split(".")[-1] if "." in raw_lot else raw_lot
                unit = lot_unit_map.get(lot_key, "張")

                result.append({
                    "code": t.contract.code if t.contract else "",
                    "name": getattr(t.contract, 'name', '') if t.contract else "",
                    "action": action_map.get(raw_action, raw_action),
                    "price": t.order.price if t.order else 0,
                    "quantity": t.order.quantity if t.order else 0,
                    "unit": unit,
                    "deal_quantity": deal_qty,
                    "deal_price": deal_price,
                    "status": status_map.get(raw_status, raw_status),
                    "status_raw": raw_status,
                    "order_id": t.status.id if t.status else "",
                    "order_time": str(getattr(t.status, 'order_datetime', ''))
                                 if t.status else "",
                })
            return result
        except Exception as e:
            log.warning("Get orders failed: %s", e)
            return []

    def get_usage(self) -> dict | None:
        """Get API usage/traffic status."""
        if not self._logged_in:
            return None
        try:
            u = self._api.usage(timeout=5000)
            return {
                "connections": u.connections,
                "bytes_used": u.bytes,
                "limit_bytes": u.limit_bytes,
                "remaining_bytes": u.remaining_bytes,
            }
        except Exception as e:
            log.warning("Get usage failed: %s", e)
            return None

    def cancel_order(self, order_id: str) -> dict:
        """Cancel an open order by order_id."""
        if not self._logged_in:
            return {"error": "尚未登入"}
        try:
            trades = self._api.list_trades()
            target = None
            for t in trades:
                if t.status and t.status.id == order_id:
                    target = t
                    break
            if not target:
                return {"error": f"找不到委託 {order_id}"}

            self._api.cancel_order(target, timeout=5000)
            log.info("Cancel order: %s", order_id)
            return {
                "success": True,
                "order_id": order_id,
                "message": "取消委託已送出",
            }
        except Exception as e:
            log.exception("Cancel order failed: %s", e)
            return {"error": str(e)}

    # ---- Order callback ----

    def add_order_callback(self, cb: Callable):
        self._order_callbacks.append(cb)

    def _on_order_event(self, stat, msg):
        log.info("Order event: %s %s", stat, msg)
        for cb in self._order_callbacks:
            try:
                cb(stat, msg)
            except Exception:
                pass
