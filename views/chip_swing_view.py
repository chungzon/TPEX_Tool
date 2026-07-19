"""籌碼波段（CHIP） — 單股籌碼趨勢 + chip_score + 波段訊號 + 多週期回測。

規則 + 加權評分（第一版無 AI）。所有計算在 chip_swing_service；此檔只負責
版面與把 VM 的 result_data 畫成圖表 / 表格。VM 於背景執行緒完成後透過
ObservableProperty 通知，callback 一律以 self.after(0, ...) marshal 回 UI 執行緒。
"""

from __future__ import annotations

import customtkinter as ctk
from tkinter import ttk

from viewmodels.chip_swing_viewmodel import ChipSwingViewModel, default_date_range

try:
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    matplotlib.rcParams["font.sans-serif"] = [
        "Microsoft JhengHei", "Microsoft YaHei", "SimHei", "sans-serif",
    ]
    matplotlib.rcParams["axes.unicode_minus"] = False
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

# 深色配色（比照 broker_analysis_view）
_BG = "#1c1c1e"
_TXT = "#c0c0c0"
_GRID = "#2c2c2e"
_RED = "#ef5350"      # 偏多 / 淨買
_GREEN = "#26a69a"    # 偏空 / 淨賣
_PRICE = "#8e8e93"
_COST = "#ffb300"     # 主力成本線
_SCORE = "#42a5f5"


def _clr(v: float) -> str:
    return _RED if v >= 0 else _GREEN


class ChipSwingView(ctk.CTkFrame):
    """籌碼波段 tab page."""

    def __init__(self, parent: ctk.CTkFrame, viewmodel: ChipSwingViewModel):
        super().__init__(parent, fg_color="transparent")
        self.vm = viewmodel
        self._build_ui()
        self._bind_vm()

    # ================================================================ UI
    def _build_ui(self):
        self.container = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.container.pack(fill="both", expand=True)

        ctk.CTkLabel(
            self.container, text="籌碼波段選股與回測",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(pady=(20, 4))
        ctk.CTkLabel(
            self.container,
            text="主力成本 × 集中度 × 法人連買 × 大戶結構 × 技術面 → chip_score(0–100) → 波段訊號 + 5/10/20/40/60 日回測",
            font=ctk.CTkFont(size=13), text_color="gray",
        ).pack(pady=(0, 16))

        # --- 輸入卡 ---
        inp = ctk.CTkFrame(self.container, corner_radius=12)
        inp.pack(padx=32, pady=8, fill="x")
        row = ctk.CTkFrame(inp, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=16)

        ctk.CTkLabel(row, text="股票代碼：",
                     font=ctk.CTkFont(size=14)).pack(side="left")
        self.code_entry = ctk.CTkEntry(row, width=110, font=ctk.CTkFont(size=14),
                                        placeholder_text="如 2330")
        self.code_entry.pack(side="left", padx=(4, 12))
        self.code_entry.bind("<Return>", lambda e: self._on_analyse())

        ctk.CTkLabel(row, text="區間：",
                     font=ctk.CTkFont(size=14)).pack(side="left")
        s0, e0 = default_date_range()
        self.start_entry = ctk.CTkEntry(row, width=110, font=ctk.CTkFont(size=14),
                                        placeholder_text="yyyy-mm-dd")
        self.start_entry.pack(side="left", padx=(4, 4))
        self.start_entry.insert(0, s0)
        ctk.CTkLabel(row, text="~", font=ctk.CTkFont(size=14)).pack(side="left")
        self.end_entry = ctk.CTkEntry(row, width=110, font=ctk.CTkFont(size=14),
                                      placeholder_text="yyyy-mm-dd")
        self.end_entry.pack(side="left", padx=(4, 12))
        self.end_entry.insert(0, e0)

        self.analyse_btn = ctk.CTkButton(
            row, text="分析", width=90, height=32, corner_radius=8,
            font=ctk.CTkFont(size=14, weight="bold"), command=self._on_analyse)
        self.analyse_btn.pack(side="left")
        self.error_label = ctk.CTkLabel(row, text="", font=ctk.CTkFont(size=13),
                                        text_color=_RED)
        self.error_label.pack(side="left", padx=(12, 0))

        self.status_label = ctk.CTkLabel(
            inp, text="就緒", font=ctk.CTkFont(size=13), text_color="gray")
        self.status_label.pack(anchor="w", padx=22, pady=(0, 12))

        # --- 結果區（初始隱藏；分析完成後 pack）---
        self.trend_card = self._make_card("籌碼趨勢圖　（收盤價 + 主力成本線 + 買/出場標記）")
        self.trend_body = self._card_body(self.trend_card)

        self.score_card = self._make_card("Chip Score 趨勢")
        self.score_body = self._card_body(self.score_card)

        self.holder_card = self._make_card("大戶 / 散戶持股變化（集保週頻）")
        self.holder_body = self._card_body(self.holder_card)

        self.insti_card = self._make_card("法人（外資+投信）累積買賣超")
        self.insti_body = self._card_body(self.insti_card)

        self.broker_card = self._make_card("分點籌碼表（區間聚合，標示隔日沖分點）")
        self.broker_tree = self._make_tree(
            self.broker_card,
            [("broker", "分點", 150, "w"), ("buy", "買(張)", 70, "e"),
             ("sell", "賣(張)", 70, "e"), ("net", "淨(張)", 80, "e"),
             ("cost", "買均價", 75, "e"), ("tag", "標記", 70, "center")],
            height=10)

        self.signal_card = self._make_card("訊號紀錄表（波段：進場 → 出場）")
        self.signal_tree = self._make_tree(
            self.signal_card,
            [("entry", "進場日", 90, "center"), ("eprice", "進場價", 70, "e"),
             ("escore", "進場分", 60, "e"), ("cost", "主力成本", 75, "e"),
             ("exit", "出場日", 90, "center"), ("xprice", "出場價", 70, "e"),
             ("reason", "出場原因", 90, "center"), ("hold", "持有日", 55, "e"),
             ("ret", "報酬%", 70, "e")],
            height=8)

        self.bt_card = self._make_card("多週期持有回測（勝率 / 平均報酬 / 期望值 / 最大回撤）")
        self.bt_tree = self._make_tree(
            self.bt_card,
            [("hold", "持有日", 60, "center"), ("n", "筆數", 55, "e"),
             ("win", "勝率%", 65, "e"), ("avg", "平均%", 70, "e"),
             ("med", "中位%", 70, "e"), ("exp", "期望值%", 75, "e"),
             ("best", "最佳%", 65, "e"), ("worst", "最差%", 65, "e"),
             ("mdd", "最大回撤%", 80, "e"), ("inc", "不足期", 60, "e")],
            height=6)

        self._result_cards = [
            self.trend_card, self.score_card, self.holder_card,
            self.insti_card, self.broker_card, self.signal_card, self.bt_card,
        ]

    def _make_card(self, title: str) -> ctk.CTkFrame:
        card = ctk.CTkFrame(self.container, corner_radius=12)
        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(14, 4))
        ctk.CTkLabel(hdr, text=title,
                     font=ctk.CTkFont(size=16, weight="bold")).pack(side="left")
        return card

    def _card_body(self, card: ctk.CTkFrame) -> ctk.CTkFrame:
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=8, pady=(0, 14))
        return body

    def _make_tree(self, card, cols, height) -> ttk.Treeview:
        self._ensure_style()
        frame = ctk.CTkFrame(card, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=12, pady=(0, 14))
        names = [c[0] for c in cols]
        tree = ttk.Treeview(frame, columns=names, show="headings",
                            style="Chip.Treeview", height=height)
        for key, txt, w, anc in cols:
            tree.heading(key, text=txt)
            tree.column(key, width=w, anchor=anc, stretch=True)
        tree.tag_configure("pos", foreground=_RED)
        tree.tag_configure("neg", foreground=_GREEN)
        tree.tag_configure("day", foreground="#ff6d00")
        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        return tree

    def _ensure_style(self):
        s = ttk.Style()
        name = "Chip.Treeview"
        s.configure(name, background="#252526", foreground="#d4d4d4",
                    fieldbackground="#252526", borderwidth=0, rowheight=26,
                    font=("Microsoft JhengHei", 11))
        s.map(name, background=[("selected", "#264f78")],
              foreground=[("selected", "#ffffff")])
        s.configure(f"{name}.Heading", background="#2d2d2d", foreground="#cccccc",
                    borderwidth=0, relief="flat",
                    font=("Microsoft JhengHei", 11, "bold"))
        s.map(f"{name}.Heading", background=[("active", "#3e3e3e")])

    # ================================================================ Events
    def _on_analyse(self):
        self.vm.analyse(self.code_entry.get(), self.start_entry.get(),
                        self.end_entry.get())

    # ================================================================ Bindings
    def _bind_vm(self):
        self.vm.bind("error_text", self._on_error)
        self.vm.bind("status_text", self._on_status)
        self.vm.bind("is_running", self._on_running)
        self.vm.bind("result_data", self._on_result)

    def _on_error(self, v):
        self.after(0, lambda: self.error_label.configure(text=v or ""))

    def _on_status(self, v):
        self.after(0, lambda: self.status_label.configure(text=v or ""))

    def _on_running(self, v):
        def _u():
            if v:
                self.analyse_btn.configure(state="disabled", text="分析中...")
            else:
                self.analyse_btn.configure(state="normal", text="分析")
        self.after(0, _u)

    def _on_result(self, data):
        self.after(0, lambda: self._render(data))

    # ================================================================ Render
    def _render(self, data: dict | None):
        if not data:
            for c in self._result_cards:
                c.pack_forget()
            return

        feats = data["features"]
        cfg = data["config"]
        self._render_trend(feats, data["signals"])
        self._render_score(feats, cfg)
        self._render_holder(feats)
        self._render_insti(feats)
        self._render_broker_table(data["brokers"])
        self._render_signal_table(data["signals"])
        self._render_backtest_table(data["backtest"])

        for c in self._result_cards:
            c.pack(padx=32, pady=8, fill="x")

    def _embed(self, body: ctk.CTkFrame, fig):
        for w in body.winfo_children():
            w.destroy()
        canvas = FigureCanvasTkAgg(fig, body)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True, padx=4, pady=4)

    def _no_mpl(self, body):
        for w in body.winfo_children():
            w.destroy()
        ctk.CTkLabel(body, text="（需安裝 matplotlib 才能顯示圖表）",
                     font=ctk.CTkFont(size=14), text_color="gray").pack(pady=16)

    def _new_fig(self, h=3.0):
        fig = Figure(figsize=(8.4, h), dpi=100, facecolor=_BG)
        fig.subplots_adjust(left=0.08, right=0.92, top=0.90, bottom=0.20)
        ax = fig.add_subplot(111)
        ax.set_facecolor(_BG)
        return fig, ax

    def _style_axis(self, ax, ylabel=""):
        if ylabel:
            ax.set_ylabel(ylabel, fontsize=10, color=_TXT)
        ax.tick_params(axis="both", colors=_TXT, labelsize=8)
        for sp in ax.spines.values():
            sp.set_color(_GRID)
        ax.grid(True, axis="y", alpha=0.2, color=_GRID, linewidth=0.5)

    def _xticks(self, ax, labels):
        n = len(labels)
        if n == 0:
            return
        if n <= 15:
            ticks = list(range(n))
        else:
            step = max(n // 10, 1)
            ticks = list(range(0, n, step))
            if ticks[-1] != n - 1:
                ticks.append(n - 1)
        ax.set_xticks(ticks)
        ax.set_xticklabels([labels[t][5:] for t in ticks], fontsize=8,
                           color=_TXT, rotation=35, ha="right")
        ax.set_xlim(-0.6, n - 0.4)

    def _render_trend(self, feats, signals):
        if not HAS_MPL:
            return self._no_mpl(self.trend_body)
        labels = [f["trade_date"] for f in feats]
        close = [f["close"] for f in feats]
        cost = [f["main_cost"] if f["main_cost"] > 0 else None for f in feats]
        xs = list(range(len(feats)))
        idx = {f["trade_date"]: i for i, f in enumerate(feats)}

        fig, ax = self._new_fig(3.2)
        ax.plot(xs, close, color=_PRICE, linewidth=1.3, label="收盤價")
        cxs = [i for i in xs if cost[i] is not None]
        cys = [cost[i] for i in cxs]
        if cxs:
            ax.plot(cxs, cys, color=_COST, linewidth=1.5, label="主力成本")
        # 訊號標記
        for s in signals:
            ei = idx.get(s["entry_date"])
            if ei is not None:
                ax.scatter(ei, close[ei], marker="^", s=80, color=_RED,
                           zorder=5, label="_")
            xi = idx.get(s["exit_date"])
            if xi is not None and not s.get("open_position"):
                ax.scatter(xi, close[xi], marker="v", s=80, color=_GREEN,
                           zorder=5, label="_")
        self._style_axis(ax, "價格")
        ax.legend(loc="upper left", fontsize=8, framealpha=0.5,
                  facecolor=_BG, edgecolor=_GRID, labelcolor=_TXT, ncol=2)
        self._xticks(ax, labels)
        self._embed(self.trend_body, fig)

    def _render_score(self, feats, cfg):
        if not HAS_MPL:
            return self._no_mpl(self.score_body)
        labels = [f["trade_date"] for f in feats]
        score = [f["chip_score"] for f in feats]
        xs = list(range(len(feats)))

        fig, ax = self._new_fig(2.6)
        ax.plot(xs, score, color=_SCORE, linewidth=1.4, label="chip_score")
        ax.axhline(cfg["buy_threshold"], color=_RED, linewidth=0.9,
                   linestyle="--", label=f"買進門檻 {cfg['buy_threshold']:g}")
        ax.axhline(cfg["exit_threshold"], color=_GREEN, linewidth=0.9,
                   linestyle="--", label=f"出場門檻 {cfg['exit_threshold']:g}")
        ax.set_ylim(0, 100)
        self._style_axis(ax, "分數")
        ax.legend(loc="upper left", fontsize=8, framealpha=0.5,
                  facecolor=_BG, edgecolor=_GRID, labelcolor=_TXT, ncol=3)
        self._xticks(ax, labels)
        self._embed(self.score_body, fig)

    def _render_holder(self, feats):
        if not HAS_MPL:
            return self._no_mpl(self.holder_body)
        pts = [(i, f["big_pct"], f["retail_pct"]) for i, f in enumerate(feats)
               if f["big_pct"] > 0 or f["retail_pct"] > 0]
        if not pts:
            for w in self.holder_body.winfo_children():
                w.destroy()
            ctk.CTkLabel(self.holder_body, text="（無集保資料）",
                         font=ctk.CTkFont(size=14), text_color="gray").pack(pady=16)
            return
        labels = [feats[i]["trade_date"] for i, _, _ in pts]
        big = [b for _, b, _ in pts]
        ret = [r for _, _, r in pts]
        xs = list(range(len(pts)))

        fig, ax = self._new_fig(2.6)
        ax.plot(xs, big, color=_RED, linewidth=1.5, marker="o", markersize=3,
                label="大戶 (12–15級)")
        ax.plot(xs, ret, color=_GREEN, linewidth=1.5, marker="^", markersize=3,
                label="散戶 (1–5級)")
        self._style_axis(ax, "%")
        ax.legend(loc="upper left", fontsize=8, framealpha=0.5,
                  facecolor=_BG, edgecolor=_GRID, labelcolor=_TXT, ncol=2)
        self._xticks(ax, labels)
        self._embed(self.holder_body, fig)

    def _render_insti(self, feats):
        if not HAS_MPL:
            return self._no_mpl(self.insti_body)
        labels = [f["trade_date"] for f in feats]
        xs = list(range(len(feats)))
        cum = []
        acc = 0.0
        for f in feats:
            acc += (f["insti_net"] or 0) / 1000.0   # 股 → 張
            cum.append(acc)

        fig, ax = self._new_fig(2.6)
        line_clr = _RED if (cum and cum[-1] >= 0) else _GREEN
        ax.plot(xs, cum, color=line_clr, linewidth=1.5, label="累積買賣超(張)")
        ax.fill_between(xs, cum, 0, color=line_clr, alpha=0.15)
        ax.axhline(0, color=_GRID, linewidth=0.6)
        self._style_axis(ax, "張")
        ax.legend(loc="upper left", fontsize=8, framealpha=0.5,
                  facecolor=_BG, edgecolor=_GRID, labelcolor=_TXT)
        self._xticks(ax, labels)
        self._embed(self.insti_body, fig)

    def _render_broker_table(self, brokers):
        self.broker_tree.delete(*self.broker_tree.get_children())
        # 取淨買前 15 + 淨賣前 15
        top = brokers[:15]
        bottom = [b for b in brokers if b["net_lots"] < 0][-15:]
        seen = set()
        for b in top + bottom:
            key = b["broker_name"]
            if key in seen:
                continue
            seen.add(key)
            tag = "day" if b["is_daytrade"] else (
                "pos" if b["net_lots"] >= 0 else "neg")
            self.broker_tree.insert(
                "", "end",
                values=(b["broker_name"], f"{b['buy_lots']:,}",
                        f"{b['sell_lots']:,}", f"{b['net_lots']:+,}",
                        f"{b['avg_buy_price']:.2f}" if b["avg_buy_price"] else "-",
                        "隔日沖" if b["is_daytrade"] else ""),
                tags=(tag,))

    def _render_signal_table(self, signals):
        self.signal_tree.delete(*self.signal_tree.get_children())
        for s in reversed(signals):   # 最新在上
            tag = "pos" if s["return_pct"] >= 0 else "neg"
            reason = s["exit_reason"] + ("*" if s.get("open_position") else "")
            self.signal_tree.insert(
                "", "end",
                values=(s["entry_date"], f"{s['entry_price']:.2f}",
                        f"{s['entry_score']:.0f}",
                        f"{s['entry_cost']:.2f}" if s["entry_cost"] else "-",
                        s["exit_date"], f"{s['exit_price']:.2f}", reason,
                        s["hold_days"], f"{s['return_pct']:+.2f}"),
                tags=(tag,))

    def _render_backtest_table(self, results):
        self.bt_tree.delete(*self.bt_tree.get_children())
        for r in results:
            tag = "pos" if r["expectancy"] >= 0 else "neg"
            self.bt_tree.insert(
                "", "end",
                values=(r["hold_days"], r["count"], f"{r['win_rate']:.1f}",
                        f"{r['avg_return']:+.2f}", f"{r['median_return']:+.2f}",
                        f"{r['expectancy']:+.2f}", f"{r['best']:+.2f}",
                        f"{r['worst']:+.2f}", f"{r['max_drawdown']:.2f}",
                        r["incomplete"]),
                tags=(tag,))
