"""分點分析 — search stocks, view broker positions, drill into daily charts."""

from __future__ import annotations

import customtkinter as ctk
from tkinter import ttk
import tkinter as tk
from datetime import datetime

from viewmodels.broker_analysis_viewmodel import BrokerAnalysisViewModel
from services.broker_tags import (
    get_broker_tags, TAG_COLORS, TAG_LABELS,
    TAG_DAY, TAG_NEXT, TAG_SHORT, TAG_SWING,
)

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


class _Tooltip:
    """Lightweight hover tooltip for any tkinter widget."""

    def __init__(self, widget, text: str):
        self._widget = widget
        self._text = text
        self._tw = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, event):
        x = self._widget.winfo_rootx() + 25
        y = self._widget.winfo_rooty() + 20
        tw = tk.Toplevel(self._widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.attributes("-topmost", True)
        lbl = tk.Label(
            tw, text=self._text,
            background="#333333", foreground="#e0e0e0",
            font=("Microsoft JhengHei", 11),
            padx=8, pady=4, relief="flat",
        )
        lbl.pack()
        self._tw = tw

    def _hide(self, event):
        if self._tw:
            self._tw.destroy()
            self._tw = None


def _parse_price(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").replace(" ", ""))
    except (ValueError, TypeError):
        return None


def _fmt(n: int) -> str:
    return f"{n:,}"


def _to_lots(shares: int) -> int:
    """Convert shares to lots (張). 1張 = 1,000股."""
    return shares // 1000


def _fmt_lots(shares: int) -> str:
    """Format shares as lots with thousands separator."""
    return _fmt(_to_lots(shares))


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
            font=ctk.CTkFont(size=14), text_color="gray",
        ).pack(pady=(0, 24))

        # --- Tag ranking section ---
        tag_card = ctk.CTkFrame(self.container, corner_radius=12)
        tag_card.pack(padx=40, pady=8, fill="x")

        tag_hdr = ctk.CTkFrame(tag_card, fg_color="transparent")
        tag_hdr.pack(fill="x", padx=20, pady=(14, 4))
        ctk.CTkLabel(tag_hdr, text="主力分點排行",
                      font=ctk.CTkFont(size=16, weight="bold")).pack(side="left")

        tag_date_row = ctk.CTkFrame(tag_card, fg_color="transparent")
        tag_date_row.pack(fill="x", padx=20, pady=(2, 6))
        ctk.CTkLabel(tag_date_row, text="交易日期：",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.tag_date_entry = ctk.CTkEntry(
            tag_date_row, width=130, font=ctk.CTkFont(size=14),
            placeholder_text="yyyy-mm-dd")
        self.tag_date_entry.pack(side="left", padx=(4, 8))
        from datetime import datetime as _dt
        self.tag_date_entry.insert(0, _dt.now().strftime("%Y-%m-%d"))
        self.tag_query_btn = ctk.CTkButton(
            tag_date_row, text="查詢", width=80, height=32,
            corner_radius=8, font=ctk.CTkFont(size=14, weight="bold"),
            command=self._on_tag_query)
        self.tag_query_btn.pack(side="left")
        self.tag_error_label = ctk.CTkLabel(
            tag_date_row, text="", font=ctk.CTkFont(size=13),
            text_color="#FF6B6B")
        self.tag_error_label.pack(side="left", padx=(12, 0))

        # Three tables
        self._tag_trees: dict[str, ttk.Treeview] = {}
        tag_tables = ctk.CTkFrame(tag_card, fg_color="transparent")
        tag_tables.pack(fill="x", padx=8, pady=(0, 12))
        tag_tables.columnconfigure(0, weight=1)
        tag_tables.columnconfigure(1, weight=1)
        tag_tables.columnconfigure(2, weight=1)

        for col, (tag, title) in enumerate([
            (TAG_DAY, "當沖主力"), (TAG_NEXT, "隔日沖主力"),
            (TAG_SHORT, "短線主力"),
        ]):
            f = ctk.CTkFrame(tag_tables, corner_radius=8)
            f.grid(row=0, column=col, sticky="nsew", padx=3, pady=2)

            hdr_f = ctk.CTkFrame(f, fg_color="transparent")
            hdr_f.pack(fill="x", padx=10, pady=(8, 2))
            ctk.CTkLabel(hdr_f, text="●",
                          font=ctk.CTkFont(size=12),
                          text_color=TAG_COLORS[tag]).pack(side="left")
            ctk.CTkLabel(hdr_f, text=f" {title} 買超佔比 Top20",
                          font=ctk.CTkFont(size=12, weight="bold")).pack(side="left")

            tree_f = ctk.CTkFrame(f, fg_color="transparent")
            tree_f.pack(fill="both", expand=True, padx=4, pady=(0, 6))

            columns = ("rank", "code", "name", "price", "ratio")
            tree = ttk.Treeview(
                tree_f, columns=columns, show="headings",
                style="TagRank.Treeview", height=10)
            for c, txt, w, anc in [
                ("rank", "#", 28, "center"), ("code", "代碼", 50, "center"),
                ("name", "名稱", 70, "w"), ("price", "收盤", 55, "e"),
                ("ratio", "佔比%", 52, "e"),
            ]:
                tree.heading(c, text=txt)
                tree.column(c, width=w, anchor=anc, stretch=True)

            tree.tag_configure("hot", foreground=TAG_COLORS[tag])
            tree.bind("<<TreeviewSelect>>",
                       lambda e, t=tree: self._on_tag_tree_select(t))

            sb = ttk.Scrollbar(tree_f, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=sb.set)
            tree.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=2)
            sb.pack(side="right", fill="y", padx=(0, 4), pady=2)

            self._tag_trees[tag] = tree

        # Ensure dark style for these trees
        self._ensure_tag_tree_style()

        # --- Search card ---
        search_card = ctk.CTkFrame(self.container, corner_radius=12)
        search_card.pack(padx=40, pady=8, fill="x")

        search_row = ctk.CTkFrame(search_card, fg_color="transparent")
        search_row.pack(fill="x", padx=20, pady=16)

        ctk.CTkLabel(search_row, text="股票搜尋：",
                      font=ctk.CTkFont(size=14)).pack(side="left")

        self.search_entry = ctk.CTkEntry(
            search_row, width=200, placeholder_text="代碼或名稱",
            font=ctk.CTkFont(size=14),
        )
        self.search_entry.pack(side="left", padx=(4, 8))
        self.search_entry.bind("<Return>", lambda e: self._on_search())

        self.search_btn = ctk.CTkButton(
            search_row, text="搜尋", width=80, height=32,
            corner_radius=8, font=ctk.CTkFont(size=14),
            command=self._on_search,
        )
        self.search_btn.pack(side="left")

        self.error_label = ctk.CTkLabel(
            search_row, text="", font=ctk.CTkFont(size=14),
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
            info_row, text="", font=ctk.CTkFont(size=17, weight="bold"))
        self.stock_title_label.pack(side="left")

        # Volume info row (multiple labels for different colors)
        self.vol_info_frame = ctk.CTkFrame(
            self.stock_info_card, fg_color="transparent")
        self.vol_info_frame.pack(fill="x", padx=22, pady=(0, 4))

        date_row = ctk.CTkFrame(self.stock_info_card, fg_color="transparent")
        date_row.pack(fill="x", padx=20, pady=(0, 16))
        ctk.CTkLabel(date_row, text="日期區間：",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.date_start_entry = ctk.CTkEntry(
            date_row, width=110, font=ctk.CTkFont(size=14),
            placeholder_text="yyyy-mm-dd")
        self.date_start_entry.pack(side="left", padx=(4, 4))
        ctk.CTkLabel(date_row, text="~",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.date_end_entry = ctk.CTkEntry(
            date_row, width=110, font=ctk.CTkFont(size=14),
            placeholder_text="yyyy-mm-dd")
        self.date_end_entry.pack(side="left", padx=(4, 8))
        self.date_apply_btn = ctk.CTkButton(
            date_row, text="套用", width=60, height=28,
            corner_radius=6, font=ctk.CTkFont(size=14),
            command=self._on_date_apply)
        self.date_apply_btn.pack(side="left")
        self.date_info_label = ctk.CTkLabel(
            date_row, text="", font=ctk.CTkFont(size=14), text_color="gray")
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

        # Broker-type volume share stats
        self.tag_stats_frame = ctk.CTkFrame(
            self.ranking_card, fg_color="transparent")
        self.tag_stats_frame.pack(fill="x", padx=16, pady=(0, 4))

        self.rank_list_frame = ctk.CTkFrame(
            self.ranking_card, fg_color="transparent")
        self.rank_list_frame.pack(fill="both", expand=True, padx=12, pady=(0, 14))

        # --- Holder distribution card ---
        self.holder_card = ctk.CTkFrame(self.container, corner_radius=12)

        holder_hdr = ctk.CTkFrame(self.holder_card, fg_color="transparent")
        holder_hdr.pack(fill="x", padx=20, pady=(16, 4))
        ctk.CTkLabel(
            holder_hdr, text="大戶 / 散戶持股比例",
            font=ctk.CTkFont(size=17, weight="bold"),
        ).pack(side="left")
        self.holder_refresh_btn = ctk.CTkButton(
            holder_hdr, text="從 TDCC 更新", width=110, height=28,
            corner_radius=6, font=ctk.CTkFont(size=14),
            command=self._on_refresh_holder,
        )
        self.holder_refresh_btn.pack(side="right")

        self.holder_info_frame = ctk.CTkFrame(
            self.holder_card, fg_color="transparent")
        self.holder_info_frame.pack(fill="x", padx=16, pady=(0, 4))

        self.holder_chart_frame = ctk.CTkFrame(
            self.holder_card, fg_color="transparent")
        self.holder_chart_frame.pack(fill="x", padx=8, pady=(0, 16))

        # --- Institutional (三大法人) card ---
        self.insti_card = ctk.CTkFrame(self.container, corner_radius=12)

        insti_hdr = ctk.CTkFrame(self.insti_card, fg_color="transparent")
        insti_hdr.pack(fill="x", padx=20, pady=(16, 4))
        ctk.CTkLabel(insti_hdr, text="三大法人 / 自營商避險",
                      font=ctk.CTkFont(size=16, weight="bold")).pack(side="left")

        self.insti_content_frame = ctk.CTkFrame(
            self.insti_card, fg_color="transparent")
        self.insti_content_frame.pack(fill="x", padx=16, pady=(0, 16))

        # --- Detail chart area ---
        self.detail_card = ctk.CTkFrame(self.container, corner_radius=12)

        self.detail_header = ctk.CTkFrame(self.detail_card, fg_color="transparent")
        self.detail_header.pack(fill="x", padx=20, pady=(16, 4))
        self.detail_title_label = ctk.CTkLabel(
            self.detail_header, text="",
            font=ctk.CTkFont(size=17, weight="bold"))
        self.detail_title_label.pack(side="left")

        # Chart frame (matplotlib goes here)
        self.chart_frame = ctk.CTkFrame(self.detail_card, fg_color="transparent")
        self.chart_frame.pack(fill="x", padx=8, pady=(4, 0))

        # Info below chart: 券商損益 line
        self.broker_info_label = ctk.CTkLabel(
            self.detail_card, text="", font=ctk.CTkFont(size=14),
            text_color="#c0c0c0", anchor="w")
        self.broker_info_label.pack(fill="x", padx=24, pady=(2, 4))

        # Summary boxes row
        self.summary_row = ctk.CTkFrame(self.detail_card, fg_color="transparent")
        self.summary_row.pack(fill="x", padx=20, pady=(0, 8))

        self.box_net = ctk.CTkFrame(self.summary_row, corner_radius=8,
                                     border_width=2, border_color="#e88a1a")
        self.box_net.pack(side="left", fill="x", expand=True, padx=4)
        self.box_net_title = ctk.CTkLabel(
            self.box_net, text="區間買賣超 (張)", font=ctk.CTkFont(size=14),
            text_color="gray")
        self.box_net_title.pack(pady=(6, 0))
        self.box_net_value = ctk.CTkLabel(
            self.box_net, text="", font=ctk.CTkFont(size=17, weight="bold"),
            text_color="#ef5350")
        self.box_net_value.pack(pady=(0, 6))

        self.box_amt = ctk.CTkFrame(self.summary_row, corner_radius=8,
                                     border_width=2, border_color="#e88a1a")
        self.box_amt.pack(side="left", fill="x", expand=True, padx=4)
        self.box_amt_title = ctk.CTkLabel(
            self.box_amt, text="區間買賣超金額 (萬)", font=ctk.CTkFont(size=14),
            text_color="gray")
        self.box_amt_title.pack(pady=(6, 0))
        self.box_amt_value = ctk.CTkLabel(
            self.box_amt, text="", font=ctk.CTkFont(size=17, weight="bold"),
            text_color="#ef5350")
        self.box_amt_value.pack(pady=(0, 6))

        # Day selector row
        day_row = ctk.CTkFrame(self.detail_card, fg_color="transparent")
        day_row.pack(fill="x", padx=20, pady=(4, 14))
        ctk.CTkLabel(day_row, text="統計天數",
                      font=ctk.CTkFont(size=14)).pack(side="left", padx=(0, 8))
        for d in [1, 3, 5, 10, 20, 60, 120, 240]:
            ctk.CTkButton(
                day_row, text=str(d), width=38, height=28, corner_radius=6,
                font=ctk.CTkFont(size=14),
                fg_color="#3a3a3a", hover_color="#555555",
                command=lambda days=d: self._on_day_select(days),
            ).pack(side="left", padx=2)

    # ================================================================ Events

    def _on_tag_query(self):
        self.vm.load_tag_rankings(self.tag_date_entry.get().strip())

    def _on_tag_tree_select(self, tree: ttk.Treeview):
        sel = tree.selection()
        if not sel:
            return
        vals = tree.item(sel[0])["values"]
        code = str(vals[1])  # stock_code
        # Auto-fill search and trigger
        self.search_entry.delete(0, "end")
        self.search_entry.insert(0, code)
        self._on_search()

    def _ensure_tag_tree_style(self):
        s = ttk.Style()
        name = "TagRank.Treeview"
        s.configure(name,
                    background="#252526", foreground="#d4d4d4",
                    fieldbackground="#252526", borderwidth=0,
                    rowheight=26, font=("Microsoft JhengHei", 11))
        s.map(name,
              background=[("selected", "#264f78")],
              foreground=[("selected", "#ffffff")])
        s.configure(f"{name}.Heading",
                    background="#2d2d2d", foreground="#cccccc",
                    borderwidth=0, relief="flat",
                    font=("Microsoft JhengHei", 11, "bold"))
        s.map(f"{name}.Heading",
              background=[("active", "#3e3e3e")])

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
        self.vm.bind("volume_info", self._on_volume_info)
        self.vm.bind("holder_data", self._on_holder_data)
        self.vm.bind("holder_loading", self._on_holder_loading)
        self.vm.bind("insti_data", self._on_insti_data)
        self.vm.bind("tag_rankings", self._on_tag_rankings)
        self.vm.bind("tag_rankings_loading", self._on_tag_rankings_loading)
        self.vm.bind("tag_rankings_error", self._on_tag_rankings_error)

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
                self.insti_card.pack_forget()
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
            self.insti_card.pack(padx=40, pady=8, fill="x")
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
            self._build_tag_stats(brokers)
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

    def _on_volume_info(self, info):
        def _u():
            for w in self.vol_info_frame.winfo_children():
                w.destroy()
            if not info:
                return

            fs = ctk.CTkFont(size=14)
            fsb = ctk.CTkFont(size=14, weight="bold")
            gray = "#888888"
            sp = "　"

            date = info.get("trade_date", "")
            price_raw = info.get("close_price", "")
            vol_raw = info.get("total_volume", 0)
            price_chg = info.get("price_change")
            price_chg_pct = info.get("price_change_pct")
            vol_chg = info.get("vol_change_pct")

            try:
                vol_int = int(str(vol_raw).replace(",", "").replace(" ", ""))
            except (ValueError, TypeError):
                vol_int = 0
            try:
                price_f = float(str(price_raw).replace(",", ""))
            except (ValueError, TypeError):
                price_f = None

            # Date
            ctk.CTkLabel(self.vol_info_frame, text=f"最新交易日：{date}{sp}",
                          font=fs, text_color=gray).pack(side="left")

            # Price
            if price_f is not None:
                # Determine price color
                if price_chg is not None and price_chg > 0:
                    p_clr = "#ef5350"
                    arrow = "▲"
                elif price_chg is not None and price_chg < 0:
                    p_clr = "#26a69a"
                    arrow = "▼"
                else:
                    p_clr = "#c0c0c0"
                    arrow = ""

                ctk.CTkLabel(self.vol_info_frame, text=f"{price_f:,.2f}",
                              font=fsb, text_color=p_clr).pack(side="left")

                if price_chg is not None and price_chg != 0:
                    sign = "+" if price_chg > 0 else ""
                    pct_str = ""
                    if price_chg_pct is not None:
                        pct_str = f"({sign}{price_chg_pct:.2f}%)"
                    ctk.CTkLabel(
                        self.vol_info_frame,
                        text=f" {arrow}{sign}{price_chg:.2f} {pct_str}{sp}",
                        font=fs, text_color=p_clr,
                    ).pack(side="left")
                else:
                    ctk.CTkLabel(self.vol_info_frame, text=sp,
                                  font=fs, text_color=gray).pack(side="left")

            # Volume
            ctk.CTkLabel(self.vol_info_frame,
                          text=f"成交量 {vol_int // 1000:,} 張",
                          font=fs, text_color="#c0c0c0").pack(side="left")

            # Volume change
            if vol_chg is not None:
                if vol_chg > 0:
                    v_txt = f" ▲量增 {vol_chg:.1f}%"
                    v_clr = "#ef5350"
                elif vol_chg < 0:
                    v_txt = f" ▼量縮 {abs(vol_chg):.1f}%"
                    v_clr = "#26a69a"
                else:
                    v_txt = " 量平"
                    v_clr = "#888888"
                ctk.CTkLabel(self.vol_info_frame, text=v_txt,
                              font=fsb, text_color=v_clr).pack(side="left")

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
                font=ctk.CTkFont(size=14), text_color="#FF6B6B",
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
            ctk.CTkLabel(f, text=label, font=ctk.CTkFont(size=14),
                          text_color="gray").pack(padx=10, pady=(5, 0))
            ctk.CTkLabel(f, text=value,
                          font=ctk.CTkFont(size=17, weight="bold"),
                          text_color=color).pack(padx=10, pady=(0, 5))

        # Trend chart (if history available)
        if not history or not HAS_MPL or len(history) < 2:
            if len(history) < 2:
                ctk.CTkLabel(
                    self.holder_chart_frame,
                    text="（累積 2 週以上資料後可顯示趨勢圖）",
                    font=ctk.CTkFont(size=14), text_color="gray",
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

        ax.set_ylabel("%", fontsize=11, color=txt)
        ax.tick_params(axis="both", colors=txt, labelsize=7)
        for sp in ax.spines.values():
            sp.set_color(grid)
        ax.grid(True, alpha=0.2, color=grid, linewidth=0.5)
        ax.legend(loc="upper left", fontsize=11, framealpha=0.5,
                  facecolor=panel, edgecolor=grid, labelcolor=txt, ncol=3)

        ax.set_xticks(xs)
        ax.set_xticklabels(labels, fontsize=11, color=txt, rotation=35, ha="right")

        canvas = FigureCanvasTkAgg(fig, self.holder_chart_frame)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="x", padx=4, pady=(4, 4))

    def _on_insti_data(self, data):
        def _u():
            for w in self.insti_content_frame.winfo_children():
                w.destroy()
            if not data:
                ctk.CTkLabel(
                    self.insti_content_frame,
                    text="（尚無三大法人資料，請至系統設定下載）",
                    font=ctk.CTkFont(size=13), text_color="gray",
                ).pack(pady=8)
                return

            # Latest day summary
            latest = data[-1]
            kpi_row = ctk.CTkFrame(self.insti_content_frame, fg_color="transparent")
            kpi_row.pack(fill="x", pady=(4, 4))

            def _lots(v): return _fmt(v // 1000) if v else "0"
            def _clr(v): return "#ef5350" if v >= 0 else "#26a69a"

            for label, val in [
                ("外資", latest["foreign_net"]),
                ("投信", latest["trust_net"]),
                ("自營(自行)", latest["dealer_self_net"]),
                ("自營(避險)", latest["dealer_hedge_net"]),
                ("三大法人", latest["three_insti_net"]),
            ]:
                f = ctk.CTkFrame(kpi_row, corner_radius=8, fg_color="#1e1e1e")
                f.pack(side="left", padx=4, pady=2)
                ctk.CTkLabel(f, text=label, font=ctk.CTkFont(size=12),
                              text_color="gray").pack(padx=10, pady=(5, 0))
                sign = "+" if val > 0 else ""
                ctk.CTkLabel(
                    f, text=f"{sign}{_lots(val)}",
                    font=ctk.CTkFont(size=14, weight="bold"),
                    text_color=_clr(val),
                ).pack(padx=10, pady=(0, 5))

            ctk.CTkLabel(
                self.insti_content_frame,
                text=f"最新日期：{latest['trade_date']}　單位：張",
                font=ctk.CTkFont(size=11), text_color="gray",
            ).pack(anchor="w", padx=8, pady=(0, 4))

            # Trend chart if enough data
            if len(data) >= 2 and HAS_MPL:
                self._render_insti_chart(data)

        self.after(0, _u)

    def _render_insti_chart(self, data: list[dict]):
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

        n = len(data)
        xs = list(range(n))
        hedge = [d["dealer_hedge_net"] // 1000 for d in data]
        foreign = [d["foreign_net"] // 1000 for d in data]
        trust = [d["trust_net"] // 1000 for d in data]
        labels = [d["trade_date"][5:] for d in data]

        bg = "#1c1c1e"
        panel = "#1c1c1e"
        txt = "#c0c0c0"
        grid = "#2c2c2e"

        fig = Figure(figsize=(8, 2.5), dpi=100, facecolor=bg)
        fig.subplots_adjust(left=0.08, right=0.96, top=0.88, bottom=0.18)
        ax = fig.add_subplot(111)
        ax.set_facecolor(panel)

        bar_w = 0.55
        bar_clrs = ["#ef5350" if v >= 0 else "#26a69a" for v in hedge]
        ax.bar(xs, hedge, width=bar_w, color=bar_clrs, alpha=0.8,
               label="自營避險(張)")

        ax.plot(xs, foreign, color="#42a5f5", linewidth=1.2,
                marker="o", markersize=2, label="外資", alpha=0.8)
        ax.plot(xs, trust, color="#ff9800", linewidth=1.2,
                marker="s", markersize=2, label="投信", alpha=0.8)

        ax.axhline(0, color=grid, linewidth=0.5)
        ax.set_ylabel("張", fontsize=10, color=txt)
        ax.tick_params(axis="both", colors=txt, labelsize=8)
        for sp in ax.spines.values():
            sp.set_color(grid)
        ax.grid(True, axis="y", alpha=0.2, color=grid, linewidth=0.5)
        ax.legend(loc="upper left", fontsize=9, framealpha=0.5,
                  facecolor=panel, edgecolor=grid, labelcolor=txt, ncol=3)

        if n <= 15:
            ticks = xs
        else:
            step = max(n // 10, 1)
            ticks = list(range(0, n, step))
            if ticks[-1] != n - 1:
                ticks.append(n - 1)
        ax.set_xticks(ticks)
        ax.set_xticklabels([labels[t] for t in ticks],
                            fontsize=8, color=txt, rotation=35, ha="right")
        ax.set_xlim(-0.6, n - 0.4)

        ax.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda x, _: f"{int(x):,}"))

        canvas = FigureCanvasTkAgg(fig, self.insti_content_frame)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="x", padx=4, pady=(4, 4))

    def _on_tag_rankings_loading(self, v):
        def _u():
            if v:
                self.tag_query_btn.configure(state="disabled", text="查詢中...")
            else:
                self.tag_query_btn.configure(state="normal", text="查詢")
        self.after(0, _u)

    def _on_tag_rankings_error(self, v):
        self.after(0, lambda: self.tag_error_label.configure(text=v))

    def _on_tag_rankings(self, data):
        def _u():
            for tag, tree in self._tag_trees.items():
                tree.delete(*tree.get_children())
            if data is None:
                return
            for tag, tree in self._tag_trees.items():
                rows = data.get(tag, [])
                for i, r in enumerate(rows, 1):
                    price = r.get("close_price", "")
                    try:
                        price = f"{float(str(price).replace(',', '')):,.2f}"
                    except (ValueError, TypeError):
                        pass
                    tag_style = "hot" if r["ratio"] >= 10 else ""
                    tree.insert(
                        "", "end",
                        values=(i, r["stock_code"], r["stock_name"],
                                price, f"{r['ratio']:.1f}"),
                        tags=(tag_style,) if tag_style else (),
                    )
        self.after(0, _u)

    def _on_correlation_loading(self, v):
        def _u():
            if v:
                for w in self.rank_list_frame.winfo_children():
                    w.destroy()
                ctk.CTkLabel(
                    self.rank_list_frame, text="分析中...",
                    font=ctk.CTkFont(size=14), text_color="gray",
                ).pack(pady=20)
        self.after(0, _u)

    def _on_correlation_data(self, data):
        def _u():
            if data is None:
                return
            self._render_correlation_list(data)
        self.after(0, _u)

    # ================================================================ Ranking list

    def _build_tag_stats(self, brokers: list[dict]):
        """Show net-buy (買超) volume share % for each broker-type tag."""
        for w in self.tag_stats_frame.winfo_children():
            w.destroy()

        # Total net volume across all brokers (absolute sum for denominator)
        total_vol = sum(
            b.get("buy_volume", 0) + b.get("sell_volume", 0)
            for b in brokers
        )
        if total_vol == 0:
            return

        # Accumulate net (buy - sell) per tag
        tag_net: dict[str, int] = {
            TAG_DAY: 0, TAG_NEXT: 0, TAG_SHORT: 0, TAG_SWING: 0,
        }
        for b in brokers:
            bv = b.get("buy_volume", 0)
            sv = b.get("sell_volume", 0)
            net = bv - sv
            if net <= 0:
                continue
            tags = get_broker_tags(b["broker_name"])
            for t in tags:
                if t in tag_net:
                    tag_net[t] += net

        # Render
        for tag, label in [
            (TAG_DAY, "當沖"), (TAG_NEXT, "隔日沖"),
            (TAG_SHORT, "短線"), (TAG_SWING, "波段"),
        ]:
            pct = tag_net[tag] / total_vol * 100 if total_vol > 0 else 0
            color = TAG_COLORS[tag]

            f = ctk.CTkFrame(self.tag_stats_frame, fg_color="#1e1e1e",
                              corner_radius=8)
            f.pack(side="left", padx=4, pady=2)

            ctk.CTkLabel(
                f, text=tag, width=22, height=20,
                font=ctk.CTkFont(size=10, weight="bold"),
                text_color="#1e1e1e", fg_color=color,
                corner_radius=4,
            ).pack(side="left", padx=(8, 4), pady=6)

            ctk.CTkLabel(
                f, text=f"{label} 買超佔比",
                font=ctk.CTkFont(size=12), text_color="#888888",
            ).pack(side="left", pady=6)

            ctk.CTkLabel(
                f, text=f"{pct:.1f}%",
                font=ctk.CTkFont(size=14, weight="bold"),
                text_color=color,
            ).pack(side="left", padx=(6, 10), pady=6)

        # Dealer hedge ratio (自營比) from institutional data
        insti = self.vm.insti_data
        if insti and total_vol > 0:
            # 自營商買超 / 成交量 * 100%
            hedge_buy = sum(d.get("dealer_hedge_buy", 0) for d in insti)
            hedge_sell = sum(d.get("dealer_hedge_sell", 0) for d in insti)
            hedge_net = hedge_buy - hedge_sell
            hedge_pct = hedge_net / total_vol * 100
            hedge_clr = "#ef5350" if hedge_net >= 0 else "#26a69a"

            f = ctk.CTkFrame(self.tag_stats_frame, fg_color="#1e1e1e",
                              corner_radius=8)
            f.pack(side="left", padx=4, pady=2)

            ctk.CTkLabel(
                f, text="自", width=22, height=20,
                font=ctk.CTkFont(size=10, weight="bold"),
                text_color="#1e1e1e", fg_color="#ab47bc",
                corner_radius=4,
            ).pack(side="left", padx=(8, 4), pady=6)

            ctk.CTkLabel(
                f, text="自營避險佔比",
                font=ctk.CTkFont(size=12), text_color="#888888",
            ).pack(side="left", pady=6)

            sign = "+" if hedge_pct > 0 else ""
            ctk.CTkLabel(
                f, text=f"{sign}{hedge_pct:.1f}%",
                font=ctk.CTkFont(size=14, weight="bold"),
                text_color=hedge_clr,
            ).pack(side="left", padx=(6, 10), pady=6)

    def _render_rank_list(self, tab: str):
        for w in self.rank_list_frame.winfo_children():
            w.destroy()

        is_buy = "買方" in tab
        rows = self._rank_buyers if is_buy else self._rank_sellers
        accent = "#ef5350" if not is_buy else "#26a69a"

        if not rows:
            ctk.CTkLabel(
                self.rank_list_frame, text="（無資料）",
                font=ctk.CTkFont(size=14), text_color="gray",
            ).pack(pady=20)
            return

        # Header
        hdr = ctk.CTkFrame(self.rank_list_frame, fg_color="transparent", height=32)
        hdr.pack(fill="x", padx=8, pady=(4, 0))
        hdr.pack_propagate(False)
        for text, w, anc in [("券商", 0.42, "w"), ("買賣超(張)", 0.28, "e"),
                              ("損益(萬)", 0.28, "e")]:
            lbl = ctk.CTkLabel(hdr, text=text, font=ctk.CTkFont(size=14),
                                text_color="gray")
            lbl.place(relx=w - 0.28 + 0.02 if anc == "w" else 0,
                      rely=0.5, anchor="w" if anc == "w" else "w")
        # manual placement
        ctk.CTkLabel(hdr, text="券商", font=ctk.CTkFont(size=14),
                      text_color="gray").place(relx=0.02, rely=0.5, anchor="w")
        ctk.CTkLabel(hdr, text="買賣超(張)", font=ctk.CTkFont(size=14),
                      text_color="gray").place(relx=0.58, rely=0.5, anchor="e")
        ctk.CTkLabel(hdr, text="損益(萬)", font=ctk.CTkFont(size=14),
                      text_color="gray").place(relx=0.97, rely=0.5, anchor="e")
        # clear the generic ones
        for w2 in list(hdr.winfo_children())[:-3]:
            w2.destroy()

        for i, b in enumerate(rows):
            bg = "#1e1e1e" if i % 2 == 0 else "#252526"
            row_f = ctk.CTkFrame(self.rank_list_frame, fg_color=bg,
                                  corner_radius=6, height=42)
            row_f.pack(fill="x", padx=8, pady=1)
            row_f.pack_propagate(False)

            net = b["net_volume"]
            net_lots = _to_lots(net)
            net_str = f"+{_fmt(net_lots)}" if net_lots > 0 else _fmt(net_lots)
            pnl = b.get("pnl", 0)
            pnl_val = round(pnl / 10000, 1)  # 萬
            pnl_str = f"+{pnl_val:,.1f}" if pnl > 0 else f"{pnl_val:,.1f}"
            pnl_clr = "#26a69a" if pnl >= 0 else "#ef5350"

            # Broker name + tags
            name_frame = ctk.CTkFrame(row_f, fg_color="transparent")
            name_frame.place(relx=0.02, rely=0.5, anchor="w")
            ctk.CTkLabel(
                name_frame, text=b["broker_name"],
                font=ctk.CTkFont(size=14), text_color="#e0e0e0",
            ).pack(side="left")
            for tag in get_broker_tags(b["broker_name"]):
                tag_lbl = ctk.CTkLabel(
                    name_frame, text=tag,
                    font=ctk.CTkFont(size=10, weight="bold"),
                    text_color="#1e1e1e",
                    fg_color=TAG_COLORS.get(tag, "#888"),
                    corner_radius=4, width=20, height=18,
                )
                tag_lbl.pack(side="left", padx=(3, 0))
                _Tooltip(tag_lbl, TAG_LABELS.get(tag, tag))

            ctk.CTkLabel(
                row_f, text=net_str,
                font=ctk.CTkFont(size=12, weight="bold"), text_color=accent,
            ).place(relx=0.58, rely=0.5, anchor="e")

            ctk.CTkLabel(
                row_f, text=pnl_str,
                font=ctk.CTkFont(size=14), text_color=pnl_clr,
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
                font=ctk.CTkFont(size=14), text_color="gray",
            ).pack(pady=20)
            return

        # Legend / hint panel
        hint_frame = ctk.CTkFrame(
            self.rank_list_frame, fg_color="#1e1e1e", corner_radius=8)
        hint_frame.pack(fill="x", padx=8, pady=(4, 6))

        hint_title_row = ctk.CTkFrame(hint_frame, fg_color="transparent")
        hint_title_row.pack(fill="x", padx=12, pady=(8, 2))
        ctk.CTkLabel(
            hint_title_row, text="欄位說明",
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color="#aaaaaa",
        ).pack(side="left")

        hints = [
            ("分數", "#ffeb3b",
             "綜合評分（0~1），越高 = 該分點操作與股價走勢關聯越強"),
            ("IC", "#26a69a",
             "資訊係數：綠色正值 = 買超後股價傾向漲，"
             "紅色負值 = 反指標"),
            ("lag", "#c0c0c0",
             "最佳領先天數：0d = 當日反映，2d = 操作領先股價 2 天"),
            ("連續", "#ffeb3b",
             "平均連續同方向天數：≥3（黃）= 有計畫佈局，<2（灰）= 隨機"),
            ("佔比%", "#c0c0c0",
             "該分點成交量佔全股成交量的百分比，越高影響力越大"),
            ("偏向", "#ba68c8",
             "買賣不對稱度：>50%（紫）= 幾乎單邊操作（主力特徵）"),
        ]
        for col_name, color, desc in hints:
            row = ctk.CTkFrame(hint_frame, fg_color="transparent")
            row.pack(fill="x", padx=12, pady=1)
            ctk.CTkLabel(
                row, text=col_name, width=50,
                font=ctk.CTkFont(size=13, weight="bold"),
                text_color=color,
            ).pack(side="left")
            ctk.CTkLabel(
                row, text=desc,
                font=ctk.CTkFont(size=12),
                text_color="#888888",
            ).pack(side="left", padx=(4, 0))

        # Color legend
        clr_row = ctk.CTkFrame(hint_frame, fg_color="transparent")
        clr_row.pack(fill="x", padx=12, pady=(4, 8))
        for label, clr in [
            ("■ 高關聯", "#ffeb3b"), ("■ 中關聯", "#ff9800"),
            ("■ 低關聯", "#8e8e93"), ("■ IC 正", "#26a69a"),
            ("■ IC 負", "#ef5350"), ("■ 單邊操作", "#ba68c8"),
        ]:
            ctk.CTkLabel(
                clr_row, text=label,
                font=ctk.CTkFont(size=12), text_color=clr,
            ).pack(side="left", padx=(0, 12))

        # Header
        hdr = ctk.CTkFrame(self.rank_list_frame, fg_color="transparent", height=32)
        hdr.pack(fill="x", padx=8, pady=(4, 0))
        hdr.pack_propagate(False)
        cols = [
            ("券商", 0.02, "w"), ("分數", 0.30, "e"), ("IC", 0.41, "e"),
            ("lag", 0.50, "e"), ("連續", 0.60, "e"), ("佔比%", 0.72, "e"),
            ("偏向", 0.83, "e"), ("天數", 0.95, "e"),
        ]
        for text, rx, anc in cols:
            ctk.CTkLabel(hdr, text=text, font=ctk.CTkFont(size=14),
                          text_color="gray").place(
                relx=rx, rely=0.5, anchor=anc)

        top = corr_list[:20]
        max_score = top[0].composite_score if top else 1.0

        for i, c in enumerate(top):
            bg = "#1e1e1e" if i % 2 == 0 else "#252526"
            row = ctk.CTkFrame(self.rank_list_frame, fg_color=bg,
                                corner_radius=6, height=42)
            row.pack(fill="x", padx=8, pady=1)
            row.pack_propagate(False)

            ratio = c.composite_score / max_score if max_score > 0 else 0
            score_clr = "#ffeb3b" if ratio > 0.7 else (
                "#ff9800" if ratio > 0.4 else "#8e8e93")
            ic_clr = "#26a69a" if c.ic_score > 0 else "#ef5350"

            # Streak color
            streak_clr = "#ffeb3b" if c.avg_streak >= 3 else (
                "#ff9800" if c.avg_streak >= 2 else "#8e8e93")

            # Asymmetry label
            asym_clr = "#ba68c8" if c.asymmetry > 0.5 else "#8e8e93"

            fs = ctk.CTkFont(size=14)
            fsb = ctk.CTkFont(size=11, weight="bold")

            nf = ctk.CTkFrame(row, fg_color="transparent")
            nf.place(relx=0.02, rely=0.5, anchor="w")
            ctk.CTkLabel(nf, text=c.broker_name, font=fs,
                          text_color="#e0e0e0").pack(side="left")
            for tag in get_broker_tags(c.broker_name):
                tag_lbl = ctk.CTkLabel(
                    nf, text=tag,
                    font=ctk.CTkFont(size=10, weight="bold"),
                    text_color="#1e1e1e",
                    fg_color=TAG_COLORS.get(tag, "#888"),
                    corner_radius=4, width=20, height=18,
                )
                tag_lbl.pack(side="left", padx=(3, 0))
                _Tooltip(tag_lbl, TAG_LABELS.get(tag, tag))

            ctk.CTkLabel(row, text=f"{c.composite_score:.3f}", font=fsb,
                          text_color=score_clr).place(
                relx=0.30, rely=0.5, anchor="e")

            ctk.CTkLabel(row, text=f"{c.ic_score:+.2f}", font=fs,
                          text_color=ic_clr).place(
                relx=0.41, rely=0.5, anchor="e")

            ctk.CTkLabel(row, text=f"{c.cross_corr_lag}d", font=fs,
                          text_color="#c0c0c0").place(
                relx=0.50, rely=0.5, anchor="e")

            ctk.CTkLabel(row, text=f"{c.avg_streak:.1f}", font=fs,
                          text_color=streak_clr).place(
                relx=0.60, rely=0.5, anchor="e")

            ctk.CTkLabel(row, text=f"{c.volume_share_pct:.2f}", font=fs,
                          text_color="#c0c0c0").place(
                relx=0.72, rely=0.5, anchor="e")

            ctk.CTkLabel(row, text=f"{c.asymmetry:.0%}", font=fs,
                          text_color=asym_clr).place(
                relx=0.83, rely=0.5, anchor="e")

            ctk.CTkLabel(row, text=str(c.active_days), font=fs,
                          text_color="#8e8e93").place(
                relx=0.95, rely=0.5, anchor="e")

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
        net_lots = _to_lots(net)
        net_clr = "#ef5350" if net >= 0 else "#26a69a"
        self.box_net_value.configure(text=_fmt(net_lots), text_color=net_clr)

        # 區間金額 (萬) = net_volume(股) * avg_price / 10000
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
                          font=ctk.CTkFont(size=14),
                          text_color="gray").pack(pady=16)
            return

        prices = data.get("prices", [])
        broker_daily = data.get("broker_daily", [])
        if not prices:
            ctk.CTkLabel(self.chart_frame, text="（該區間無價格資料）",
                          font=ctk.CTkFont(size=14),
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
                          font=ctk.CTkFont(size=14),
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

        # ---- Volume bars (primary visual, left y-axis, in 張) ----
        b_net_lots = [v // 1000 for v in b_net]
        bar_w = 0.55
        bar_clrs = ["#ef5350" if v >= 0 else "#26a69a" for v in b_net_lots]
        ax.bar(xs, b_net_lots, width=bar_w, color=bar_clrs, alpha=0.85, zorder=5)

        ax.axhline(0, color=grid_clr, linewidth=0.6, zorder=4)
        ax.set_ylabel("淨量(張)", fontsize=11, color=vol_axis_clr, labelpad=4)
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
        ax_price.set_ylabel("收盤價", fontsize=11, color=txt, labelpad=4)
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
        fig.text(0.06, 0.97, "分點進出", fontsize=11, color=txt_w,
                 va="top", fontweight="bold")
        cum_str = f"累積買賣超 (張) {_fmt_lots(cum_net[-1] if cum_net else 0)}"
        fig.text(0.20, 0.97, cum_str, fontsize=11, color="#ffeb3b", va="top")

        detail_str = (
            f"買賣超 (張) {_fmt_lots(net_vol)}  "
            f"買張 {_fmt_lots(total_buy)}  賣張 {_fmt_lots(total_sell)}"
        )
        fig.text(0.20, 0.91, detail_str, fontsize=11, color=net_clr, va="top")

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
                                fontsize=11, color=txt, rotation=35, ha="right")

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
            fontsize=11, color=txt_w,
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

        net_l = _to_lots(net)
        buy_l = _to_lots(buy)
        sell_l = _to_lots(sell)
        net_s = f"+{_fmt(net_l)}" if net_l > 0 else _fmt(net_l)
        info = (
            f"{cd['labels'][idx]}\n"
            f"收盤 {price:.2f}\n"
            f"買 {_fmt(buy_l)} 張  賣 {_fmt(sell_l)} 張\n"
            f"淨量 {net_s} 張"
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
