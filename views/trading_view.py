"""下單 — Shioaji trading interface."""

from __future__ import annotations

import customtkinter as ctk
from tkinter import ttk, messagebox

from viewmodels.trading_viewmodel import TradingViewModel


def _fmt(n) -> str:
    try:
        return f"{int(n):,}"
    except (ValueError, TypeError):
        return str(n)


class TradingView(ctk.CTkFrame):
    """下單 tab page."""

    def __init__(self, parent: ctk.CTkFrame, viewmodel: TradingViewModel):
        super().__init__(parent, fg_color="transparent")
        self.vm = viewmodel
        self._pos_tree: ttk.Treeview | None = None
        self._ord_tree: ttk.Treeview | None = None
        self._build_ui()
        self._bind_vm()

    def _build_ui(self):
        container = ctk.CTkScrollableFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True)

        # Title
        ctk.CTkLabel(
            container, text="下單",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(pady=(24, 8))
        self.warning_label = ctk.CTkLabel(
            container, text="永豐金 Shioaji 證券下單（測試環境）",
            font=ctk.CTkFont(size=14), text_color="#4ECDC4",
        )
        self.warning_label.pack(pady=(0, 20))

        # ============================================================
        # Login card
        # ============================================================
        login_card = ctk.CTkFrame(container, corner_radius=12)
        login_card.pack(padx=40, pady=8, fill="x")

        env_row = ctk.CTkFrame(login_card, fg_color="transparent")
        env_row.pack(fill="x", padx=24, pady=(20, 4))

        ctk.CTkLabel(
            env_row, text="帳號連線",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(side="left")

        self.env_var = ctk.StringVar(value="測試環境")
        self.env_seg = ctk.CTkSegmentedButton(
            env_row, values=["測試環境", "正式環境"],
            variable=self.env_var,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._on_env_change,
        )
        self.env_seg.pack(side="left", padx=(16, 0))

        self.env_hint = ctk.CTkLabel(
            env_row, text="模擬交易，不會實際下單",
            font=ctk.CTkFont(size=12), text_color="#4ECDC4")
        self.env_hint.pack(side="left", padx=(12, 0))

        key_row = ctk.CTkFrame(login_card, fg_color="transparent")
        key_row.pack(fill="x", padx=24, pady=4)
        ctk.CTkLabel(key_row, text="API Key：",
                      font=ctk.CTkFont(size=13)).pack(side="left")
        self.api_key_entry = ctk.CTkEntry(
            key_row, width=320, font=ctk.CTkFont(size=13), show="*")
        self.api_key_entry.pack(side="left", padx=(4, 0))
        # Pre-fill from config
        saved_key = self.vm._config.get("shioaji_api_key") or ""
        if saved_key:
            self.api_key_entry.insert(0, saved_key)

        secret_row = ctk.CTkFrame(login_card, fg_color="transparent")
        secret_row.pack(fill="x", padx=24, pady=4)
        ctk.CTkLabel(secret_row, text="Secret：  ",
                      font=ctk.CTkFont(size=13)).pack(side="left")
        self.secret_entry = ctk.CTkEntry(
            secret_row, width=320, font=ctk.CTkFont(size=13), show="*")
        self.secret_entry.pack(side="left", padx=(4, 0))
        saved_secret = self.vm._config.get("shioaji_secret_key") or ""
        if saved_secret:
            self.secret_entry.insert(0, saved_secret)

        pid_row = ctk.CTkFrame(login_card, fg_color="transparent")
        pid_row.pack(fill="x", padx=24, pady=4)
        ctk.CTkLabel(pid_row, text="身分證號：",
                      font=ctk.CTkFont(size=13)).pack(side="left")
        self.person_id_entry = ctk.CTkEntry(
            pid_row, width=150, font=ctk.CTkFont(size=13))
        self.person_id_entry.pack(side="left", padx=(4, 16))
        saved_pid = self.vm._config.get("shioaji_person_id") or ""
        if saved_pid:
            self.person_id_entry.insert(0, saved_pid)

        ctk.CTkLabel(pid_row, text="憑證密碼：",
                      font=ctk.CTkFont(size=13)).pack(side="left")
        self.ca_passwd_entry = ctk.CTkEntry(
            pid_row, width=150, font=ctk.CTkFont(size=13), show="*")
        self.ca_passwd_entry.pack(side="left", padx=(4, 0))

        btn_row = ctk.CTkFrame(login_card, fg_color="transparent")
        btn_row.pack(fill="x", padx=24, pady=(8, 4))

        self.login_btn = ctk.CTkButton(
            btn_row, text="登入", width=100, height=34,
            corner_radius=8, font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#1f6aa5", hover_color="#185a8c",
            command=self._on_login)
        self.login_btn.pack(side="left")

        self.logout_btn = ctk.CTkButton(
            btn_row, text="登出", width=80, height=34,
            corner_radius=8, font=ctk.CTkFont(size=13),
            fg_color="#555", hover_color="#777",
            command=self._on_logout, state="disabled")
        self.logout_btn.pack(side="left", padx=(8, 0))

        self.login_status_label = ctk.CTkLabel(
            btn_row, text="", font=ctk.CTkFont(size=13),
            text_color="#4ECDC4")
        self.login_status_label.pack(side="left", padx=(16, 0))

        ctk.CTkFrame(login_card, height=12, fg_color="transparent").pack()

        # ============================================================
        # Order card
        # ============================================================
        order_card = ctk.CTkFrame(container, corner_radius=12)
        order_card.pack(padx=40, pady=8, fill="x")

        ctk.CTkLabel(
            order_card, text="委託下單",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=24, pady=(20, 8))

        # Stock code + snapshot
        snap_row = ctk.CTkFrame(order_card, fg_color="transparent")
        snap_row.pack(fill="x", padx=24, pady=4)
        ctk.CTkLabel(snap_row, text="股票代碼：",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.stock_entry = ctk.CTkEntry(
            snap_row, width=100, font=ctk.CTkFont(size=14))
        self.stock_entry.pack(side="left", padx=(4, 8))
        ctk.CTkButton(
            snap_row, text="查詢報價", width=90, height=30,
            corner_radius=6, font=ctk.CTkFont(size=13),
            command=self._on_query_snap,
        ).pack(side="left")
        self.snap_label = ctk.CTkLabel(
            snap_row, text="", font=ctk.CTkFont(size=13),
            text_color="#c0c0c0")
        self.snap_label.pack(side="left", padx=(12, 0))

        # Action row
        action_row = ctk.CTkFrame(order_card, fg_color="transparent")
        action_row.pack(fill="x", padx=24, pady=4)

        ctk.CTkLabel(action_row, text="買賣：",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.action_var = ctk.StringVar(value="買進")
        self.action_seg = ctk.CTkSegmentedButton(
            action_row, values=["買進", "賣出"],
            variable=self.action_var,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._on_action_change,
        )
        self.action_seg.pack(side="left", padx=(4, 16))

        ctk.CTkLabel(action_row, text="價格：",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.price_entry = ctk.CTkEntry(
            action_row, width=90, font=ctk.CTkFont(size=14))
        self.price_entry.pack(side="left", padx=(4, 16))

        self.qty_label = ctk.CTkLabel(action_row, text="張數：",
                      font=ctk.CTkFont(size=14))
        self.qty_label.pack(side="left")
        self.qty_entry = ctk.CTkEntry(
            action_row, width=60, font=ctk.CTkFont(size=14))
        self.qty_entry.pack(side="left", padx=(4, 0))
        self.qty_entry.insert(0, "1")

        # Order type row
        type_row = ctk.CTkFrame(order_card, fg_color="transparent")
        type_row.pack(fill="x", padx=24, pady=4)

        ctk.CTkLabel(type_row, text="價格類型：",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.price_type_var = ctk.StringVar(value="限價")
        ctk.CTkSegmentedButton(
            type_row, values=["限價", "市價"],
            variable=self.price_type_var,
            font=ctk.CTkFont(size=12),
        ).pack(side="left", padx=(4, 16))

        ctk.CTkLabel(type_row, text="有效條件：",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.order_type_var = ctk.StringVar(value="當日有效")
        ctk.CTkSegmentedButton(
            type_row, values=["當日有效", "立即成交", "全部成交"],
            variable=self.order_type_var,
            font=ctk.CTkFont(size=12),
        ).pack(side="left", padx=(4, 16))

        ctk.CTkLabel(type_row, text="交易類別：",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.cond_var = ctk.StringVar(value="現股")
        self.cond_menu = ctk.CTkOptionMenu(
            type_row, values=["現股", "融資", "融券"],
            variable=self.cond_var,
            font=ctk.CTkFont(size=12), width=90,
        )
        self.cond_menu.pack(side="left", padx=(4, 16))

        ctk.CTkLabel(type_row, text="單位：",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.lot_var = ctk.StringVar(value="整股(張)")
        self.lot_menu = ctk.CTkOptionMenu(
            type_row, values=["整股(張)", "盤中零股", "盤後零股"],
            variable=self.lot_var,
            font=ctk.CTkFont(size=12), width=110,
        )
        self.lot_menu.pack(side="left", padx=(4, 0))

        # Submit button
        submit_row = ctk.CTkFrame(order_card, fg_color="transparent")
        submit_row.pack(fill="x", padx=24, pady=(10, 4))

        self.buy_btn = ctk.CTkButton(
            submit_row, text="買進下單", width=130, height=40,
            corner_radius=8,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color="#ef5350", hover_color="#c62828",
            command=lambda: self._on_submit("買進"))
        self.buy_btn.pack(side="left", padx=(0, 8))

        self.sell_btn = ctk.CTkButton(
            submit_row, text="賣出下單", width=130, height=40,
            corner_radius=8,
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color="#26a69a", hover_color="#00897b",
            command=lambda: self._on_submit("賣出"))
        self.sell_btn.pack(side="left")

        self.order_status_label = ctk.CTkLabel(
            submit_row, text="", font=ctk.CTkFont(size=13))
        self.order_status_label.pack(side="left", padx=(16, 0))

        ctk.CTkFrame(order_card, height=12, fg_color="transparent").pack()

        # ============================================================
        # Positions + Orders card
        # ============================================================
        acct_card = ctk.CTkFrame(container, corner_radius=12)
        acct_card.pack(padx=40, pady=8, fill="x")

        acct_hdr = ctk.CTkFrame(acct_card, fg_color="transparent")
        acct_hdr.pack(fill="x", padx=24, pady=(20, 8))
        ctk.CTkLabel(
            acct_hdr, text="帳務資訊",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(side="left")
        ctk.CTkButton(
            acct_hdr, text="重新整理", width=90, height=30,
            corner_radius=6, font=ctk.CTkFont(size=13),
            command=self._on_refresh_acct,
        ).pack(side="right")

        self.balance_label = ctk.CTkLabel(
            acct_card, text="", font=ctk.CTkFont(size=13),
            text_color="#c0c0c0")
        self.balance_label.pack(anchor="w", padx=24, pady=(0, 8))

        # Positions table
        ctk.CTkLabel(
            acct_card, text="持倉部位",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=24, pady=(4, 2))

        self.pos_frame = ctk.CTkFrame(acct_card, fg_color="transparent")
        self.pos_frame.pack(fill="x", padx=16, pady=(0, 8))

        # Today's orders table
        ord_hdr = ctk.CTkFrame(acct_card, fg_color="transparent")
        ord_hdr.pack(fill="x", padx=24, pady=(4, 2))
        ctk.CTkLabel(
            ord_hdr, text="今日委託",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(side="left")
        self.cancel_btn = ctk.CTkButton(
            ord_hdr, text="取消選取委託", width=120, height=28,
            corner_radius=6, font=ctk.CTkFont(size=12),
            fg_color="#ef5350", hover_color="#c62828",
            command=self._on_cancel_order,
        )
        self.cancel_btn.pack(side="right")

        self.ord_frame = ctk.CTkFrame(acct_card, fg_color="transparent")
        self.ord_frame.pack(fill="x", padx=16, pady=(0, 8))

        ctk.CTkFrame(acct_card, height=12, fg_color="transparent").pack()

        # ============================================================
        # Event log card
        # ============================================================
        log_card = ctk.CTkFrame(container, corner_radius=12)
        log_card.pack(padx=40, pady=8, fill="x")

        ctk.CTkLabel(
            log_card, text="委託回報",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=24, pady=(16, 4))

        self.log_textbox = ctk.CTkTextbox(
            log_card, height=150,
            font=ctk.CTkFont(size=12, family="Consolas"),
            state="disabled")
        self.log_textbox.pack(fill="x", padx=20, pady=(0, 16))

    # ================================================================ Events

    def _on_env_change(self, val):
        if val == "測試環境":
            self.env_hint.configure(text="模擬交易，不會實際下單", text_color="#4ECDC4")
            self.warning_label.configure(
                text="永豐金 Shioaji 證券下單（測試環境）", text_color="#4ECDC4")
        else:
            self.env_hint.configure(text="⚠ 真實交易，請謹慎操作！", text_color="#FF6B6B")
            self.warning_label.configure(
                text="永豐金 Shioaji 證券下單（正式環境，實際交易）", text_color="#FF6B6B")

    def _on_login(self):
        simulation = self.env_var.get() == "測試環境"
        self.vm.login(
            self.api_key_entry.get().strip(),
            self.secret_entry.get().strip(),
            self.person_id_entry.get().strip(),
            self.ca_passwd_entry.get().strip(),
            simulation=simulation,
        )

    def _on_logout(self):
        self.vm.logout()

    def _on_action_change(self, val):
        self.action_var.set(val)

    def _on_query_snap(self):
        self.vm.query_snapshot(self.stock_entry.get().strip())

    # Chinese → API value mappings
    _ACTION_MAP = {"買進": "Buy", "賣出": "Sell"}
    _PRICE_TYPE_MAP = {"限價": "LMT", "市價": "MKT"}
    _ORDER_TYPE_MAP = {"當日有效": "ROD", "立即成交": "IOC", "全部成交": "FOK"}
    _COND_MAP = {"現股": "Cash", "融資": "MarginTrading", "融券": "ShortSelling"}
    _LOT_MAP = {"整股(張)": "Common", "盤中零股": "IntradayOdd", "盤後零股": "Odd"}
    _LOT_UNIT = {"整股(張)": "張", "盤中零股": "股(盤中零股)", "盤後零股": "股(盤後零股)"}

    def _on_submit(self, action_zh: str):
        code = self.stock_entry.get().strip()
        if not code:
            self.order_status_label.configure(
                text="請輸入股票代碼", text_color="#FF6B6B")
            return

        price_type_zh = self.price_type_var.get()
        try:
            price = float(self.price_entry.get().strip())
        except ValueError:
            if price_type_zh == "市價":
                price = 0
            else:
                self.order_status_label.configure(
                    text="請輸入價格", text_color="#FF6B6B")
                return
        try:
            qty = int(self.qty_entry.get().strip())
        except ValueError:
            self.order_status_label.configure(
                text="請輸入數量", text_color="#FF6B6B")
            return
        cond_zh = self.cond_var.get()
        order_type_zh = self.order_type_var.get()
        lot_zh = self.lot_var.get()
        lot_unit = self._LOT_UNIT.get(lot_zh, "張")

        confirm = messagebox.askyesno(
            "確認下單",
            f"確定要 {action_zh} {code} {qty} {lot_unit} @ {price}？\n"
            f"類型：{price_type_zh} / {order_type_zh} / {cond_zh}\n\n"
            f"⚠ 這是實際交易，請確認！",
        )
        if not confirm:
            return

        self.vm.place_order(
            code,
            self._ACTION_MAP.get(action_zh, "Buy"),
            price, qty,
            self._PRICE_TYPE_MAP.get(price_type_zh, "LMT"),
            self._ORDER_TYPE_MAP.get(order_type_zh, "ROD"),
            self._COND_MAP.get(cond_zh, "Cash"),
            self._LOT_MAP.get(lot_zh, "Common"),
        )

    def _on_cancel_order(self):
        if not self._ord_tree:
            return
        sel = self._ord_tree.selection()
        if not sel:
            self.order_status_label.configure(
                text="請先在委託列表選取要取消的委託", text_color="#FF6B6B")
            return
        item = self._ord_tree.item(sel[0])
        vals = item["values"]
        # vals: (code, name, action, price, qty, deal_qty, status, time)
        code = vals[0]
        status = vals[6]

        # Find order_id from stored data
        if not self.vm.orders_data:
            return
        order_id = None
        for o in self.vm.orders_data:
            if o.get("code") == str(code) and o.get("status") == str(status):
                order_id = o.get("order_id")
                break
        if not order_id:
            self.order_status_label.configure(
                text="找不到該筆委託", text_color="#FF6B6B")
            return

        if status in ("全部成交", "已取消", "委託失敗"):
            self.order_status_label.configure(
                text=f"委託已{status}，無法取消", text_color="#888888")
            return

        confirm = messagebox.askyesno(
            "確認取消",
            f"確定要取消 {vals[2]} {code} {vals[1]} "
            f"{vals[4]}張 @ {vals[3]} 的委託？",
        )
        if confirm:
            self.vm.cancel_order(order_id)

    def _on_refresh_acct(self):
        self.vm.refresh_positions()
        self.vm.refresh_orders()

    # ================================================================ Bindings

    def _bind_vm(self):
        self.vm.bind("login_status", self._on_login_status)
        self.vm.bind("is_logged_in", self._on_logged_in)
        self.vm.bind("is_logging_in", self._on_logging_in)
        self.vm.bind("snapshot_data", self._on_snapshot)
        self.vm.bind("snapshot_error", self._on_snap_error)
        self.vm.bind("order_result", self._on_order_result)
        self.vm.bind("order_error", self._on_order_error)
        self.vm.bind("positions_data", self._on_positions)
        self.vm.bind("balance_data", self._on_balance)
        self.vm.bind("orders_data", self._on_orders)
        self.vm.bind("event_log", self._on_event_log)

    def _on_login_status(self, v):
        self.after(0, lambda: self.login_status_label.configure(text=v))

    def _on_logged_in(self, v):
        def _u():
            if v:
                self.login_btn.configure(state="disabled")
                self.logout_btn.configure(state="normal")
            else:
                self.login_btn.configure(state="normal")
                self.logout_btn.configure(state="disabled")
        self.after(0, _u)

    def _on_logging_in(self, v):
        def _u():
            if v:
                self.login_btn.configure(state="disabled", text="連線中...")
            else:
                self.login_btn.configure(text="登入")
                if not self.vm.is_logged_in:
                    self.login_btn.configure(state="normal")
        self.after(0, _u)

    def _on_snapshot(self, data):
        def _u():
            if not data:
                return
            chg = data.get("change_price", 0)
            chg_s = f"+{chg:.2f}" if chg >= 0 else f"{chg:.2f}"
            clr = "#ef5350" if chg >= 0 else "#26a69a"
            txt = (f"現價 {data['close']:.2f} ({chg_s})　"
                   f"量 {_fmt(data.get('total_volume', 0))}　"
                   f"買 {data.get('buy_price', '')}　"
                   f"賣 {data.get('sell_price', '')}")
            self.snap_label.configure(text=txt, text_color=clr)
            # Auto-fill price
            if not self.price_entry.get().strip():
                self.price_entry.delete(0, "end")
                self.price_entry.insert(0, f"{data['close']:.2f}")
        self.after(0, _u)

    def _on_snap_error(self, v):
        self.after(0, lambda: self.snap_label.configure(
            text=v, text_color="#FF6B6B"))

    def _on_order_result(self, data):
        def _u():
            if not data:
                return
            if "stock_code" in data:
                action = "買進" if data.get("action") == "Buy" else "賣出"
                unit = data.get("unit", "張")
                self.order_status_label.configure(
                    text=f"✓ {action} {data['stock_code']} "
                         f"{data['quantity']}{unit} → {data.get('status', '已送出')}",
                    text_color="#4ECDC4",
                )
            else:
                self.order_status_label.configure(
                    text=f"✓ {data.get('message', '操作完成')}",
                    text_color="#4ECDC4",
                )
        self.after(0, _u)

    def _on_order_error(self, v):
        self.after(0, lambda: self.order_status_label.configure(
            text=v, text_color="#FF6B6B"))

    def _on_balance(self, data):
        def _u():
            if not data:
                self.balance_label.configure(text="")
                return
            bal = data.get("balance", 0)
            self.balance_label.configure(
                text=f"帳戶餘額：{_fmt(bal)} 元　日期：{data.get('date', '')}")
        self.after(0, _u)

    def _on_positions(self, data):
        def _u():
            for w in self.pos_frame.winfo_children():
                w.destroy()
            if not data:
                ctk.CTkLabel(
                    self.pos_frame, text="（無持倉）",
                    font=ctk.CTkFont(size=13), text_color="gray",
                ).pack(pady=4)
                return

            columns = ("code", "dir", "qty", "cost", "last", "pnl")
            tree = ttk.Treeview(
                self.pos_frame, columns=columns, show="headings",
                height=min(len(data), 10))
            for c, txt, w, anc in [
                ("code", "代碼", 60, "center"),
                ("dir", "方向", 50, "center"),
                ("qty", "張數", 50, "e"),
                ("cost", "成本", 70, "e"),
                ("last", "現價", 70, "e"),
                ("pnl", "損益", 80, "e"),
            ]:
                tree.heading(c, text=txt)
                tree.column(c, width=w, anchor=anc, stretch=True)

            tree.tag_configure("profit", foreground="#ef5350")
            tree.tag_configure("loss", foreground="#26a69a")

            for p in data:
                pnl = p.get("pnl", 0)
                tag = "profit" if pnl >= 0 else "loss"
                tree.insert("", "end", values=(
                    p.get("code", ""),
                    "做多" if "Buy" in str(p.get("direction", "")) else "做空",
                    p.get("quantity", 0),
                    f"{p.get('price', 0):,.2f}",
                    f"{p.get('last_price', 0):,.2f}",
                    f"{pnl:+,.0f}",
                ), tags=(tag,))

            tree.pack(fill="x", padx=8, pady=4)
        self.after(0, _u)

    def _on_orders(self, data):
        def _u():
            for w in self.ord_frame.winfo_children():
                w.destroy()
            if not data:
                ctk.CTkLabel(
                    self.ord_frame, text="（無委託）",
                    font=ctk.CTkFont(size=13), text_color="gray",
                ).pack(pady=4)
                return

            columns = ("code", "name", "action", "price", "qty",
                       "deal_qty", "status", "time")
            tree = ttk.Treeview(
                self.ord_frame, columns=columns, show="headings",
                height=min(len(data), 12))
            for c, txt, w, anc in [
                ("code", "代碼", 55, "center"),
                ("name", "名稱", 70, "w"),
                ("action", "買賣", 45, "center"),
                ("price", "委託價", 65, "e"),
                ("qty", "委託量", 55, "e"),
                ("deal_qty", "成交量", 55, "e"),
                ("status", "狀態", 80, "center"),
                ("time", "時間", 130, "w"),
            ]:
                tree.heading(c, text=txt)
                tree.column(c, width=w, anchor=anc, stretch=True)

            tree.tag_configure("filled", foreground="#4ECDC4")
            tree.tag_configure("cancelled", foreground="#888888")
            tree.tag_configure("failed", foreground="#FF6B6B")
            tree.tag_configure("pending", foreground="#ffeb3b")

            for o in data:
                status = o.get("status", "")
                raw = o.get("status_raw", "")
                if raw == "Filled":
                    tag = "filled"
                elif raw in ("Cancelled", "Failed"):
                    tag = "cancelled" if raw == "Cancelled" else "failed"
                else:
                    tag = "pending"

                unit = o.get("unit", "張")
                qty_txt = f"{o.get('quantity', 0)}{unit}"
                deal_txt = f"{o.get('deal_quantity', 0)}{unit}"
                tree.insert("", "end", values=(
                    o.get("code", ""),
                    o.get("name", ""),
                    o.get("action", ""),
                    o.get("price", 0),
                    qty_txt,
                    deal_txt,
                    status,
                    o.get("order_time", "")[:19],
                ), tags=(tag,))

            sb = ttk.Scrollbar(
                self.ord_frame, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=sb.set)
            tree.pack(side="left", fill="x", expand=True, padx=(8, 0), pady=4)
            sb.pack(side="right", fill="y", padx=(0, 8), pady=4)
            self._ord_tree = tree
        self.after(0, _u)

    def _on_event_log(self, v):
        def _u():
            self.log_textbox.configure(state="normal")
            self.log_textbox.delete("1.0", "end")
            self.log_textbox.insert("1.0", v or "")
            self.log_textbox.see("end")
            self.log_textbox.configure(state="disabled")
        self.after(0, _u)
