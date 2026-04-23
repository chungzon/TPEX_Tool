import customtkinter as ctk
from tkinter import ttk, Canvas
import tkinter as tk

from viewmodels.broker_download_viewmodel import BrokerDownloadViewModel
from views.stats_helpers import compute_stats, fmt_number, StatsResult, BrokerStat

# Try to import matplotlib for charts
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    # Set Chinese font for Windows
    matplotlib.rcParams["font.sans-serif"] = [
        "Microsoft JhengHei", "Microsoft YaHei", "SimHei", "sans-serif",
    ]
    matplotlib.rcParams["axes.unicode_minus"] = False
    HAS_MPL = True
except ImportError:
    HAS_MPL = False


class BrokerDownloadView(ctk.CTkFrame):
    """上櫃分點資料下載 tab page."""

    def __init__(self, parent: ctk.CTkFrame, viewmodel: BrokerDownloadViewModel):
        super().__init__(parent, fg_color="transparent")
        self.vm = viewmodel
        self._sort_col: str = ""
        self._sort_asc: bool = True
        self._current_stats: StatsResult | None = None
        self._chart_canvas: FigureCanvasTkAgg | None = None if HAS_MPL else None
        self._build_ui()
        self._bind_vm()

    # ------------------------------------------------------------------ UI
    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        # --- Scrollable container ---
        self.container = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.container.pack(fill="both", expand=True)

        # --- Title ---
        ctk.CTkLabel(
            self.container,
            text="上櫃分點資料下載",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(pady=(24, 8))

        ctk.CTkLabel(
            self.container,
            text="從證券櫃檯買賣中心下載各券商分點進出資料（僅提供當日資料，約 16:00 後更新）",
            font=ctk.CTkFont(size=13),
            text_color="gray",
        ).pack(pady=(0, 24))

        # --- Parameter card ---
        card = ctk.CTkFrame(self.container, corner_radius=12)
        card.pack(padx=40, pady=8, fill="x")

        ctk.CTkLabel(
            card, text="下載參數設定",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(anchor="w", padx=20, pady=(16, 12))

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=4)
        ctk.CTkLabel(row, text="股票代碼", width=80, anchor="w").pack(side="left")
        self.stock_entry = ctk.CTkEntry(row, placeholder_text="例: 6180", width=200)
        self.stock_entry.pack(side="left", padx=(8, 0))

        self.error_label = ctk.CTkLabel(
            card, text="", font=ctk.CTkFont(size=12), text_color="#FF6B6B",
        )
        self.error_label.pack(padx=20, pady=(4, 0))

        ctk.CTkFrame(card, fg_color="transparent", height=4).pack()

        self.download_btn = ctk.CTkButton(
            card, text="開始下載", width=160, height=38, corner_radius=8,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._on_download_click,
        )
        self.download_btn.pack(pady=(8, 20))

        # --- Progress area ---
        progress_frame = ctk.CTkFrame(self.container, corner_radius=12)
        progress_frame.pack(padx=40, pady=16, fill="x")

        self.progress_bar = ctk.CTkProgressBar(progress_frame, width=400)
        self.progress_bar.pack(padx=20, pady=(16, 8))
        self.progress_bar.set(0)

        self.status_label = ctk.CTkLabel(
            progress_frame, text="就緒",
            font=ctk.CTkFont(size=12), text_color="gray",
        )
        self.status_label.pack(pady=(0, 16))

        # --- Summary card (hidden) ---
        self.summary_card = ctk.CTkFrame(self.container, corner_radius=12)
        self.summary_card.pack(padx=40, pady=8, fill="x")
        self.summary_card.pack_forget()

        self.summary_label = ctk.CTkLabel(
            self.summary_card, text="",
            font=ctk.CTkFont(size=13), justify="left",
        )
        self.summary_label.pack(padx=20, pady=16, anchor="w")

        # --- Stats section (created but not packed until data arrives) ---
        self.stats_frame = ctk.CTkFrame(self.container, fg_color="transparent")

        # --- Chart section (created but not packed until data arrives) ---
        self.chart_frame = ctk.CTkFrame(self.container, corner_radius=12)

        # --- Result table ---
        self.table_frame = ctk.CTkFrame(self.container, corner_radius=12)
        self.table_frame.pack(padx=40, pady=8, fill="both", expand=True)

        self.placeholder_label = ctk.CTkLabel(
            self.table_frame, text="下載結果將顯示於此",
            font=ctk.CTkFont(size=13), text_color="gray",
        )
        self.placeholder_label.pack(expand=True, pady=30)

        self.tree: ttk.Treeview | None = None

    # ------------------------------------------------------------------ Gauge
    def _build_gauge(self, parent: ctk.CTkFrame, stats: StatsResult):
        """Draw a buy/sell ratio gauge bar."""
        gauge_card = ctk.CTkFrame(parent, corner_radius=12)
        gauge_card.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(
            gauge_card, text="買賣力道",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(12, 4))

        bar_frame = ctk.CTkFrame(gauge_card, fg_color="transparent")
        bar_frame.pack(fill="x", padx=16, pady=(0, 4))

        # Labels show net buying force vs net selling force
        buy_pct = stats.buy_ratio * 100
        sell_pct = 100 - buy_pct
        ctk.CTkLabel(
            bar_frame,
            text=f"買超 {fmt_number(stats.net_buy_force)} 股 ({buy_pct:.1f}%)",
            font=ctk.CTkFont(size=12), text_color="#4CAF50",
        ).pack(side="left")
        ctk.CTkLabel(
            bar_frame,
            text=f"賣超 {fmt_number(stats.net_sell_force)} 股 ({sell_pct:.1f}%)",
            font=ctk.CTkFont(size=12), text_color="#F44336",
        ).pack(side="right")

        # Canvas gauge
        canvas = Canvas(gauge_card, height=24, bg="#2b2b2b", highlightthickness=0)
        canvas.pack(fill="x", padx=16, pady=(4, 12))

        def _draw(event=None):
            w = canvas.winfo_width()
            if w < 2:
                return
            canvas.delete("all")
            buy_w = max(int(w * stats.buy_ratio), 1)
            canvas.create_rectangle(0, 0, buy_w, 24, fill="#4CAF50", outline="")
            canvas.create_rectangle(buy_w, 0, w, 24, fill="#F44336", outline="")

        canvas.bind("<Configure>", _draw)

    # ------------------------------------------------------------------ Stats cards
    def _build_stats_section(self, stats: StatsResult):
        """Build the statistics summary cards."""
        # Clear previous
        for w in self.stats_frame.winfo_children():
            w.destroy()

        # --- Row 1: Gauge ---
        self._build_gauge(self.stats_frame, stats)

        # --- Row 2: KPI cards ---
        kpi_row = ctk.CTkFrame(self.stats_frame, fg_color="transparent")
        kpi_row.pack(fill="x", pady=(0, 8))
        kpi_row.columnconfigure((0, 1, 2, 3), weight=1)

        kpis = [
            ("買超券商", str(stats.buyer_count), "#4CAF50"),
            ("賣超券商", str(stats.seller_count), "#F44336"),
            ("前5大買超集中度", f"{stats.top5_buy_pct:.1%}", "#66BB6A"),
            ("前5大賣超集中度", f"{stats.top5_sell_pct:.1%}", "#EF5350"),
        ]
        for i, (label, value, color) in enumerate(kpis):
            kpi_card = ctk.CTkFrame(kpi_row, corner_radius=10)
            kpi_card.grid(row=0, column=i, padx=4, pady=4, sticky="nsew")
            ctk.CTkLabel(
                kpi_card, text=label,
                font=ctk.CTkFont(size=11), text_color="gray",
            ).pack(padx=12, pady=(10, 2))
            ctk.CTkLabel(
                kpi_card, text=value,
                font=ctk.CTkFont(size=20, weight="bold"), text_color=color,
            ).pack(padx=12, pady=(0, 10))

        # --- Row 3: Top 5 buyers / sellers ---
        top_row = ctk.CTkFrame(self.stats_frame, fg_color="transparent")
        top_row.pack(fill="x", pady=(0, 8))
        top_row.columnconfigure((0, 1), weight=1)

        self._build_top_card(top_row, "買超前 5 大", stats.top_buyers, "#4CAF50", 0)
        self._build_top_card(top_row, "賣超前 5 大", stats.top_sellers, "#F44336", 1)

        # Show frame
        self.stats_frame.pack(padx=40, pady=8, fill="x")

    def _build_top_card(self, parent, title: str, brokers: list[BrokerStat], color: str, col: int):
        card = ctk.CTkFrame(parent, corner_radius=10)
        card.grid(row=0, column=col, padx=4, pady=4, sticky="nsew")

        ctk.CTkLabel(
            card, text=title,
            font=ctk.CTkFont(size=13, weight="bold"), text_color=color,
        ).pack(anchor="w", padx=14, pady=(12, 6))

        for i, b in enumerate(brokers):
            row_frame = ctk.CTkFrame(card, fg_color="transparent")
            row_frame.pack(fill="x", padx=14, pady=1)
            ctk.CTkLabel(
                row_frame, text=f"{i+1}. {b.broker_name}",
                font=ctk.CTkFont(size=12), anchor="w",
            ).pack(side="left")
            net_text = f"+{fmt_number(b.net)}" if b.net > 0 else fmt_number(b.net)
            ctk.CTkLabel(
                row_frame, text=f"{net_text} 股",
                font=ctk.CTkFont(size=12, weight="bold"), text_color=color, anchor="e",
            ).pack(side="right")

        if not brokers:
            ctk.CTkLabel(
                card, text="（無資料）",
                font=ctk.CTkFont(size=12), text_color="gray",
            ).pack(padx=14, pady=8)

        # bottom padding
        ctk.CTkFrame(card, fg_color="transparent", height=8).pack()

    # ------------------------------------------------------------------ Charts
    def _build_charts(self, stats: StatsResult):
        """Render matplotlib charts embedded in the UI."""
        for w in self.chart_frame.winfo_children():
            w.destroy()

        if not HAS_MPL:
            ctk.CTkLabel(
                self.chart_frame, text="（安裝 matplotlib 可顯示圖表：pip install matplotlib）",
                font=ctk.CTkFont(size=12), text_color="gray",
            ).pack(pady=16)
            self.chart_frame.pack(padx=40, pady=8, fill="x")
            return

        ctk.CTkLabel(
            self.chart_frame, text="統計圖表",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(12, 4))

        # Prepare data
        top_all = stats.top_buyers + stats.top_sellers
        if not top_all:
            ctk.CTkLabel(
                self.chart_frame, text="（無足夠資料繪製圖表）",
                font=ctk.CTkFont(size=12), text_color="gray",
            ).pack(pady=16)
            self.chart_frame.pack(padx=40, pady=8, fill="x")
            return

        is_dark = ctk.get_appearance_mode().lower() == "dark"
        bg_color = "#2b2b2b" if is_dark else "#f0f0f0"
        text_color = "#e0e0e0" if is_dark else "#333333"

        fig = Figure(figsize=(7.5, 3.5), dpi=100, facecolor=bg_color)

        # --- Left: Bar chart ---
        ax1 = fig.add_subplot(121)
        ax1.set_facecolor(bg_color)

        # Combine top buyers and sellers, sort by net
        combined = sorted(top_all, key=lambda b: b.net, reverse=True)
        names = [b.broker_name for b in combined]
        nets = [b.net for b in combined]
        colors = ["#4CAF50" if n > 0 else "#F44336" for n in nets]

        y_pos = range(len(names))
        ax1.barh(y_pos, nets, color=colors, height=0.6)
        ax1.set_yticks(y_pos)
        ax1.set_yticklabels(names, fontsize=8, color=text_color)
        ax1.invert_yaxis()
        ax1.set_title("買超 / 賣超排行", fontsize=10, color=text_color, pad=8)
        ax1.tick_params(axis="x", colors=text_color, labelsize=8)
        ax1.spines[:].set_color(text_color)
        ax1.axvline(0, color=text_color, linewidth=0.5)

        # Format x-axis with thousands
        ax1.xaxis.set_major_formatter(
            matplotlib.ticker.FuncFormatter(lambda x, _: f"{int(x):,}")
        )

        # --- Right: Pie chart ---
        ax2 = fig.add_subplot(122)
        ax2.set_facecolor(bg_color)

        if stats.total_buy_volume > 0 or stats.total_sell_volume > 0:
            pie_data = [stats.total_buy_volume, stats.total_sell_volume]
            pie_labels = ["買進", "賣出"]
            pie_colors = ["#4CAF50", "#F44336"]
            wedges, texts, autotexts = ax2.pie(
                pie_data, labels=pie_labels, colors=pie_colors,
                autopct="%1.1f%%", startangle=90,
                textprops={"fontsize": 10, "color": text_color},
            )
            for t in autotexts:
                t.set_color("white")
                t.set_fontsize(9)
            ax2.set_title("買賣比例", fontsize=10, color=text_color, pad=8)

        fig.tight_layout(pad=2)

        # Embed in tk
        if self._chart_canvas is not None:
            self._chart_canvas.get_tk_widget().destroy()
        self._chart_canvas = FigureCanvasTkAgg(fig, self.chart_frame)
        self._chart_canvas.draw()
        self._chart_canvas.get_tk_widget().pack(fill="x", padx=8, pady=(0, 12))

        self.chart_frame.pack(padx=40, pady=8, fill="x")

    # ------------------------------------------------------------------ Table
    def _build_table(self):
        """Create the Treeview widget with enhanced columns."""
        if self.tree is not None:
            self.tree.destroy()
            # Also destroy any existing scrollbar
            for w in self.table_frame.winfo_children():
                if isinstance(w, ttk.Scrollbar):
                    w.destroy()

        style = ttk.Style()
        style.configure("Broker.Treeview", rowheight=26, font=("", 11))
        style.configure("Broker.Treeview.Heading", font=("", 11, "bold"))

        columns = ("seq", "broker", "buy", "sell", "net")
        self.tree = ttk.Treeview(
            self.table_frame,
            columns=columns,
            show="headings",
            style="Broker.Treeview",
            height=15,
        )

        headings = {
            "seq": "序號", "broker": "券商",
            "buy": "買進股數", "sell": "賣出股數", "net": "淨買賣",
        }
        widths = {
            "seq": 50, "broker": 200,
            "buy": 120, "sell": 120, "net": 120,
        }
        anchors = {
            "seq": "center", "broker": "w",
            "buy": "e", "sell": "e", "net": "e",
        }

        for col in columns:
            self.tree.heading(
                col, text=headings[col],
                command=lambda c=col: self._sort_by(c),
            )
            self.tree.column(col, width=widths[col], anchor=anchors[col])

        # Color tags
        self.tree.tag_configure("buy", foreground="#4CAF50")
        self.tree.tag_configure("sell", foreground="#F44336")
        self.tree.tag_configure("neutral", foreground="gray")

        scrollbar = ttk.Scrollbar(self.table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=12)
        scrollbar.pack(side="right", fill="y", padx=(0, 12), pady=12)

    def _populate_table(self, stats: StatsResult):
        """Fill table rows from stats data."""
        if self.tree is None:
            return
        # Clear
        self.tree.delete(*self.tree.get_children())

        for i, b in enumerate(stats.brokers, 1):
            net = b.net
            tag = "buy" if net > 0 else ("sell" if net < 0 else "neutral")
            net_str = f"+{fmt_number(net)}" if net > 0 else fmt_number(net)
            self.tree.insert(
                "", "end",
                values=(
                    i,
                    b.broker_name,
                    fmt_number(b.buy_volume),
                    fmt_number(b.sell_volume),
                    net_str,
                ),
                tags=(tag,),
            )

    def _sort_by(self, col: str):
        """Sort treeview by clicking column header."""
        if self._current_stats is None:
            return

        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col
            self._sort_asc = True

        brokers = list(self._current_stats.brokers)

        key_map = {
            "seq": lambda b: 0,
            "broker": lambda b: b.broker_name,
            "buy": lambda b: b.buy_volume,
            "sell": lambda b: b.sell_volume,
            "net": lambda b: b.net,
        }
        if col != "seq":
            brokers.sort(key=key_map[col], reverse=not self._sort_asc)

        # Re-populate
        self.tree.delete(*self.tree.get_children())
        for i, b in enumerate(brokers, 1):
            net = b.net
            tag = "buy" if net > 0 else ("sell" if net < 0 else "neutral")
            net_str = f"+{fmt_number(net)}" if net > 0 else fmt_number(net)
            self.tree.insert(
                "", "end",
                values=(
                    i, b.broker_name,
                    fmt_number(b.buy_volume), fmt_number(b.sell_volume), net_str,
                ),
                tags=(tag,),
            )

        # Update heading to show sort indicator
        headings = {
            "seq": "序號", "broker": "券商",
            "buy": "買進股數", "sell": "賣出股數", "net": "淨買賣",
        }
        for c in headings:
            arrow = ""
            if c == col and col != "seq":
                arrow = " ▲" if self._sort_asc else " ▼"
            self.tree.heading(
                c, text=headings[c] + arrow,
                command=lambda cc=c: self._sort_by(cc),
            )

    # ------------------------------------------------------------------ Events
    def _on_download_click(self):
        stock_code = self.stock_entry.get().strip()
        self.vm.start_download(stock_code)

    def _bind_vm(self):
        self.vm.bind("status_text", self._on_status_changed)
        self.vm.bind("progress", self._on_progress_changed)
        self.vm.bind("is_downloading", self._on_downloading_changed)
        self.vm.bind("result_data", self._on_result_changed)
        self.vm.bind("error_text", self._on_error_changed)

    def _on_status_changed(self, value: str):
        self.after(0, lambda: self.status_label.configure(text=value))

    def _on_progress_changed(self, value: float):
        self.after(0, lambda: self.progress_bar.set(value))

    def _on_downloading_changed(self, value: bool):
        def _update():
            if value:
                self.download_btn.configure(state="disabled", text="下載中...")
            else:
                self.download_btn.configure(state="normal", text="開始下載")
        self.after(0, _update)

    def _on_error_changed(self, value: str):
        self.after(0, lambda: self.error_label.configure(text=value))

    def _on_result_changed(self, result):
        if result is None:
            return

        def _update():
            self.placeholder_label.pack_forget()

            # Compute stats
            stats = compute_stats(result)
            self._current_stats = stats

            # Unpack all result sections to re-order them
            self.summary_card.pack_forget()
            self.stats_frame.pack_forget()
            self.chart_frame.pack_forget()
            self.table_frame.pack_forget()

            # Summary
            summary_text = (
                f"股票：{result.stock_name}　　"
                f"交易日期：{result.trade_date}\n"
                f"成交筆數：{result.total_trades}　　"
                f"成交金額：{result.total_amount}　　"
                f"成交股數：{result.total_volume}\n"
                f"開盤：{result.open_price}　　"
                f"最高：{result.high_price}　　"
                f"最低：{result.low_price}　　"
                f"收盤：{result.close_price}"
            )
            self.summary_label.configure(text=summary_text)
            self.summary_card.pack(padx=40, pady=8, fill="x")

            # Stats section
            self._build_stats_section(stats)

            # Charts
            self._build_charts(stats)

            # Table (re-pack at the end)
            self.table_frame.pack(padx=40, pady=8, fill="both", expand=True)
            self._build_table()
            self._populate_table(stats)

        self.after(0, _update)
