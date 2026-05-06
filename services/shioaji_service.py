"""Shioaji trading service — wraps Sinopac securities API."""

from __future__ import annotations

import logging
import threading
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
                self._api.logout()
            except Exception:
                pass
        self._logged_in = False
        self._api = None

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
