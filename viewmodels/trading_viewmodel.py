"""ViewModel for the trading (下單) tab."""

from __future__ import annotations

import threading

from viewmodels.base_viewmodel import BaseViewModel, ObservableProperty
from services.shioaji_service import ShioajiService
from services.config_service import ConfigService


class TradingViewModel(BaseViewModel):

    # Connection
    login_status = ObservableProperty("")
    is_logged_in = ObservableProperty(False)
    is_logging_in = ObservableProperty(False)

    # Snapshot
    snapshot_data = ObservableProperty(None)   # dict | None
    snapshot_error = ObservableProperty("")

    # Order
    order_result = ObservableProperty(None)    # dict | None
    order_error = ObservableProperty("")

    # Positions
    positions_data = ObservableProperty(None)  # list[dict] | None
    balance_data = ObservableProperty(None)    # dict | None

    # Orders list
    orders_data = ObservableProperty(None)     # list[dict] | None

    # Order event log
    event_log = ObservableProperty("")

    def __init__(self, config: ConfigService):
        super().__init__()
        self._config = config
        self._sj = ShioajiService()

    def login(self, api_key: str = "", secret_key: str = "",
              person_id: str = "", ca_passwd: str = "",
              simulation: bool = True):
        """Login to Shioaji. Uses config values if not provided."""
        if self.is_logging_in or self.is_logged_in:
            return
        api_key = api_key.strip() or self._config.get("shioaji_api_key") or ""
        secret_key = secret_key.strip() or self._config.get("shioaji_secret_key") or ""
        person_id = person_id.strip() or self._config.get("shioaji_person_id") or ""
        if not api_key or not secret_key:
            self.login_status = "請輸入 API Key 和 Secret Key"
            return

        # Save to config (not ca_passwd for security)
        self._config.set("shioaji_api_key", api_key)
        self._config.set("shioaji_secret_key", secret_key)
        if person_id:
            self._config.set("shioaji_person_id", person_id)

        self.is_logging_in = True
        self.login_status = "連線中..."

        def _status(msg: str):
            self.login_status = msg

        def _work():
            try:
                self._sj.add_order_callback(self._on_order_event)
                ok = self._sj.login(
                    api_key, secret_key,
                    person_id=person_id, ca_passwd=ca_passwd,
                    simulation=simulation,
                    on_status=_status,
                )
                self.is_logged_in = ok
            except Exception as e:
                self.login_status = f"登入失敗：{e}"
            finally:
                self.is_logging_in = False

        threading.Thread(target=_work, daemon=True).start()

    def logout(self):
        self._sj.logout()
        self.is_logged_in = False
        self.login_status = "已登出"

    # ---- Snapshot ----

    def query_snapshot(self, stock_code: str):
        stock_code = stock_code.strip()
        if not stock_code:
            self.snapshot_error = "請輸入股票代碼"
            return
        self.snapshot_error = ""
        self.snapshot_data = None

        def _work():
            snap = self._sj.get_snapshot(stock_code)
            if snap:
                self.snapshot_data = snap
            else:
                self.snapshot_error = f"查無 {stock_code} 報價"

        threading.Thread(target=_work, daemon=True).start()

    # ---- Place order ----

    def place_order(
        self, stock_code: str, action: str, price: float,
        quantity: int, price_type: str, order_type: str,
        order_cond: str, order_lot: str = "Common",
    ):
        self.order_error = ""
        self.order_result = None

        def _work():
            result = self._sj.place_order(
                stock_code, action, price, quantity,
                price_type, order_type, order_cond, order_lot,
            )
            if "error" in result:
                self.order_error = result["error"]
            else:
                self.order_result = result
                unit = result.get("unit", "張")
                self._log(
                    f"{'買進' if action == 'Buy' else '賣出'} "
                    f"{stock_code} {quantity}{unit} @ {price} "
                    f"→ {result.get('status', '已送出')}\n"
                )

        threading.Thread(target=_work, daemon=True).start()

    # ---- Cancel order ----

    def cancel_order(self, order_id: str):
        self.order_error = ""
        self.order_result = None

        def _work():
            result = self._sj.cancel_order(order_id)
            if "error" in result:
                self.order_error = result["error"]
            else:
                self.order_result = result
                self._log(f"取消委託 {order_id} → {result.get('message', '已送出')}\n")
                # Auto refresh orders
                self.refresh_orders()

        threading.Thread(target=_work, daemon=True).start()

    # ---- Account ----

    def refresh_positions(self):
        def _work():
            self.positions_data = self._sj.get_positions()
            self.balance_data = self._sj.get_balance()
        threading.Thread(target=_work, daemon=True).start()

    def refresh_orders(self):
        def _work():
            self.orders_data = self._sj.get_open_orders()
        threading.Thread(target=_work, daemon=True).start()

    # ---- Event log ----

    def _on_order_event(self, stat, msg):
        self._log(f"[{stat}] {msg}\n")

    def _log(self, text: str):
        self.event_log = (self.event_log or "") + text

    def shutdown(self):
        self._sj.logout()
