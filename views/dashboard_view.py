"""Dashboard — 拼圖式指揮桌（4 欄多宮格）。

版面：CTkScrollableFrame 內以 grid 切成 4 欄。寬圖面板跨 2 欄保持可讀，
KPI/清單型面板佔 1 欄，湊成 4×N 的宮格。已實作的面板填實內容，規劃中的
面板以佔位磚顯示標題與用途，逐格擴充。

背景資料由 DashboardViewModel 處理；callback 一律 self.after(0, ...) marshal
回 UI 執行緒。
"""

from __future__ import annotations

import customtkinter as ctk

from viewmodels.dashboard_viewmodel import DashboardViewModel
from views import chart_style as cs
from views.chart_style import HAS_MPL

# 面板/文字用色（沿用 chart_style 色票，維持全頁一致）
_RED = cs.RED
_GREEN = cs.GREEN
_FLAT = cs.FLAT

_COLS = 4             # 宮格欄數
_TILE_MINH = 150     # 佔位磚最小高度
_CHART_W = 4.6       # 半寬圖邏輯英吋
_CHART_H = 2.5


def _updown_color(change) -> str:
    return cs.up_down_color(change)


class DashboardView(ctk.CTkFrame):
    """Dashboard tab page（4 欄多宮格）。"""

    def __init__(self, parent: ctk.CTkFrame, viewmodel: DashboardViewModel):
        super().__init__(parent, fg_color="transparent")
        self.vm = viewmodel
        self._build_ui()
        self._bind_vm()
        # 開機延後啟動輪詢，避免搶 mainloop 尚未就緒（比照 MEDE 修正）
        self.after(300, self.vm.start)

    # ================================================================ UI
    def _build_ui(self):
        self.container = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.container.pack(fill="both", expand=True)
        for c in range(_COLS):
            self.container.grid_columnconfigure(c, weight=1, uniform="dash")

        ctk.CTkLabel(
            self.container, text="Dashboard",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).grid(row=0, column=0, columnspan=_COLS, sticky="w",
               padx=8, pady=(14, 6))

        # Row 1（跨欄寬圖）：加權指數 | 三大法人資金流
        self.index_card, index_body = self._tile(
            1, 0, colspan=2, title="加權指數 (TAIEX)", header_extra=True)
        self._build_index_body(index_body)

        self.insti_card, insti_body = self._tile(
            1, 2, colspan=2, title="三大法人資金流（全市場・億元）")
        self._build_insti_body(insti_body)

        # Row 2-3 左：高周轉率 Top20（跨 2 欄、2 列的大格）
        self.turnover_card, turnover_body = self._tile(
            2, 0, colspan=2, rowspan=2, title="高周轉率 Top20（量 > 1000 張）")
        self._build_turnover_body(turnover_body)

        # Row 2-3 右：規劃中面板佔位磚（col 2-3）
        planned = [
            (2, 2, "投信連續買超", "投信連買天數榜（作帳/認養訊號）"),
            (2, 3, "大戶吸籌", "集保大戶%上升・散戶%下降最快"),
            (3, 2, "融資券警訊", "融資急增・券資比偏高（軋空潛力）"),
            (3, 3, "隔日沖風險", "今日成交由隔日沖分點主導個股"),
            # Row 4：其餘規劃面板
            (4, 0, "市場廣度", "漲跌家數・站上均線比例・創新高低"),
            (4, 1, "主力分點淨買", "排隔日沖後全市場主力淨買力道榜"),
            (4, 2, "集中度黃金交叉", "短期集中度即將上穿長期（籌碼轉強）"),
            (4, 3, "資料完整度", "各資料源最後日・分點缺漏・排程狀態"),
        ]
        for r, c, title, desc in planned:
            self._placeholder_tile(r, c, title, desc)

    def _tile(self, row, col, colspan=1, rowspan=1, title="",
              header_extra=False):
        """建一個宮格磚，回 (card, body)。body 供內容填入。"""
        card = ctk.CTkFrame(self.container, corner_radius=12)
        card.grid(row=row, column=col, columnspan=colspan, rowspan=rowspan,
                  sticky="nsew", padx=6, pady=6)
        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=16, pady=(12, 2))
        ctk.CTkLabel(hdr, text=title,
                     font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        if header_extra:
            self.refresh_btn = ctk.CTkButton(
                hdr, text="刷新", width=56, height=26, corner_radius=6,
                font=ctk.CTkFont(size=12), command=self.vm.refresh)
            self.refresh_btn.pack(side="right")
            self.poll_dot = ctk.CTkLabel(hdr, text="●",
                                         font=ctk.CTkFont(size=12),
                                         text_color="#555555")
            self.poll_dot.pack(side="right", padx=(0, 8))
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=8, pady=(0, 10))
        return card, body

    def _placeholder_tile(self, row, col, title, desc):
        card = ctk.CTkFrame(self.container, corner_radius=12,
                            fg_color="#1a1a1c", border_width=1,
                            border_color="#2c2c2e")
        card.grid(row=row, column=col, sticky="nsew", padx=6, pady=6)
        card.grid_propagate(False)
        card.configure(height=_TILE_MINH)
        ctk.CTkLabel(card, text=title,
                     font=ctk.CTkFont(size=14, weight="bold"),
                     text_color="#9a9a9a").pack(anchor="w", padx=14, pady=(14, 2))
        ctk.CTkLabel(card, text=desc, font=ctk.CTkFont(size=11),
                     text_color="#6a6a6a", wraplength=190,
                     justify="left").pack(anchor="w", padx=14, pady=(0, 8))
        ctk.CTkLabel(card, text="規劃中", font=ctk.CTkFont(size=11),
                     text_color="#4a90d9").pack(side="bottom", anchor="e",
                                                padx=14, pady=(0, 12))

    # ---- 加權指數面板內容 ----
    def _build_index_body(self, body):
        self.kpi_row = ctk.CTkFrame(body, fg_color="transparent")
        self.kpi_row.pack(fill="x", padx=12, pady=(2, 0))
        self.lbl_last = ctk.CTkLabel(
            self.kpi_row, text="—", font=ctk.CTkFont(size=28, weight="bold"),
            text_color=_FLAT)
        self.lbl_last.pack(side="left")
        self.lbl_change = ctk.CTkLabel(
            self.kpi_row, text="", font=ctk.CTkFont(size=15, weight="bold"),
            text_color=_FLAT)
        self.lbl_change.pack(side="left", padx=(10, 0), pady=(8, 0))

        self.lbl_ohl = ctk.CTkLabel(body, text="", font=ctk.CTkFont(size=12),
                                    text_color="gray")
        self.lbl_ohl.pack(anchor="w", padx=12, pady=(0, 2))

        self.chart_frame = ctk.CTkFrame(body, fg_color="transparent")
        self.chart_frame.pack(fill="both", expand=True, padx=4, pady=(2, 2))

        self.status_label = ctk.CTkLabel(body, text="尚未載入",
                                         font=ctk.CTkFont(size=11),
                                         text_color="gray")
        self.status_label.pack(anchor="w", padx=12, pady=(0, 2))

    # ---- 三大法人面板內容 ----
    def _build_insti_body(self, body):
        self.insti_kpi_row = ctk.CTkFrame(body, fg_color="transparent")
        self.insti_kpi_row.pack(fill="x", padx=10, pady=(2, 0))
        self.insti_chart_frame = ctk.CTkFrame(body, fg_color="transparent")
        self.insti_chart_frame.pack(fill="both", expand=True, padx=4,
                                    pady=(2, 2))
        self.insti_status_label = ctk.CTkLabel(body, text="尚未載入",
                                               font=ctk.CTkFont(size=11),
                                               text_color="gray")
        self.insti_status_label.pack(anchor="w", padx=12, pady=(0, 2))

    # ---- 高周轉率面板內容 ----
    # 欄位：(key, 標題, 邏輯寬度px, 對齊, 欄權重)
    _TURN_COLS = [
        ("rank", "#", 26, "center", 0),
        ("mkt", "", 34, "center", 0),
        ("code", "代碼", 50, "center", 0),
        ("name", "名稱", 88, "w", 1),
        ("close", "收盤", 62, "e", 0),
        ("vol", "量(張)", 66, "e", 0),
        ("turn", "周轉率%", 66, "e", 0),
    ]
    # 市場別徽章配色（避開紅漲/綠跌語意）
    _MKT_STYLE = {"上市": ("市", "#3d6fb4"), "上櫃": ("櫃", "#b07a2e")}

    def _build_turnover_body(self, body):
        self.turnover_table = ctk.CTkFrame(body, fg_color="transparent")
        self.turnover_table.pack(fill="both", expand=True, padx=10, pady=(2, 2))
        for i, (_k, _t, w, _a, weight) in enumerate(self._TURN_COLS):
            self.turnover_table.grid_columnconfigure(
                i, weight=weight, minsize=w)
        # 表頭
        for i, (_k, title, _w, anc, _wt) in enumerate(self._TURN_COLS):
            ctk.CTkLabel(self.turnover_table, text=title,
                         font=ctk.CTkFont(size=12, weight="bold"),
                         text_color="#8a8a8e",
                         anchor=("center" if anc == "center"
                                 else "e" if anc == "e" else "w")).grid(
                row=0, column=i, sticky="ew", padx=3, pady=(0, 4))
        self._turnover_row_widgets: list = []

        self.turnover_status_label = ctk.CTkLabel(body, text="尚未載入",
                                                  font=ctk.CTkFont(size=11),
                                                  text_color="gray")
        self.turnover_status_label.pack(anchor="w", padx=12, pady=(0, 2))

    # ================================================================ Bindings
    def _bind_vm(self):
        self.vm.bind("index_quote", self._on_quote)
        self.vm.bind("intraday_series", self._on_series)
        self.vm.bind("index_status", self._on_status)
        self.vm.bind("is_polling", self._on_polling)
        self.vm.bind("insti_today", self._on_insti_today)
        self.vm.bind("insti_trend", self._on_insti_trend)
        self.vm.bind("insti_status", self._on_insti_status)
        self.vm.bind("turnover_rows", self._on_turnover_rows)
        self.vm.bind("turnover_status", self._on_turnover_status)

    def _on_status(self, v):
        self.after(0, lambda: self.status_label.configure(text=v or ""))

    def _on_polling(self, v):
        self.after(0, lambda: self.poll_dot.configure(
            text_color=_RED if v else "#555555"))

    def _on_quote(self, q):
        self.after(0, lambda: self._render_kpi(q))

    def _on_series(self, s):
        self.after(0, lambda: self._render_chart(s))

    def _on_insti_status(self, v):
        self.after(0, lambda: self.insti_status_label.configure(text=v or ""))

    def _on_turnover_status(self, v):
        self.after(0, lambda: self.turnover_status_label.configure(text=v or ""))

    def _on_turnover_rows(self, rows):
        self.after(0, lambda: self._render_turnover(rows))

    def _render_turnover(self, rows):
        for w in getattr(self, "_turnover_row_widgets", []):
            w.destroy()
        self._turnover_row_widgets = []
        if not rows:
            return
        for idx, r in enumerate(rows, 1):
            stripe = "#1d1e21" if idx % 2 == 0 else "transparent"
            rowf = ctk.CTkFrame(self.turnover_table, fg_color=stripe,
                                corner_radius=4)
            rowf.grid(row=idx, column=0, columnspan=len(self._TURN_COLS),
                      sticky="ew", pady=1)
            for i, (_k, _t, w, _a, weight) in enumerate(self._TURN_COLS):
                rowf.grid_columnconfigure(i, weight=weight, minsize=w)

            turn = r["turnover_pct"]
            turn_clr = cs.RED if turn >= 20 else cs.TXT
            cells = [
                ("rank", str(idx), "#7a7a7e", "center", False),
                ("code", r["stock_code"], "#d4d4d4", "center", False),
                ("name", r["stock_name"], "#e6e6e6", "w", False),
                ("close", f"{r['close']:,.2f}", "#c7c7cc", "e", False),
                ("vol", f"{r['volume_lots']:,}", "#c7c7cc", "e", False),
                ("turn", f"{turn:.2f}", turn_clr, "e", True),
            ]
            # 市場別小徽章（欄 1）
            mkt = r.get("market", "")
            label, color = self._MKT_STYLE.get(mkt, ("", "#555555"))
            if label:
                ctk.CTkLabel(rowf, text=label, width=20, height=18,
                             corner_radius=4, fg_color=color,
                             text_color="#ffffff",
                             font=ctk.CTkFont(size=10, weight="bold")).grid(
                    row=0, column=1, padx=3, pady=2)
            # 其餘資料欄（rank 在 0，market 在 1，其餘 2..6）
            col_map = {"rank": 0, "code": 2, "name": 3, "close": 4,
                       "vol": 5, "turn": 6}
            for key, text, clr, anc, bold in cells:
                ctk.CTkLabel(
                    rowf, text=text, text_color=clr,
                    font=ctk.CTkFont(size=12, weight="bold" if bold else "normal"),
                    anchor=("center" if anc == "center"
                            else "e" if anc == "e" else "w")).grid(
                    row=0, column=col_map[key], sticky="ew", padx=3, pady=2)
            self._turnover_row_widgets.append(rowf)

    def _on_insti_today(self, q):
        self.after(0, lambda: self._render_insti_kpi(q))

    def _on_insti_trend(self, t):
        self.after(0, lambda: self._render_insti_chart(t))

    # ================================================================ Render
    def _render_kpi(self, q: dict | None):
        if not q:
            return
        last = q.get("last")
        change = q.get("change")
        pct = q.get("change_pct")
        clr = _updown_color(change)
        arrow = "▲" if (change or 0) > 0 else ("▼" if (change or 0) < 0 else "")
        self.lbl_last.configure(
            text=f"{last:,.2f}" if last is not None else "—", text_color=clr)
        if change is not None and pct is not None:
            sign = "+" if change > 0 else ""
            self.lbl_change.configure(
                text=f"{arrow} {sign}{change:,.2f}  ({sign}{pct:.2f}%)",
                text_color=clr)
        else:
            self.lbl_change.configure(text="")
        o, h, l = q.get("open"), q.get("high"), q.get("low")
        pc = q.get("prev_close")
        parts = []
        if o is not None:
            parts.append(f"開 {o:,.2f}")
        if h is not None:
            parts.append(f"高 {h:,.2f}")
        if l is not None:
            parts.append(f"低 {l:,.2f}")
        if pc is not None:
            parts.append(f"昨收 {pc:,.2f}")
        self.lbl_ohl.configure(text="　".join(parts))

    def _render_chart(self, series):
        for w in self.chart_frame.winfo_children():
            w.destroy()
        if not HAS_MPL:
            ctk.CTkLabel(self.chart_frame, text="（需安裝 matplotlib）",
                         font=ctk.CTkFont(size=12), text_color="gray").pack(pady=12)
            return
        if not series:
            ctk.CTkLabel(self.chart_frame, text="（暫無分時資料）",
                         font=ctk.CTkFont(size=12), text_color="gray").pack(pady=12)
            return

        times = [t for t, _ in series]
        vals = [v for _, v in series]
        xs = list(range(len(vals)))
        q = self.vm.index_quote or {}
        prev = q.get("prev_close")
        base = prev if prev is not None else vals[0]
        clr = _RED if vals[-1] >= base else _GREEN

        fig, ax = cs.new_fig(_CHART_W, _CHART_H)
        cs.gradient_fill(ax, xs, vals, base, clr)
        if prev is not None:
            ax.axhline(prev, color=_FLAT, linewidth=1.0,
                       linestyle=(0, (4, 3)), alpha=0.55, zorder=2)
        cs.line(ax, xs, vals, clr, width=1.6, zorder=4)
        cs.end_marker(ax, xs[-1], vals[-1], clr, f"{vals[-1]:,.0f}")
        cs.style_axis(ax)

        n = len(times)
        step = max(n // 6, 1)
        ticks = list(range(0, n, step))
        if ticks and ticks[-1] != n - 1:
            ticks.append(n - 1)
        ax.set_xticks(ticks)
        ax.set_xticklabels([times[t][:5] for t in ticks])
        ax.set_xlim(0, n - 1)
        lo, hi = min(min(vals), base), max(max(vals), base)
        pad = (hi - lo) * 0.08 or 1
        ax.set_ylim(lo - pad, hi + pad)
        cs.embed(self.chart_frame, fig, _CHART_W, _CHART_H)

    def _render_insti_kpi(self, q: dict | None):
        for w in self.insti_kpi_row.winfo_children():
            w.destroy()
        if not q:
            return
        for label, key in [("外資", "foreign"), ("投信", "trust"),
                           ("自營", "dealer"), ("合計", "total")]:
            val = q.get(key, 0.0)
            clr = _updown_color(val)
            sign = "+" if val > 0 else ""
            box = ctk.CTkFrame(self.insti_kpi_row, corner_radius=8,
                               fg_color="#1e1e1e")
            box.pack(side="left", padx=4, pady=2)
            ctk.CTkLabel(box, text=label, font=ctk.CTkFont(size=12),
                         text_color="gray").pack(padx=12, pady=(5, 0))
            ctk.CTkLabel(box, text=f"{sign}{val:,.0f}",
                         font=ctk.CTkFont(size=16, weight="bold"),
                         text_color=clr).pack(padx=12, pady=(0, 5))

    def _render_insti_chart(self, trend):
        for w in self.insti_chart_frame.winfo_children():
            w.destroy()
        if not HAS_MPL:
            ctk.CTkLabel(self.insti_chart_frame, text="（需安裝 matplotlib）",
                         font=ctk.CTkFont(size=12), text_color="gray").pack(pady=12)
            return
        if not trend:
            ctk.CTkLabel(self.insti_chart_frame, text="（暫無資金流資料）",
                         font=ctk.CTkFont(size=12), text_color="gray").pack(pady=12)
            return

        labels = [r["date"][4:] for r in trend]
        foreign = [r["foreign"] for r in trend]
        trust = [r["trust"] for r in trend]
        dealer = [r["dealer"] for r in trend]
        xs = list(range(len(trend)))

        fig, ax = cs.new_fig(_CHART_W, _CHART_H)
        bar_clr = [_RED if v >= 0 else _GREEN for v in foreign]
        ax.bar(xs, foreign, width=0.6, color=bar_clr, alpha=0.9,
               label="外資", zorder=3)
        cs.line(ax, xs, trust, cs.AMBER, width=1.4, marker="o",
                markersize=3 * 2, label="投信", zorder=4)
        cs.line(ax, xs, dealer, cs.PURPLE, width=1.2, marker="s",
                markersize=3 * 2, label="自營", alpha=0.9, zorder=4)
        ax.axhline(0, color=_FLAT, linewidth=1.0, zorder=2)
        cs.style_axis(ax, ylabel="億元")
        ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=3,
                  fontsize=14, framealpha=0.0, labelcolor=cs.TXT,
                  handlelength=1.2, columnspacing=1.2, borderpad=0.1)
        ax.set_xticks(xs)
        ax.set_xticklabels(labels, rotation=30, ha="right")
        ax.set_xlim(-0.6, len(trend) - 0.4)
        cs.embed(self.insti_chart_frame, fig, _CHART_W, _CHART_H)
