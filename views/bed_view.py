"""View — 「空方發動偵測（BED）」分頁 · 即時觀察（Phase 7）。

控制列 + 狀態卡 + 主圖(價/VWAP/事件 + 空方分數) + 五檔 / Detector / 事件 三表。
沿用桌面既有技術：customtkinter + matplotlib(FigureCanvasTkAgg) + ttk.Treeview(clam)。
"""

from __future__ import annotations

import customtkinter as ctk
from tkinter import ttk

try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    _MPL = True
except Exception:
    _MPL = False

from viewmodels.bed_viewmodel import BedViewModel

_BG = "#1a222c"
_GRID = "#2b3948"


class BedView(ctk.CTkFrame):
    def __init__(self, parent, viewmodel: BedViewModel):
        super().__init__(parent, fg_color="transparent")
        self.vm = viewmodel
        self._tiles: dict[str, ctk.CTkLabel] = {}
        self._canvas = None
        self._ax_price = None
        self._ax_score = None
        self._build_ui()
        self._bind_vm()

    # ---------------- UI ----------------
    def _build_ui(self):
        container = ctk.CTkScrollableFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True)

        hdr = ctk.CTkFrame(container, corner_radius=12)
        hdr.pack(fill="x", padx=24, pady=(12, 6))
        ctk.CTkLabel(hdr, text="📉 空方發動偵測（BED）· 即時觀察",
                     font=ctk.CTkFont(size=17, weight="bold")).pack(
                         side="left", padx=16, pady=10)
        ctk.CTkLabel(hdr, text="拉高失敗→跌破VWAP→Lower High→跌破微結構低點→空方發動（研究訊號，不下單）",
                     font=ctk.CTkFont(size=11), text_color="#8a94a0").pack(side="left")

        # 控制列
        ctrl = ctk.CTkFrame(container, corner_radius=12)
        ctrl.pack(fill="x", padx=24, pady=6)
        row = ctk.CTkFrame(ctrl, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=12)
        ctk.CTkLabel(row, text="股票代碼：", font=ctk.CTkFont(size=13)).pack(side="left")
        self.code_entry = ctk.CTkEntry(row, width=140, placeholder_text="例：2330")
        self.code_entry.pack(side="left", padx=(4, 12))
        self.start_btn = ctk.CTkButton(row, text="開始追蹤", width=100, height=32,
                                       fg_color="#b3453b", hover_color="#8f372f",
                                       font=ctk.CTkFont(size=13, weight="bold"),
                                       command=self._on_start)
        self.start_btn.pack(side="left")
        self.stop_btn = ctk.CTkButton(row, text="停止", width=72, height=32,
                                      fg_color="#455a64", hover_color="#37474f",
                                      command=self._on_stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))
        self.msg_label = ctk.CTkLabel(ctrl, text="尚未追蹤（即時行情需先於『下單/大單追蹤』連線永豐正式環境）",
                                      font=ctk.CTkFont(size=12), text_color="#9aa4ad")
        self.msg_label.pack(anchor="w", padx=16, pady=(0, 10))

        # 狀態卡
        st = ctk.CTkFrame(container, corner_radius=12)
        st.pack(fill="x", padx=24, pady=6)
        grid = ctk.CTkFrame(st, fg_color="transparent")
        grid.pack(fill="x", padx=12, pady=12)
        specs = [("price", "最新價"), ("chg", "漲跌%"), ("vwap", "VWAP"),
                 ("open", "開盤"), ("dist", "距VWAP(跳)"), ("bear", "空方分數"),
                 ("state", "狀態"), ("flow", "買/賣流"), ("spread", "點差(跳)"),
                 ("struct", "結構/成交/簿")]
        for i, (k, lab) in enumerate(specs):
            cell = ctk.CTkFrame(grid, corner_radius=8)
            cell.grid(row=i // 5, column=i % 5, padx=5, pady=5, sticky="nsew")
            grid.grid_columnconfigure(i % 5, weight=1)
            ctk.CTkLabel(cell, text=lab, font=ctk.CTkFont(size=11),
                         text_color="#888").pack(pady=(6, 0))
            v = ctk.CTkLabel(cell, text="—", font=ctk.CTkFont(size=15, weight="bold"))
            v.pack(pady=(0, 6))
            self._tiles[k] = v

        # 主圖
        chart_card = ctk.CTkFrame(container, corner_radius=12)
        chart_card.pack(fill="both", expand=True, padx=24, pady=6)
        self._build_chart(chart_card)

        # 五檔 + Detector
        mid = ctk.CTkFrame(container, fg_color="transparent")
        mid.pack(fill="both", expand=True, padx=24, pady=6)
        left = ctk.CTkFrame(mid, corner_radius=12)
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))
        ctk.CTkLabel(left, text="五檔委買賣", font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", padx=12, pady=(10, 2))
        self._build_book_tree(left)
        right = ctk.CTkFrame(mid, corner_radius=12)
        right.pack(side="left", fill="both", expand=True, padx=(6, 0))
        ctk.CTkLabel(right, text="偵測器（依分數）", font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", padx=12, pady=(10, 2))
        self._build_detector_tree(right)

        # 事件表
        ev = ctk.CTkFrame(container, corner_radius=12)
        ev.pack(fill="both", expand=True, padx=24, pady=(6, 16))
        ctk.CTkLabel(ev, text="空方事件紀錄", font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", padx=12, pady=(10, 2))
        self._build_event_tree(ev)

    def _tree_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Bed.Treeview", background=_BG, fieldbackground=_BG,
                        foreground="#e0e0e0", rowheight=22, borderwidth=0)
        style.configure("Bed.Treeview.Heading", background="#263238",
                        foreground="#c0c0c0", font=("", 10, "bold"))

    def _build_chart(self, parent):
        if not _MPL:
            ctk.CTkLabel(parent, text="（未安裝 matplotlib，圖表停用）",
                         text_color="#ef5350").pack(pady=20)
            return
        fig = Figure(figsize=(11, 5.2), dpi=100, facecolor=_BG)
        gs = fig.add_gridspec(3, 1, height_ratios=[3, 1, 0], hspace=0.12)
        self._ax_price = fig.add_subplot(gs[0], facecolor=_BG)
        self._ax_score = fig.add_subplot(gs[1], facecolor=_BG, sharex=self._ax_price)
        for ax in (self._ax_price, self._ax_score):
            ax.tick_params(colors="#9aa4ad", labelsize=8)
            for sp in ax.spines.values():
                sp.set_color(_GRID)
            ax.grid(True, color=_GRID, linewidth=0.5, alpha=0.5)
        self._fig = fig
        self._canvas = FigureCanvasTkAgg(fig, parent)
        self._canvas.get_tk_widget().pack(fill="both", expand=True, padx=8, pady=8)

    def _build_book_tree(self, parent):
        self._tree_style()
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=8, pady=(0, 10))
        cols = ("bidv", "bid", "ask", "askv")
        tree = ttk.Treeview(frame, columns=cols, show="headings", height=5,
                            style="Bed.Treeview")
        for c, txt, anc in [("bidv", "買量", "e"), ("bid", "買價", "e"),
                            ("ask", "賣價", "e"), ("askv", "賣量", "e")]:
            tree.heading(c, text=txt); tree.column(c, width=80, anchor=anc)
        tree.tag_configure("bid", foreground="#ef5350")
        tree.tag_configure("ask", foreground="#26a69a")
        tree.pack(fill="both", expand=True)
        self._book_tree = tree

    def _build_detector_tree(self, parent):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=8, pady=(0, 10))
        cols = ("name", "dir", "score", "reason")
        tree = ttk.Treeview(frame, columns=cols, show="headings", height=8,
                            style="Bed.Treeview")
        for c, txt, w, anc in [("name", "偵測器", 130, "w"), ("dir", "方向", 44, "center"),
                               ("score", "分數", 50, "e"), ("reason", "主要原因", 240, "w")]:
            tree.heading(c, text=txt)
            tree.column(c, width=w, anchor=anc, stretch=(c == "reason"))
        tree.tag_configure("bear", foreground="#ef5350")
        tree.tag_configure("bull", foreground="#26a69a")
        tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set); sb.pack(side="right", fill="y")
        self._det_tree = tree

    def _build_event_tree(self, parent):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=8, pady=(0, 10))
        cols = ("sec", "type", "dir", "price")
        tree = ttk.Treeview(frame, columns=cols, show="headings", height=5,
                            style="Bed.Treeview")
        for c, txt, w, anc in [("sec", "秒", 70, "e"), ("type", "事件", 160, "w"),
                               ("dir", "方向", 60, "center"), ("price", "觸發價", 80, "e")]:
            tree.heading(c, text=txt); tree.column(c, width=w, anchor=anc)
        tree.tag_configure("bear", foreground="#ef5350")
        tree.tag_configure("bull", foreground="#26a69a")
        tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set); sb.pack(side="right", fill="y")
        self._ev_tree = tree

    # ---------------- events ----------------
    def _on_start(self):
        self.vm.start(self.code_entry.get())

    def _on_stop(self):
        self.vm.stop()

    def _bind_vm(self):
        self.vm.bind("is_tracking", self._on_tracking)
        self.vm.bind("status_msg", self._on_msg)
        self.vm.bind("status_data", self._on_status)
        self.vm.bind("book_data", self._on_book)
        self.vm.bind("detector_rows", self._on_detectors)
        self.vm.bind("event_rows", self._on_events)
        self.vm.bind("chart_data", self._on_chart)

    def _on_tracking(self, v):
        def _u():
            self.start_btn.configure(state="disabled" if v else "normal")
            self.stop_btn.configure(state="normal" if v else "disabled")
        self.after(0, _u)

    def _on_msg(self, v):
        clr = "#4ECDC4" if v.startswith("✓") else ("#FF6B6B" if ("失敗" in v or "尚未" in v or "請輸入" in v) else "#9aa4ad")
        self.after(0, lambda: self.msg_label.configure(text=v, text_color=clr))

    def _on_status(self, d):
        if not d:
            return
        def _u():
            t = self._tiles
            t["price"].configure(text=f"{d['price']:g}")
            chg = d["chg_pct"]
            t["chg"].configure(text=f"{chg:+.2f}%",
                               text_color="#ef5350" if chg < 0 else "#26a69a")
            t["vwap"].configure(text=f"{d['vwap']:g}")
            t["open"].configure(text=f"{d['open']:g}")
            t["dist"].configure(text=f"{d['dist_vwap']:+g}",
                                text_color="#ef5350" if d['dist_vwap'] < 0 else "#26a69a")
            bs = d["bear_score"]
            t["bear"].configure(text=f"{bs:.0f}",
                                text_color="#ef5350" if bs >= 65 else "#e0e0e0")
            t["state"].configure(text=d["state"])
            t["flow"].configure(text=f"{d['bull_flow']:.0f}/{d['bear_flow']:.0f}")
            t["spread"].configure(text=f"{d['spread']:g}")
            t["struct"].configure(
                text=f"{d['structure']:.0f}/{d['trade']:.0f}/{d['orderbook']:.0f}")
        self.after(0, _u)

    def _on_book(self, b):
        if not b:
            return
        def _u():
            self._book_tree.delete(*self._book_tree.get_children())
            bp, bv = b.get("bid_price", []), b.get("bid_volume", [])
            ap, av = b.get("ask_price", []), b.get("ask_volume", [])
            for i in range(5):
                bpi = bp[i] if i < len(bp) else ""
                bvi = bv[i] if i < len(bv) else ""
                api = ap[i] if i < len(ap) else ""
                avi = av[i] if i < len(av) else ""
                self._book_tree.insert("", "end", values=(
                    f"{bvi:g}" if bvi != "" else "", f"{bpi:g}" if bpi != "" else "",
                    f"{api:g}" if api != "" else "", f"{avi:g}" if avi != "" else ""))
        self.after(0, _u)

    def _on_detectors(self, rows):
        if rows is None:
            return
        def _u():
            self._det_tree.delete(*self._det_tree.get_children())
            for r in sorted(rows, key=lambda x: (-x["trig"], -x["score"])):
                if r["score"] <= 0 and not r["trig"]:
                    continue
                d = r["dir"]
                arrow = "▼空" if d < 0 else ("▲多" if d > 0 else "—")
                tag = "bear" if d < 0 else ("bull" if d > 0 else "")
                self._det_tree.insert("", "end", values=(
                    r["name"], arrow, f"{r['score']:.0f}",
                    ("★ " if r["trig"] else "") + r["reason"]), tags=(tag,))
        self.after(0, _u)

    def _on_events(self, rows):
        if rows is None:
            return
        def _u():
            self._ev_tree.delete(*self._ev_tree.get_children())
            for r in reversed(rows):
                d = r["dir"]
                arrow = "▼空" if d < 0 else "▲多"
                tag = "bear" if d < 0 else "bull"
                self._ev_tree.insert("", "end", values=(
                    r["sec"], r["type"], arrow, f"{r['price']:g}"), tags=(tag,))
        self.after(0, _u)

    def _on_chart(self, d):
        if not d or not _MPL or self._ax_price is None:
            return
        def _u():
            axp, axs = self._ax_price, self._ax_score
            axp.clear(); axs.clear()
            for ax in (axp, axs):
                ax.set_facecolor(_BG)
                ax.tick_params(colors="#9aa4ad", labelsize=8)
                ax.grid(True, color=_GRID, linewidth=0.5, alpha=0.5)
            tt = d["t"]
            axp.plot(tt, d["price"], color="#e0e0e0", linewidth=1.1, label="價格")
            axp.plot(tt, d["vwap"], color="#ffb74d", linewidth=1.0, label="VWAP")
            if d.get("open"):
                axp.axhline(d["open"], color="#5c6bc0", linewidth=0.8, linestyle="--", alpha=0.7)
            if d.get("swing_high"):
                axp.axhline(d["swing_high"], color="#ef5350", linewidth=0.6, linestyle=":", alpha=0.6)
            if d.get("swing_low"):
                axp.axhline(d["swing_low"], color="#26a69a", linewidth=0.6, linestyle=":", alpha=0.6)
            for (et, ep, ed) in d.get("events", []):
                axp.scatter([et], [ep], marker="v" if ed < 0 else "^",
                            color="#ef5350" if ed < 0 else "#26a69a", s=60, zorder=5)
            axp.legend(loc="upper left", fontsize=7, facecolor=_BG,
                       edgecolor=_GRID, labelcolor="#c0c0c0")
            axp.set_ylabel("價格", color="#9aa4ad", fontsize=8)
            bser = d.get("bear_series") or []
            if bser:
                axs.fill_between(tt, bser, color="#ef5350", alpha=0.35)
                axs.plot(tt, bser, color="#ef5350", linewidth=1.0)
            axs.axhline(75, color="#ffb74d", linewidth=0.6, linestyle="--", alpha=0.6)  # 觸發帶
            axs.set_ylim(0, 100)
            axs.set_ylabel("空方分", color="#9aa4ad", fontsize=8)
            self._canvas.draw_idle()
        self.after(0, _u)
