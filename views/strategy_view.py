"""策略篩選 — screen stocks by predefined strategies."""

from __future__ import annotations

import customtkinter as ctk
from tkinter import ttk
from datetime import datetime

from viewmodels.strategy_viewmodel import StrategyViewModel


def _fmt(n: int) -> str:
    return f"{n:,}"


def _lots(shares: int) -> str:
    return _fmt(shares // 1000)


# Dark treeview style
_STYLE_INIT = False


def _ensure_style():
    global _STYLE_INIT
    if _STYLE_INIT:
        return
    _STYLE_INIT = True
    s = ttk.Style()
    name = "Strategy.Treeview"
    s.configure(name,
                background="#252526", foreground="#d4d4d4",
                fieldbackground="#252526", borderwidth=0,
                rowheight=30, font=("Microsoft JhengHei", 12))
    s.map(name,
          background=[("selected", "#264f78")],
          foreground=[("selected", "#ffffff")])
    s.configure(f"{name}.Heading",
                background="#2d2d2d", foreground="#cccccc",
                borderwidth=0, relief="flat",
                font=("Microsoft JhengHei", 12, "bold"))
    s.map(f"{name}.Heading",
          background=[("active", "#3e3e3e")])


class StrategyView(ctk.CTkFrame):
    """策略篩選 tab page."""

    def __init__(self, parent: ctk.CTkFrame, viewmodel: StrategyViewModel):
        super().__init__(parent, fg_color="transparent")
        self.vm = viewmodel
        self._tree: ttk.Treeview | None = None
        self._active_strategy = 1
        self._build_ui()
        self._bind_vm()

    def _build_ui(self):
        container = ctk.CTkScrollableFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True)

        # Title
        ctk.CTkLabel(
            container, text="策略篩選",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(pady=(24, 8))
        ctk.CTkLabel(
            container,
            text="依據預設策略條件，篩選符合條件的股票標的",
            font=ctk.CTkFont(size=14), text_color="gray",
        ).pack(pady=(0, 20))

        # ============================================================
        # Strategy 1: 自營商避險
        # ============================================================
        card = ctk.CTkFrame(container, corner_radius=12)
        card.pack(padx=40, pady=8, fill="x")

        ctk.CTkLabel(
            card, text="策略一：自營商避險買超篩選",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=24, pady=(20, 4))

        ctk.CTkLabel(
            card,
            text="篩選自營商總部（如凱基、統一等）買超佔比 ≥ X%、買超金額 ≥ Y 萬、隔日沖佔比 ≥ Z% 的標的",
            font=ctk.CTkFont(size=13), text_color="gray",
        ).pack(anchor="w", padx=24, pady=(0, 12))

        # Parameters row
        param_row = ctk.CTkFrame(card, fg_color="transparent")
        param_row.pack(fill="x", padx=24, pady=4)

        ctk.CTkLabel(param_row, text="交易日期：",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.date_entry = ctk.CTkEntry(
            param_row, width=130, font=ctk.CTkFont(size=14),
            placeholder_text="yyyy-mm-dd")
        self.date_entry.pack(side="left", padx=(4, 16))
        self.date_entry.insert(0, datetime.now().strftime("%Y-%m-%d"))

        ctk.CTkLabel(param_row, text="自營比 ≥",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.pct_entry = ctk.CTkEntry(
            param_row, width=60, font=ctk.CTkFont(size=14))
        self.pct_entry.pack(side="left", padx=(4, 2))
        self.pct_entry.insert(0, "10")
        ctk.CTkLabel(param_row, text="%",
                      font=ctk.CTkFont(size=14)).pack(side="left", padx=(0, 16))

        ctk.CTkLabel(param_row, text="買超金額 ≥",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.amt_entry = ctk.CTkEntry(
            param_row, width=80, font=ctk.CTkFont(size=14))
        self.amt_entry.pack(side="left", padx=(4, 2))
        self.amt_entry.insert(0, "1000")
        ctk.CTkLabel(param_row, text="萬",
                      font=ctk.CTkFont(size=14)).pack(side="left", padx=(0, 16))

        ctk.CTkLabel(param_row, text="隔日沖佔比 ≥",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.nd_entry = ctk.CTkEntry(
            param_row, width=60, font=ctk.CTkFont(size=14))
        self.nd_entry.pack(side="left", padx=(4, 2))
        self.nd_entry.insert(0, "10")
        ctk.CTkLabel(param_row, text="%",
                      font=ctk.CTkFont(size=14)).pack(side="left")

        # Button row
        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill="x", padx=24, pady=(8, 4))

        self.run_btn = ctk.CTkButton(
            btn_row, text="執行篩選", width=120, height=36,
            corner_radius=8,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#ab47bc", hover_color="#8e24aa",
            command=self._on_run,
        )
        self.run_btn.pack(side="left")

        self.status_label = ctk.CTkLabel(
            btn_row, text="", font=ctk.CTkFont(size=13),
            text_color="#4ECDC4")
        self.status_label.pack(side="left", padx=(12, 0))

        self.error_label = ctk.CTkLabel(
            btn_row, text="", font=ctk.CTkFont(size=13),
            text_color="#FF6B6B")
        self.error_label.pack(side="left", padx=(12, 0))

        # Results table for strategy 1
        self.result_frame = ctk.CTkFrame(card, fg_color="transparent")
        self.result_frame.pack(fill="both", expand=True, padx=16, pady=(4, 16))

        # ============================================================
        # Strategy 2: Bollinger Breakout + Dealer Buy
        # ============================================================
        card2 = ctk.CTkFrame(container, corner_radius=12)
        card2.pack(padx=40, pady=8, fill="x")

        ctk.CTkLabel(
            card2, text="策略二：布林通道突破 + 自營商買超",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=24, pady=(20, 4))

        ctk.CTkLabel(
            card2,
            text="篩選收盤價突破布林上軌（20MA+2σ）且自營商總部有買超的標的",
            font=ctk.CTkFont(size=13), text_color="gray",
        ).pack(anchor="w", padx=24, pady=(0, 12))

        param2_row = ctk.CTkFrame(card2, fg_color="transparent")
        param2_row.pack(fill="x", padx=24, pady=4)

        ctk.CTkLabel(param2_row, text="交易日期：",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.bb_date_entry = ctk.CTkEntry(
            param2_row, width=130, font=ctk.CTkFont(size=14),
            placeholder_text="yyyy-mm-dd")
        self.bb_date_entry.pack(side="left", padx=(4, 16))
        self.bb_date_entry.insert(0, datetime.now().strftime("%Y-%m-%d"))

        ctk.CTkLabel(param2_row, text="週期",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.bb_period_entry = ctk.CTkEntry(
            param2_row, width=50, font=ctk.CTkFont(size=14))
        self.bb_period_entry.pack(side="left", padx=(4, 2))
        self.bb_period_entry.insert(0, "20")

        ctk.CTkLabel(param2_row, text="倍數",
                      font=ctk.CTkFont(size=14)).pack(side="left", padx=(12, 0))
        self.bb_k_entry = ctk.CTkEntry(
            param2_row, width=50, font=ctk.CTkFont(size=14))
        self.bb_k_entry.pack(side="left", padx=(4, 0))
        self.bb_k_entry.insert(0, "2")

        btn2_row = ctk.CTkFrame(card2, fg_color="transparent")
        btn2_row.pack(fill="x", padx=24, pady=(8, 4))

        self.bb_run_btn = ctk.CTkButton(
            btn2_row, text="執行篩選", width=120, height=36,
            corner_radius=8,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#ff6d00", hover_color="#e65100",
            command=self._on_run_bb,
        )
        self.bb_run_btn.pack(side="left")

        self.bb_status_label = ctk.CTkLabel(
            btn2_row, text="", font=ctk.CTkFont(size=13),
            text_color="#4ECDC4")
        self.bb_status_label.pack(side="left", padx=(12, 0))

        self.bb_error_label = ctk.CTkLabel(
            btn2_row, text="", font=ctk.CTkFont(size=13),
            text_color="#FF6B6B")
        self.bb_error_label.pack(side="left", padx=(12, 0))

        # Results table for strategy 2
        self.bb_result_frame = ctk.CTkFrame(card2, fg_color="transparent")
        self.bb_result_frame.pack(fill="both", expand=True, padx=16, pady=(4, 16))

    # Events

    def _on_run(self):
        self._active_strategy = 1
        date = self.date_entry.get().strip()
        try:
            pct = float(self.pct_entry.get().strip())
        except ValueError:
            pct = 10.0
        try:
            amt = float(self.amt_entry.get().strip()) * 10000  # 萬 → 元
        except ValueError:
            amt = 10_000_000
        try:
            nd_pct = float(self.nd_entry.get().strip())
        except ValueError:
            nd_pct = 10.0
        self.vm.run_dealer_hedge_strategy(date, pct, amt, nd_pct)

    def _on_run_bb(self):
        self._active_strategy = 2
        date = self.bb_date_entry.get().strip()
        try:
            period = int(self.bb_period_entry.get().strip())
        except ValueError:
            period = 20
        try:
            k = float(self.bb_k_entry.get().strip())
        except ValueError:
            k = 2.0
        self.vm.run_bollinger_strategy(date, period, k)

    # Bindings

    def _bind_vm(self):
        self.vm.bind("results", self._on_results)
        self.vm.bind("loading", self._on_loading)
        self.vm.bind("error_text", self._on_error)
        self.vm.bind("status_text", self._on_status)

    def _on_loading(self, v):
        def _u():
            if v:
                if self._active_strategy == 1:
                    self.run_btn.configure(state="disabled", text="篩選中...")
                else:
                    self.bb_run_btn.configure(state="disabled", text="篩選中...")
            else:
                self.run_btn.configure(state="normal", text="執行篩選")
                self.bb_run_btn.configure(state="normal", text="執行篩選")
        self.after(0, _u)

    def _on_error(self, v):
        def _u():
            if self._active_strategy == 1:
                self.error_label.configure(text=v)
            else:
                self.bb_error_label.configure(text=v)
        self.after(0, _u)

    def _on_status(self, v):
        def _u():
            if self._active_strategy == 1:
                self.status_label.configure(text=v)
            else:
                self.bb_status_label.configure(text=v)
        self.after(0, _u)

    def _on_results(self, data):
        def _u():
            if self._active_strategy == 1:
                self._render_strategy1(data)
            else:
                self._render_strategy2(data)
        self.after(0, _u)

    def _render_strategy1(self, data):
        for w in self.result_frame.winfo_children():
            w.destroy()
        self._tree = None

        if data is None or len(data) == 0:
            return

        _ensure_style()

        columns = ("rank", "code", "name", "price",
                   "dealer_pct", "nd_pct", "dealer_buy", "buy_amt")
        tree = ttk.Treeview(
            self.result_frame, columns=columns, show="headings",
            style="Strategy.Treeview", height=min(len(data), 20))

        headings = {
            "rank": "#", "code": "代碼", "name": "名稱",
            "price": "收盤",
            "dealer_pct": "自營比%", "nd_pct": "隔沖%",
            "dealer_buy": "自營買(張)", "buy_amt": "買超金額(萬)",
        }
        widths = {
            "rank": 35, "code": 60, "name": 90, "price": 70,
            "dealer_pct": 70, "nd_pct": 60,
            "dealer_buy": 85, "buy_amt": 100,
        }
        anchors = {
            "rank": "center", "code": "center", "name": "w",
            "price": "e", "dealer_pct": "e", "nd_pct": "e",
            "dealer_buy": "e", "buy_amt": "e",
        }

        for c in columns:
            tree.heading(c, text=headings[c])
            tree.column(c, width=widths[c], anchor=anchors[c], stretch=True)

        tree.tag_configure("hot", foreground="#ab47bc")
        tree.tag_configure("buy", foreground="#ef5350")

        for i, r in enumerate(data, 1):
            d_pct = r.get("dealer_pct", 0)
            nd_pct = r.get("next_day_pct", 0)
            tag = "hot" if d_pct >= 20 else "buy"
            tree.insert("", "end", values=(
                i, r["stock_code"], r["stock_name"],
                f"{r['close_price']:,.2f}",
                f"{d_pct:.1f}", f"{nd_pct:.1f}",
                _lots(r.get("dealer_buy", 0)),
                f"{r.get('dealer_buy_amount', 0) / 10000:,.0f}",
            ), tags=(tag,))

        sb = ttk.Scrollbar(
            self.result_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True,
                  padx=(4, 0), pady=4)
        sb.pack(side="right", fill="y", padx=(0, 4), pady=4)
        self._tree = tree

    def _render_strategy2(self, data):
        for w in self.bb_result_frame.winfo_children():
            w.destroy()

        if data is None or len(data) == 0:
            return

        _ensure_style()

        columns = ("rank", "code", "name", "price",
                   "bb_upper", "bb_diff", "dealer_pct", "dealer_buy")
        tree = ttk.Treeview(
            self.bb_result_frame, columns=columns, show="headings",
            style="Strategy.Treeview", height=min(len(data), 20))

        headings = {
            "rank": "#", "code": "代碼", "name": "名稱",
            "price": "收盤", "bb_upper": "BB上軌",
            "bb_diff": "突破%", "dealer_pct": "自營比%",
            "dealer_buy": "自營買(張)",
        }
        widths = {
            "rank": 35, "code": 60, "name": 90, "price": 70,
            "bb_upper": 70, "bb_diff": 65,
            "dealer_pct": 70, "dealer_buy": 85,
        }
        anchors = {
            "rank": "center", "code": "center", "name": "w",
            "price": "e", "bb_upper": "e", "bb_diff": "e",
            "dealer_pct": "e", "dealer_buy": "e",
        }

        for c in columns:
            tree.heading(c, text=headings[c])
            tree.column(c, width=widths[c], anchor=anchors[c], stretch=True)

        tree.tag_configure("hot", foreground="#ff6d00")
        tree.tag_configure("strong", foreground="#ef5350")

        for i, r in enumerate(data, 1):
            diff = r.get("bb_diff_pct", 0)
            tag = "hot" if diff >= 3 else "strong"
            tree.insert("", "end", values=(
                i, r["stock_code"], r["stock_name"],
                f"{r['close_price']:,.2f}",
                f"{r.get('bb_upper', 0):,.2f}",
                f"+{diff:.1f}",
                f"{r.get('dealer_pct', 0):.1f}",
                _lots(r.get("dealer_buy", 0)),
            ), tags=(tag,))

        sb = ttk.Scrollbar(
            self.bb_result_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True,
                  padx=(4, 0), pady=4)
        sb.pack(side="right", fill="y", padx=(0, 4), pady=4)
