"""分點分析 — search stocks, view broker positions, drill into daily charts."""

from __future__ import annotations

import customtkinter as ctk
from tkinter import ttk
import tkinter as tk
from datetime import datetime

from viewmodels.broker_analysis_viewmodel import BrokerAnalysisViewModel

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.ticker as mticker
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    matplotlib.rcParams["font.sans-serif"] = [
        "Microsoft JhengHei", "Microsoft YaHei", "SimHei", "sans-serif",
    ]
    matplotlib.rcParams["axes.unicode_minus"] = False
    HAS_MPL = True
except ImportError:
    HAS_MPL = False


def _parse_price(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").replace(" ", ""))
    except (ValueError, TypeError):
        return None


def _fmt(n: int) -> str:
    return f"{n:,}"


# =====================================================================
# View
# =====================================================================

class BrokerAnalysisView(ctk.CTkFrame):
    """分點分析 tab page."""

    def __init__(self, parent: ctk.CTkFrame, viewmodel: BrokerAnalysisViewModel):
        super().__init__(parent, fg_color="transparent")
        self.vm = viewmodel
        self._chart_canvas: FigureCanvasTkAgg | None = None
        self._chart_fig: Figure | None = None
        self._chart_data: dict | None = None
        self._rank_buyers: list[dict] = []
        self._rank_sellers: list[dict] = []
        self._build_ui()
        self._bind_vm()

    # ================================================================ UI
    def _build_ui(self):
        self.container = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.container.pack(fill="both", expand=True)

        # --- Title ---
        ctk.CTkLabel(
            self.container, text="分點分析",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(pady=(24, 8))
        ctk.CTkLabel(
            self.container,
            text="搜尋股票，檢視各分點買賣超，點選分點查看詳細走勢圖",
            font=ctk.CTkFont(size=13), text_color="gray",
        ).pack(pady=(0, 24))

        # --- Search card ---
        search_card = ctk.CTkFrame(self.container, corner_radius=12)
        search_card.pack(padx=40, pady=8, fill="x")

        search_row = ctk.CTkFrame(search_card, fg_color="transparent")
        search_row.pack(fill="x", padx=20, pady=16)

        ctk.CTkLabel(search_row, text="股票搜尋：",
                      font=ctk.CTkFont(size=13)).pack(side="left")

        self.search_entry = ctk.CTkEntry(
            search_row, width=200, placeholder_text="代碼或名稱",
            font=ctk.CTkFont(size=13),
        )
        self.search_entry.pack(side="left", padx=(4, 8))
        self.search_entry.bind("<Return>", lambda e: self._on_search())

        self.search_btn = ctk.CTkButton(
            search_row, text="搜尋", width=80, height=32,
            corner_radius=8, font=ctk.CTkFont(size=13),
            command=self._on_search,
        )
        self.search_btn.pack(side="left")

        self.error_label = ctk.CTkLabel(
            search_row, text="", font=ctk.CTkFont(size=12),
            text_color="#FF6B6B",
        )
        self.error_label.pack(side="left", padx=(12, 0))

        # Search results listbox
        self.results_frame = ctk.CTkFrame(search_card, fg_color="transparent")
        self.results_listbox = tk.Listbox(
            self.results_frame, height=6, font=("Consolas", 11),
            bg="#252526", fg="#d4d4d4",
            selectbackground="#264f78", selectforeground="white",
            borderwidth=0, highlightthickness=0,
        )
        self.results_listbox.pack(fill="x", padx=20, pady=(0, 12))
        self.results_listbox.bind("<<ListboxSelect>>", self._on_stock_select)

        # --- Stock info + date range ---
        self.stock_info_card = ctk.CTkFrame(self.container, corner_radius=12)

        info_row = ctk.CTkFrame(self.stock_info_card, fg_color="transparent")
        info_row.pack(fill="x", padx=20, pady=(16, 8))
        self.stock_title_label = ctk.CTkLabel(
            info_row, text="", font=ctk.CTkFont(size=16, weight="bold"))
        self.stock_title_label.pack(side="left")

        date_row = ctk.CTkFrame(self.stock_info_card, fg_color="transparent")
        date_row.pack(fill="x", padx=20, pady=(0, 16))
        ctk.CTkLabel(date_row, text="日期區間：",
                      font=ctk.CTkFont(size=12)).pack(side="left")
        self.date_start_entry = ctk.CTkEntry(
            date_row, width=110, font=ctk.CTkFont(size=12),
            placeholder_text="yyyy-mm-dd")
        self.date_start_entry.pack(side="left", padx=(4, 4))
        ctk.CTkLabel(date_row, text="~",
                      font=ctk.CTkFont(size=12)).pack(side="left")
        self.date_end_entry = ctk.CTkEntry(
            date_row, width=110, font=ctk.CTkFont(size=12),
            placeholder_text="yyyy-mm-dd")
        self.date_end_entry.pack(side="left", padx=(4, 8))
        self.date_apply_btn = ctk.CTkButton(
            date_row, text="套用", width=60, height=28,
            corner_radius=6, font=ctk.CTkFont(size=12),
            command=self._on_date_apply)
        self.date_apply_btn.pack(side="left")
        self.date_info_label = ctk.CTkLabel(
            date_row, text="", font=ctk.CTkFont(size=11), text_color="gray")
        self.date_info_label.pack(side="left", padx=(12, 0))

        # --- Ranking card (tabbed) ---
        self.ranking_card = ctk.CTkFrame(self.container, corner_radius=12)

        self.rank_tab = ctk.CTkSegmentedButton(
            self.ranking_card,
            values=["買方 Top15", "賣方 Top15", "主力關聯度"],
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._on_rank_tab_change,
        )
        self.rank_tab.pack(padx=16, pady=(14, 6))
        self.rank_tab.set("買方 Top15")

        self.rank_list_frame = ctk.CTkFrame(
            self.ranking_card, fg_color="transparent")
        self.rank_list_frame.pack(fill="both", expand=True, padx=12, pady=(0, 14))

        # --- Holder distribution card ---
        self.holder_card = ctk.CTkFrame(self.container, corner_radius=12)

        holder_hdr = ctk.CTkFrame(self.holder_card, fg_color="transparent")
        holder_hdr.pack(fill="x", padx=20, pady=(16, 4))
        ctk.CTkLabel(
            holder_hdr, text="大戶 / 散戶持股比例",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(side="left")
        self.holder_refresh_btn = ctk.CTkButton(
            holder_hdr, text="從 TDCC 更新", width=110, height=28,
            corner_radius=6, font=ctk.CTkFont(size=11),
            command=self._on_refresh_holder,
        )
        self.holder_refresh_btn.pack(side="right")

        self.holder_info_frame = ctk.CTkFrame(
            self.holder_card, fg_color="transparent")
        self.holder_info_frame.pack(fill="x", padx=16, pady=(0, 4))

        self.holder_chart_frame = ctk.CTkFrame(
            self.holder_card, fg_color="transparent")
        self.holder_chart_frame.pack(fill="x", padx=8, pady=(0, 16))

        # --- Detail chart area ---
        self.detail_card = ctk.CTkFrame(self.container, corner_radius=12)

        self.detail_header = ctk.CTkFrame(self.detail_card, fg_color="transparent")
        self.detail_header.pack(fill="x", padx=20, pady=(16, 4))
        self.detail_title_label = ctk.CTkLabel(
            self.detail_header, text="",
            font=ctk.CTkFont(size=14, weight="bold"))
        self.detail_title_label.pack(side="left")

        # Chart frame (matplotlib goes here)
        self.chart_frame = ctk.CTkFrame(self.detail_card, fg_color="transparent")
        self.chart_frame.pack(fill="x", padx=8, pady=(4, 0))

        # Info below chart: 券商損益 line
        self.broker_info_label = ctk.CTkLabel(
            self.detail_card, text="", font=ctk.CTkFont(size=12),
            text_color="#c0c0c0", anchor="w")
        self.broker_info_label.pack(fill="x", padx=24, pady=(2, 4))

        # Summary boxes row
        self.summary_row = ctk.CTkFrame(self.detail_card, fg_color="transparent")
        self.summary_row.pack(fill="x", padx=20, pady=(0, 8))

        self.box_net = ctk.CTkFrame(self.summary_row, corner_radius=8,
                                     border_width=2, border_color="#e88a1a")
        self.box_net.pack(side="left", fill="x", expand=True, padx=4)
        self.box_net_title = ctk.CTkLabel(
            self.box_net, text="區間買賣超 (張)", font=ctk.CTkFont(size=11),
            text_color="gray")
        self.box_net_title.pack(pady=(6, 0))
        self.box_net_value = ctk.CTkLabel(
            self.box_net, text="", font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#ef5350")
        self.box_net_value.pack(pady=(0, 6))

        self.box_amt = ctk.CTkFrame(self.summary_row, corner_radius=8,
                                     border_width=2, border_color="#e88a1a")
        self.box_amt.pack(side="left", fill="x", expand=True, padx=4)
        self.box_amt_title = ctk.CTkLabel(
            self.box_amt, text="區間買賣超金額 (萬)", font=ctk.CTkFont(size=11),
            text_color="gray")
        self.box_amt_title.pack(pady=(6, 0))
        self.box_amt_value = ctk.CTkLabel(
            self.box_amt, text="", font=ctk.CTkFont(size=16, weight="bold"),
            text_color="#ef5350")
        self.box_amt_value.pack(pady=(0, 6))

        # Day selector row
        day_row = ctk.CTkFrame(self.detail_card, fg_color="transparent")
        day_row.pack(fill="x", padx=20, pady=(4, 14))
        ctk.CTkLabel(day_row, text="統計天數",
                      font=ctk.CTkFont(size=12)).pack(side="left", padx=(0, 8))
        for d in [1, 3, 5, 10, 20, 60, 120, 240]:
            ctk.CTkButton(
                day_row, text=str(d), width=38, height=28, corner_radius=6,
                font=ctk.CTkFont(size=11),
                fg_color="#3a3a3a", hover_color="#555555",
                command=lambda days=d: self._on_day_select(days),
            ).pack(side="left", padx=2)

    # ================================================================ Events

    def _on_search(self):
        self.vm.search(self.search_entry.get().strip())

    def _on_stock_select(self, event):
        sel = self.results_listbox.curselection()
        if not sel:
            return
        idx = sel[0]
        results = self.vm.search_results
        if results and 0 <= idx < len(results):
            r = results[idx]
            self.vm.select_stock(r["stock_code"], r["stock_name"])

    def _on_date_apply(self):
        s, e = self.date_start_entry.get().strip(), self.date_end_entry.get().strip()
        if s and e:
            self.vm.reload_brokers(s, e)
            if self.vm.selected_broker:
                self.vm.reload_detail(s, e)

    def _on_day_select(self, days: int):
        """Set date range to last N trading days from the end of available data."""
        end = self.vm.date_max
        if not end:
            return
        from datetime import timedelta
        end_dt = datetime.strptime(end, "%Y-%m-%d")
        start_dt = end_dt - timedelta(days=int(days * 1.6) + 5)
        start = start_dt.strftime("%Y-%m-%d")
        self.vm.reload_detail(start, end)

    def _on_refresh_holder(self):
        self.vm.load_holder_distribution()

    def _on_rank_tab_change(self, value: str):
        if "關聯" in value:
            start = self.date_start_entry.get().strip() or self.vm.date_min
            end = self.date_end_entry.get().strip() or self.vm.date_max
            self.vm.load_correlations(start, end)
        else:
            self._render_rank_list(value)

    # ================================================================ Bindings

    def _bind_vm(self):
        self.vm.bind("search_results", self._on_search_results)
        self.vm.bind("error_text", self._on_error)
        self.vm.bind("selected_stock", self._on_stock_selected)
        self.vm.bind("brokers_data", self._on_brokers_data)
        self.vm.bind("detail_data", self._on_detail_data)
        self.vm.bind("correlation_data", self._on_correlation_data)
        self.vm.bind("correlation_loading", self._on_correlation_loading)
        self.vm.bind("holder_data", self._on_holder_data)
        self.vm.bind("holder_loading", self._on_holder_loading)

    def _on_error(self, v):
        self.after(0, lambda: self.error_label.configure(text=v))

    def _on_search_results(self, results):
        def _u():
            if not results:
                self.results_frame.pack_forget()
                return
            self.results_listbox.delete(0, "end")
            for r in results:
                self.results_listbox.insert(
                    "end", f"{r['stock_code']}  {r['stock_name']}")
            self.results_frame.pack(fill="x")
            if len(results) == 1:
                self.results_listbox.select_set(0)
                self.vm.select_stock(
                    results[0]["stock_code"], results[0]["stock_name"])
        self.after(0, _u)

    def _on_stock_selected(self, stock):
        def _u():
            if not stock:
                self.stock_info_card.pack_forget()
                self.ranking_card.pack_forget()
                self.holder_card.pack_forget()
                self.detail_card.pack_forget()
                return
            self.stock_title_label.configure(
                text=f"{stock['stock_code']} {stock['stock_name']}")
            self.date_start_entry.delete(0, "end")
            self.date_start_entry.insert(0, self.vm.date_min)
            self.date_end_entry.delete(0, "end")
            self.date_end_entry.insert(0, self.vm.date_max)
            d_min, d_max = self.vm.date_min, self.vm.date_max
            self.date_info_label.configure(
                text=f"資料範圍：{d_min} ~ {d_max}" if d_min else "")
            self.stock_info_card.pack(padx=40, pady=8, fill="x")
            self.ranking_card.pack(padx=40, pady=8, fill="x")
            self.holder_card.pack(padx=40, pady=8, fill="x")
            self.detail_card.pack_forget()
            # Auto-load holder distribution
            self.vm.load_holder_distribution()
        self.after(0, _u)

    def _on_brokers_data(self, brokers):
        def _u():
            if brokers is None:
                return
            buyers = [b for b in brokers if b["net_volume"] > 0]
            buyers.sort(key=lambda b: b["net_volume"], reverse=True)
            sellers = [b for b in brokers if b["net_volume"] < 0]
            sellers.sort(key=lambda b: b["net_volume"])
            self._rank_buyers = buyers[:15]
            self._rank_sellers = sellers[:15]
            self._render_rank_list(self.rank_tab.get())
        self.after(0, _u)

    def _on_detail_data(self, data):
        def _u():
            if data is None:
                self.detail_card.pack_forget()
                return
            broker = self.vm.selected_broker
            b_name = broker["broker_name"] if broker else ""
            self.detail_title_label.configure(text=f"分點進出：{b_name}")
            self._fill_detail_info(data)
            self._build_detail_chart(data)
            self.detail_card.pack(padx=40, pady=8, fill="x")
        self.after(0, _u)

    def _on_holder_loading(self, v):
        def _u():
            if v:
                self.holder_refresh_btn.configure(state="disabled", text="載入中...")
            else:
                self.holder_refresh_btn.configure(state="normal", text="從 TDCC 更新")
        self.after(0, _u)

    def _on_holder_data(self, data):
        def _u():
            self._render_holder(data)
        self.after(0, _u)

    def _render_holder(self, data):
        for w in self.holder_info_frame.winfo_children():
            w.destroy()
        for w in self.holder_chart_frame.winfo_children():
            w.destroy()

        if data is None:
            return
        if "error" in data:
            ctk.CTkLabel(
                self.holder_info_frame, text=data["error"],
                font=ctk.CTkFont(size=12), text_color="#FF6B6B",
            ).pack(pady=8)
            return

        cur = data.get("current", {})
        history = data.get("history", [])

        # KPI cards
        kpi_row = ctk.CTkFrame(self.holder_info_frame, fg_color="transparent")
        kpi_row.pack(fill="x", pady=(4, 4))

        for label, value, color in [
            ("大戶 (>400張)", f"{cur.get('big_pct', 0):.1f}%", "#ef5350"),
            ("中實戶 (20-400張)", f"{cur.get('mid_pct', 0):.1f}%", "#ff9800"),
            ("散戶 (<20張)", f"{cur.get('retail_pct', 0):.1f}%", "#26a69a"),
            ("資料日期", cur.get("report_date", ""), "#8e8e93"),
        ]:
            f = ctk.CTkFrame(kpi_row, corner_radius=8, width=130)
            f.pack(side="left", padx=6, pady=2)
            ctk.CTkLabel(f, text=label, font=ctk.CTkFont(size=10),
                          text_color="gray").pack(padx=10, pady=(5, 0))
            ctk.CTkLabel(f, text=value,
                          font=ctk.CTkFont(size=14, weight="bold"),
                          text_color=color).pack(padx=10, pady=(0, 5))

        # Trend chart (if history available)
        if not history or not HAS_MPL or len(history) < 2:
            if len(history) < 2:
                ctk.CTkLabel(
                    self.holder_chart_frame,
                    text="（累積 2 週以上資料後可顯示趨勢圖）",
                    font=ctk.CTkFont(size=11), text_color="gray",
                ).pack(pady=8)
            return

        from matplotlib.figure import Figure
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

        is_dark = ctk.get_appearance_mode().lower() == "dark"
        bg = "#1c1c1e" if is_dark else "#fafafa"
        panel = "#1c1c1e" if is_dark else "#ffffff"
        txt = "#c0c0c0" if is_dark else "#333333"
        grid = "#2c2c2e" if is_dark else "#e0e0e0"

        n = len(history)
        xs = list(range(n))
        big = [h["big_pct"] for h in history]
        mid = [h["mid_pct"] for h in history]
        ret = [h["retail_pct"] for h in history]
        labels = [h["report_date"][5:] for h in history]  # mm-dd

        fig = Figure(figsize=(8, 2.5), dpi=100, facecolor=bg)
        fig.subplots_adjust(left=0.08, right=0.96, top=0.90, bottom=0.18)
        ax = fig.add_subplot(111)
        ax.set_facecolor(panel)

        ax.plot(xs, big, color="#ef5350", linewidth=1.5, marker="o",
                markersize=3, label="大戶")
        ax.plot(xs, mid, color="#ff9800", linewidth=1.5, marker="s",
                markersize=3, label="中實戶")
        ax.plot(xs, ret, color="#26a69a", linewidth=1.5, marker="^",
                markersize=3, label="散戶")

        ax.set_ylabel("%", fontsize=8, color=txt)
        ax.tick_params(axis="both", colors=txt, labelsize=7)
        for sp in ax.spines.values():
            sp.set_color(grid)
        ax.grid(True, alpha=0.2, color=grid, linewidth=0.5)
        ax.legend(loc="upper left", fontsize=7, framealpha=0.5,
                  facecolor=panel, edgecolor=grid, labelcolor=txt, ncol=3)

        ax.set_xticks(xs)
        ax.set_xticklabels(labels, fontsize=7, color=txt, rotation=35, ha="right")

        canvas = FigureCanvasTkAgg(fig, self.holder_chart_frame)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="x", padx=4, pady=(4, 4))

    def _on_correlation_loading(self, v):
        def _u():
            if v:
                for w in self.rank_list_frame.winfo_children():
                    w.destroy()
                ctk.CTkLabel(
                    self.rank_list_frame, text="分析中...",
                    font=ctk.CTkFont(size=13), text_color="gray",
                ).pack(pady=20)
        self.after(0, _u)

    def _on_correlation_data(self, data):
        def _u():
            if data is None:
                return
            self._render_correlation_list(data)
        self.after(0, _u)

    # ================================================================ Ranking list

    def _render_rank_list(self, tab: str):
        for w in self.rank_list_frame.winfo_children():
            w.destroy()

        is_buy = "買方" in tab
        rows = self._rank_buyers if is_buy else self._rank_sellers
        accent = "#ef5350" if not is_buy else "#26a69a"

        if not rows:
            ctk.CTkLabel(
                self.rank_list_frame, text="（無資料）",
                font=ctk.CTkFont(size=12), text_color="gray",
            ).pack(pady=20)
            return

        # Header
        hdr = ctk.CTkFrame(self.rank_list_frame, fg_color="transparent", height=28)
        hdr.pack(fill="x", padx=8, pady=(4, 0))
        hdr.pack_propagate(False)
        for text, w, anc in [("券商", 0.42, "w"), ("買賣超(張)", 0.28, "e"),
                              ("損益(萬)", 0.28, "e")]:
            lbl = ctk.CTkLabel(hdr, text=text, font=ctk.CTkFont(size=11),
                                text_color="gray")
            lbl.place(relx=w - 0.28 + 0.02 if anc == "w" else 0,
                      rely=0.5, anchor="w" if anc == "w" else "w")
        # manual placement
        ctk.CTkLabel(hdr, text="券商", font=ctk.CTkFont(size=11),
                      text_color="gray").place(relx=0.02, rely=0.5, anchor="w")
        ctk.CTkLabel(hdr, text="買賣超(張)", font=ctk.CTkFont(size=11),
                      text_color="gray").place(relx=0.58, rely=0.5, anchor="e")
        ctk.CTkLabel(hdr, text="損益(萬)", font=ctk.CTkFont(size=11),
                      text_color="gray").place(relx=0.97, rely=0.5, anchor="e")
        # clear the generic ones
        for w2 in list(hdr.winfo_children())[:-3]:
            w2.destroy()

        for i, b in enumerate(rows):
            bg = "#1e1e1e" if i % 2 == 0 else "#252526"
            row_f = ctk.CTkFrame(self.rank_list_frame, fg_color=bg,
                                  corner_radius=6, height=38)
            row_f.pack(fill="x", padx=8, pady=1)
            row_f.pack_propagate(False)

            net = b["net_volume"]
            net_str = f"+{_fmt(net)}" if net > 0 else _fmt(net)
            pnl = b.get("pnl", 0)
            pnl_val = round(pnl / 10000, 1)  # 萬
            pnl_str = f"+{pnl_val:,.1f}" if pnl > 0 else f"{pnl_val:,.1f}"
            pnl_clr = "#26a69a" if pnl >= 0 else "#ef5350"

            ctk.CTkLabel(
                row_f, text=b["broker_name"],
                font=ctk.CTkFont(size=12), text_color="#e0e0e0",
            ).place(relx=0.02, rely=0.5, anchor="w")

            ctk.CTkLabel(
                row_f, text=net_str,
                font=ctk.CTkFont(size=12, weight="bold"), text_color=accent,
            ).place(relx=0.58, rely=0.5, anchor="e")

            ctk.CTkLabel(
                row_f, text=pnl_str,
                font=ctk.CTkFont(size=12), text_color=pnl_clr,
            ).place(relx=0.97, rely=0.5, anchor="e")

            # Click to show detail
            row_f.bind("<Button-1>",
                        lambda e, br=b: self._on_rank_row_click(br))
            for child in row_f.winfo_children():
                child.bind("<Button-1>",
                            lambda e, br=b: self._on_rank_row_click(br))

    def _on_rank_row_click(self, b: dict):
        start = self.date_start_entry.get().strip() or self.vm.date_min
        end = self.date_end_entry.get().strip() or self.vm.date_max
        self.vm.select_broker(b["broker_code"], b["broker_name"], start, end)

    # ================================================================ Correlation list

    def _render_correlation_list(self, corr_list):
        for w in self.rank_list_frame.winfo_children():
            w.destroy()

        if not corr_list:
            ctk.CTkLabel(
                self.rank_list_frame, text="（資料不足，無法分析）",
                font=ctk.CTkFont(size=12), text_color="gray",
            ).pack(pady=20)
            return

        # Header
        hdr = ctk.CTkFrame(self.rank_list_frame, fg_color="transparent", height=28)
        hdr.pack(fill="x", padx=8, pady=(4, 0))
        hdr.pack_propagate(False)
        for text, rx in [("券商", 0.02), ("綜合分數", 0.42),
                          ("IC", 0.58), ("最佳lag", 0.72),
                          ("交易天", 0.88)]:
            anc = "w" if text == "券商" else "e"
            ctk.CTkLabel(hdr, text=text, font=ctk.CTkFont(size=10),
                          text_color="gray").place(
                relx=rx, rely=0.5, anchor=anc)

        top = corr_list[:20]
        max_score = top[0].composite_score if top else 1.0

        for i, c in enumerate(top):
            bg = "#1e1e1e" if i % 2 == 0 else "#252526"
            row = ctk.CTkFrame(self.rank_list_frame, fg_color=bg,
                                corner_radius=6, height=38)
            row.pack(fill="x", padx=8, pady=1)
            row.pack_propagate(False)

            # Score color: high=yellow, medium=orange, low=gray
            ratio = c.composite_score / max_score if max_score > 0 else 0
            if ratio > 0.7:
                score_clr = "#ffeb3b"
            elif ratio > 0.4:
                score_clr = "#ff9800"
            else:
                score_clr = "#8e8e93"

            ic_clr = "#26a69a" if c.ic_score > 0 else "#ef5350"

            ctk.CTkLabel(
                row, text=c.broker_name,
                font=ctk.CTkFont(size=12), text_color="#e0e0e0",
            ).place(relx=0.02, rely=0.5, anchor="w")

            ctk.CTkLabel(
                row, text=f"{c.composite_score:.3f}",
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color=score_clr,
            ).place(relx=0.50, rely=0.5, anchor="e")

            ic_str = f"{c.ic_score:+.3f}"
            ctk.CTkLabel(
                row, text=ic_str,
                font=ctk.CTkFont(size=11), text_color=ic_clr,
            ).place(relx=0.65, rely=0.5, anchor="e")

            lag_str = f"{c.cross_corr_lag}日"
            ctk.CTkLabel(
                row, text=lag_str,
                font=ctk.CTkFont(size=11), text_color="#c0c0c0",
            ).place(relx=0.78, rely=0.5, anchor="e")

            ctk.CTkLabel(
                row, text=str(c.active_days),
                font=ctk.CTkFont(size=11), text_color="#8e8e93",
            ).place(relx=0.92, rely=0.5, anchor="e")

            # Click to show detail chart
            row.bind("<Button-1>", lambda e, br=c: self._on_rank_row_click({
                "broker_code": br.broker_code, "broker_name": br.broker_name,
            }))
            for child in row.winfo_children():
                child.bind("<Button-1>", lambda e, br=c: self._on_rank_row_click({
                    "broker_code": br.broker_code, "broker_name": br.broker_name,
                }))

    # ================================================================ Detail info

    def _fill_detail_info(self, data: dict):
        avg_buy = data.get("avg_buy_price")
        avg_sell = data.get("avg_sell_price")
        total_buy = data.get("total_buy_volume", 0)
        total_sell = data.get("total_sell_volume", 0)
        net = data.get("net_volume", 0)

        # 券商損益 line
        parts = "券商損益"
        if avg_buy is not None:
            parts += f"　買均價 {avg_buy:.2f}"
        if avg_sell is not None:
            parts += f"　賣均價 {avg_sell:.2f}"
        self.broker_info_label.configure(text=parts)

        # Summary boxes
        net_clr = "#ef5350" if net >= 0 else "#26a69a"
        self.box_net_value.configure(text=_fmt(net), text_color=net_clr)

        # 區間金額 (萬) = net_volume * avg_price (rough)
        avg_p = avg_buy or avg_sell or 0
        amt = round(abs(net) * avg_p / 10000)
        amt_str = _fmt(amt)
        self.box_amt_value.configure(text=amt_str, text_color=net_clr)

    # ================================================================ Chart

    def _build_detail_chart(self, data: dict):
        for w in self.chart_frame.winfo_children():
            w.destroy()
        self._chart_canvas = None
        self._chart_fig = None
        self._chart_data = None

        if not HAS_MPL:
            ctk.CTkLabel(self.chart_frame, text="（pip install matplotlib）",
                          font=ctk.CTkFont(size=12),
                          text_color="gray").pack(pady=16)
            return

        prices = data.get("prices", [])
        broker_daily = data.get("broker_daily", [])
        if not prices:
            ctk.CTkLabel(self.chart_frame, text="（該區間無價格資料）",
                          font=ctk.CTkFont(size=12),
                          text_color="gray").pack(pady=16)
            return

        # ---- Parse close prices ----
        labels: list[str] = []
        c_list: list[float] = []
        for p in prices:
            d_str = str(p["trade_date"])[:10]
            c = _parse_price(p["close_price"])
            if c is not None:
                labels.append(d_str)
                c_list.append(c)

        if not labels:
            ctk.CTkLabel(self.chart_frame, text="（無法解析價格資料）",
                          font=ctk.CTkFont(size=12),
                          text_color="gray").pack(pady=16)
            return

        n = len(labels)
        xs = list(range(n))

        # Broker data aligned to price dates
        b_map: dict[str, dict] = {}
        for bd in broker_daily:
            b_map[str(bd["trade_date"])[:10]] = bd
        b_net, b_buy, b_sell = [], [], []
        for lbl in labels:
            rec = b_map.get(lbl)
            b_buy.append((rec["buy_volume"] or 0) if rec else 0)
            b_sell.append((rec["sell_volume"] or 0) if rec else 0)
            b_net.append((rec["net_volume"] or 0) if rec else 0)

        # Cumulative net
        cum_net = []
        s = 0
        for v in b_net:
            s += v
            cum_net.append(s)

        total_buy = data.get("total_buy_volume", 0)
        total_sell = data.get("total_sell_volume", 0)
        net_vol = data.get("net_volume", 0)
        avg_buy = data.get("avg_buy_price")
        avg_sell = data.get("avg_sell_price")

        # ---- Theme (dark, matching reference) ----
        bg       = "#1c1c1e"
        panel_bg = "#1c1c1e"
        txt      = "#8e8e93"
        txt_w    = "#d1d1d6"
        grid_clr = "#2c2c2e"
        cross_clr = "#636366"
        price_line_clr = "#8e8e93"
        vol_axis_clr = "#ef5350"

        fig = Figure(figsize=(8.5, 4.5), dpi=100, facecolor=bg)
        fig.subplots_adjust(left=0.06, right=0.92, top=0.82, bottom=0.10)

        ax = fig.add_subplot(111)
        ax.set_facecolor(panel_bg)

        # ---- Volume bars (primary visual, left y-axis) ----
        bar_w = 0.55
        bar_clrs = ["#ef5350" if v >= 0 else "#26a69a" for v in b_net]
        ax.bar(xs, b_net, width=bar_w, color=bar_clrs, alpha=0.85, zorder=5)

        ax.axhline(0, color=grid_clr, linewidth=0.6, zorder=4)
        ax.set_ylabel("淨量", fontsize=8, color=vol_axis_clr, labelpad=4)
        ax.tick_params(axis="y", colors=vol_axis_clr, labelsize=7)
        ax.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda x, _: f"{int(x):,}"))
        for sp in ax.spines.values():
            sp.set_color(grid_clr)
        ax.grid(True, axis="y", alpha=0.2, color=grid_clr, linewidth=0.5)
        ax.grid(True, axis="x", alpha=0.1, color=grid_clr, linewidth=0.3)

        # ---- Price line (twin right y-axis) ----
        ax_price = ax.twinx()
        ax_price.plot(xs, c_list, color=price_line_clr, linewidth=1.0,
                       zorder=8)
        ax_price.set_ylabel("收盤價", fontsize=8, color=txt, labelpad=4)
        ax_price.tick_params(axis="y", colors=txt, labelsize=7)
        for sp in ax_price.spines.values():
            sp.set_color(grid_clr)

        # Price range
        p_lo, p_hi = min(c_list), max(c_list)
        p_rng = p_hi - p_lo if p_hi > p_lo else 1.0
        ax_price.set_ylim(p_lo - p_rng * 0.15, p_hi + p_rng * 0.15)

        # avg buy/sell dashed lines on price axis
        if avg_buy is not None:
            ax_price.axhline(avg_buy, color="#26a69a", linestyle="--",
                              linewidth=0.7, alpha=0.6, zorder=7)
        if avg_sell is not None:
            ax_price.axhline(avg_sell, color="#ef5350", linestyle="--",
                              linewidth=0.7, alpha=0.6, zorder=7)

        # ---- Text overlay at top (inside figure) ----
        net_clr = "#ef5350" if net_vol >= 0 else "#26a69a"
        fig.text(0.06, 0.97, "分點進出", fontsize=10, color=txt_w,
                 va="top", fontweight="bold")
        cum_str = f"累積買賣超 (張) {_fmt(cum_net[-1] if cum_net else 0)}"
        fig.text(0.20, 0.97, cum_str, fontsize=9, color="#ffeb3b", va="top")

        detail_str = (
            f"買賣超 (張) {_fmt(net_vol)}  "
            f"買張 {_fmt(total_buy)}  賣張 {_fmt(total_sell)}"
        )
        fig.text(0.20, 0.91, detail_str, fontsize=8.5, color=net_clr, va="top")

        # ---- X ticks ----
        def _fmt_lbl(i):
            i = int(round(i))
            return labels[i][5:7] + "/" + labels[i][8:10] if 0 <= i < n else ""

        def _apply_ticks(xmin_v, xmax_v):
            vs = max(int(xmin_v) + 1, 0)
            ve = min(int(xmax_v), n - 1)
            vn = ve - vs + 1
            if vn <= 0:
                return
            step = max(vn // 10, 1) if vn > 15 else 1
            tks = list(range(vs, ve + 1, step))
            if tks and tks[-1] != ve:
                tks.append(ve)
            ax.set_xticks(tks)
            ax.set_xticklabels([_fmt_lbl(t) for t in tks],
                                fontsize=7, color=txt, rotation=35, ha="right")

        ax.set_xlim(-0.6, n - 0.4)
        _apply_ticks(-0.6, n - 0.4)
        ax.tick_params(axis="x", colors=txt, labelsize=7)

        # ---- Embed ----
        canvas = FigureCanvasTkAgg(fig, self.chart_frame)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="x", padx=4, pady=(4, 4))

        self._chart_canvas = canvas
        self._chart_fig = fig
        self._chart_data = {
            "labels": labels, "close": c_list,
            "net": b_net, "buy": b_buy, "sell": b_sell, "n": n,
            "ax": ax, "ax_price": ax_price,
            "cross_clr": cross_clr, "txt": txt, "txt_w": txt_w,
            "panel_bg": panel_bg, "grid_clr": grid_clr,
            "apply_ticks": _apply_ticks,
        }

        # ---- Crosshair ----
        self._cross_vline = ax.axvline(0, color=cross_clr, lw=0.5,
                                        ls="--", visible=False, zorder=18)
        self._cross_hline_price = ax_price.axhline(
            0, color=cross_clr, lw=0.5, ls="--", visible=False, zorder=18)
        self._cross_dot = ax_price.plot([], [], "o", color="#ffffff", ms=4,
                                         zorder=19, visible=False)[0]
        self._annot = ax_price.annotate(
            "", xy=(0, 0), xytext=(12, 16), textcoords="offset points",
            fontsize=8, color=txt_w,
            bbox=dict(boxstyle="round,pad=0.4", fc="#2c2c2e",
                      ec="#3a3a3c", alpha=0.92),
            zorder=25, visible=False)

        canvas.mpl_connect("motion_notify_event", self._on_mouse_move)
        canvas.mpl_connect("axes_leave_event", self._on_mouse_leave)
        canvas.mpl_connect("scroll_event", self._on_scroll_zoom)

    # ---- Crosshair ----

    def _on_mouse_move(self, event):
        cd = self._chart_data
        if cd is None or event.inaxes is None or event.xdata is None:
            self._hide_crosshair()
            return
        n = cd["n"]
        if n == 0:
            return

        idx = max(0, min(int(round(event.xdata)), n - 1))
        price = cd["close"][idx]
        net = cd["net"][idx]
        buy = cd["buy"][idx]
        sell = cd["sell"][idx]

        self._cross_vline.set_xdata([idx])
        self._cross_vline.set_visible(True)
        self._cross_hline_price.set_ydata([price])
        self._cross_hline_price.set_visible(True)
        self._cross_dot.set_data([idx], [price])
        self._cross_dot.set_visible(True)

        net_s = f"+{_fmt(net)}" if net > 0 else _fmt(net)
        info = (
            f"{cd['labels'][idx]}\n"
            f"收盤 {price:.2f}\n"
            f"買 {_fmt(buy)}  賣 {_fmt(sell)}\n"
            f"淨量 {net_s}"
        )
        self._annot.set_text(info)
        self._annot.xy = (idx, price)

        ax_p = cd["ax_price"]
        xmin, xmax = cd["ax"].get_xlim()
        rel = (idx - xmin) / (xmax - xmin) if xmax > xmin else 0
        self._annot.set_position((-130, 16) if rel > 0.65 else (12, 16))
        self._annot.set_visible(True)
        self._chart_canvas.draw_idle()

    def _on_mouse_leave(self, event):
        self._hide_crosshair()

    def _hide_crosshair(self):
        if self._chart_data is None:
            return
        for o in (self._cross_vline, self._cross_hline_price,
                   self._cross_dot, self._annot):
            o.set_visible(False)
        if self._chart_canvas:
            self._chart_canvas.draw_idle()

    # ---- Scroll zoom ----

    def _on_scroll_zoom(self, event):
        cd = self._chart_data
        if cd is None or event.inaxes is None or event.xdata is None:
            return
        ax = cd["ax"]
        n = cd["n"]
        xmin, xmax = ax.get_xlim()
        rng = xmax - xmin
        scale = 0.8 if event.button == "up" else (
            1.25 if event.button == "down" else None)
        if scale is None:
            return
        half = rng * scale / 2
        cx = event.xdata
        lo = max(cx - half, -0.6)
        hi = min(cx + half, n - 0.4)
        if hi - lo < 3:
            return
        ax.set_xlim(lo, hi)
        cd["apply_ticks"](lo, hi)
        self._chart_canvas.draw_idle()
