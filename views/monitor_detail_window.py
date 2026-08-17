"""監控詳細視窗 — 單檔即時監控彈窗（CTkToplevel）。

版面：
- 頂部 KPI：名稱/代碼/市場徽章 + 即時價/漲跌/OHL。
- 中段兩圖並排：左＝日線趨勢（收盤 + 月線MA20 + 布林通道 + 底部量棒，單一圖）；
  右＝即時分時走勢（分 K 收盤 + 底部量棒）。兩圖皆「量價同框」。
- 底段兩塊：左＝同類股成交量前五（價 + 當日漲跌幅%）；右＝權證多空（多方佔比）。

所有 VM callback 一律 self.after(0, ...) marshal 回 UI 執行緒；關閉時 vm.shutdown()。
"""

from __future__ import annotations

import customtkinter as ctk

from viewmodels.monitor_detail_viewmodel import MonitorDetailViewModel
from views import chart_style as cs
from views.chart_style import HAS_MPL

try:
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    import matplotlib.ticker as mticker
    from matplotlib.patheffects import withStroke
    _HAS_TKAGG = True
except Exception:      # noqa: BLE001
    _HAS_TKAGG = False

_MKT_STYLE = {"上市": ("市", "#3d6fb4"), "上櫃": ("櫃", "#b07a2e")}
_WARRANT_CLR = {"多": "#ef5350", "空": "#26a69a", "中性": "#8a8a8e"}
_CLOSE_CLR = "#e8e8ea"      # 收盤線
_MA_CLR = cs.AMBER          # 月線
_TREND_W, _TREND_H = 5.7, 3.35
_TREND_DPI = 130            # 互動日線圖解析度（越高越精細）
_MAX_DAILY_BARS = 90        # 趨勢圖最多顯示根數
_DEDUCT_PERIOD = 20         # 均線扣抵基準（月線MA20）
_DEDUCT_CLR = cs.GREEN      # 扣抵位置符號色（尖尖綠色，同券商慣例）


class MonitorDetailWindow(ctk.CTkToplevel):
    """單檔監控彈窗。"""

    def __init__(self, master, stock_code: str, stock_name: str, market: str,
                 date: str, ctx: dict | None, shioaji_svc=None):
        super().__init__(master)
        self.vm = MonitorDetailViewModel(stock_code, stock_name, market, date,
                                         ctx, shioaji_svc=shioaji_svc)
        self.title(f"監控 · {stock_code} {stock_name}")
        self.geometry("1180x860")
        self.configure(fg_color="#151517")
        self.transient(master)
        self.after(50, self.lift)

        self._build_ui()
        self._bind_vm()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(200, self.vm.start)

    # ================================================================ UI
    def _build_ui(self):
        root = ctk.CTkScrollableFrame(self, fg_color="transparent")
        root.pack(fill="both", expand=True, padx=14, pady=12)
        root.grid_columnconfigure(0, weight=1, uniform="col")
        root.grid_columnconfigure(1, weight=1, uniform="col")
        self._grid = root      # 注意：勿命名為 _root，會覆蓋 tkinter Misc._root()

        # ---- 頂部 KPI（跨兩欄）----
        head = ctk.CTkFrame(root, corner_radius=12, fg_color="#1b1c1f")
        head.grid(row=0, column=0, columnspan=2, sticky="ew", padx=6, pady=6)
        idbar = ctk.CTkFrame(head, fg_color="transparent")
        idbar.pack(fill="x", padx=16, pady=(12, 2))
        badge = _MKT_STYLE.get(self.vm.market)
        if badge:
            ctk.CTkLabel(idbar, text=badge[0], width=22, height=20,
                         corner_radius=4, fg_color=badge[1],
                         text_color="#ffffff",
                         font=ctk.CTkFont(size=12, weight="bold")).pack(
                side="left", padx=(0, 8))
        ctk.CTkLabel(idbar, text=f"{self.vm.name}",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(side="left")
        ctk.CTkLabel(idbar, text=f"  {self.vm.code}",
                     font=ctk.CTkFont(size=15), text_color="#9a9a9e").pack(
            side="left")

        kpi = ctk.CTkFrame(head, fg_color="transparent")
        kpi.pack(fill="x", padx=16, pady=(0, 4))
        self.lbl_price = ctk.CTkLabel(
            kpi, text="—", font=ctk.CTkFont(size=30, weight="bold"),
            text_color=cs.FLAT)
        self.lbl_price.pack(side="left")
        self.lbl_change = ctk.CTkLabel(
            kpi, text="", font=ctk.CTkFont(size=15, weight="bold"),
            text_color=cs.FLAT)
        self.lbl_change.pack(side="left", padx=(10, 0), pady=(8, 0))
        self.lbl_ohl = ctk.CTkLabel(head, text="", font=ctk.CTkFont(size=12),
                                    text_color="gray")
        self.lbl_ohl.pack(anchor="w", padx=16, pady=(0, 10))

        # ---- 兩圖並排 ----
        self.trend_card, self.trend_body = self._card(
            1, 0, "日線趨勢（K線 · 月線MA20 · 布林通道 · 扣抵 · 量）")
        self.intra_card, self.intra_body = self._card(
            1, 1, "即時分時走勢（分 K · 量）")

        # ---- 同類股 + 權證 ----
        self.peer_card, self.peer_body = self._card(
            2, 0, "同類股成交量前五（同市場同產業 · 即時）")
        self.warr_card, self.warr_body = self._card(
            2, 1, "權證多空（最新可得日）")

        # ---- 最賺錢前5分點（跨兩欄）----
        self.tb_title = "近120交易日最賺錢前五分點（買賣均價 · 近一日買賣超）"
        self.tb_card, self.tb_body, self.tb_label = self._card(
            3, 0, self.tb_title, span=2, with_subtitle=True)

        self.status_label = ctk.CTkLabel(
            root, text="載入中…", font=ctk.CTkFont(size=11),
            text_color="gray", anchor="w", justify="left")
        self.status_label.grid(row=4, column=0, columnspan=2, sticky="ew",
                               padx=12, pady=(2, 6))

    def _card(self, row, col, title, span=1, with_subtitle=False):
        card = ctk.CTkFrame(self._grid, corner_radius=12, fg_color="#1b1c1f")
        card.grid(row=row, column=col, columnspan=span, sticky="nsew",
                  padx=6, pady=6)
        ctk.CTkLabel(card, text=title,
                     font=ctk.CTkFont(size=14, weight="bold")).pack(
            anchor="w", padx=16, pady=(12, 2))
        sub = None
        if with_subtitle:
            sub = ctk.CTkLabel(card, text="", font=ctk.CTkFont(size=11),
                               text_color="#8a8a8e")
            sub.pack(anchor="w", padx=16, pady=(0, 2))
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=8, pady=(0, 10))
        if with_subtitle:
            return card, body, sub
        return card, body

    # ================================================================ Bindings
    def _bind_vm(self):
        self.vm.bind("quote", lambda v: self.after(0, self._render_kpi))
        self.vm.bind("daily_trend",
                     lambda v: self.after(0, lambda: self._render_trend(v)))
        self.vm.bind("intraday",
                     lambda v: self.after(0, lambda: self._render_intraday(v)))
        self.vm.bind("peers",
                     lambda v: self.after(0, lambda: self._render_peers(v)))
        self.vm.bind("warrant",
                     lambda v: self.after(0, lambda: self._render_warrant(v)))
        self.vm.bind("top_brokers",
                     lambda v: self.after(0, lambda: self._render_top_brokers(v)))
        self.vm.bind("status",
                     lambda v: self.after(0, lambda: self.status_label.configure(
                         text=v or "")))

    # ================================================================ Render
    def _render_kpi(self):
        q = self.vm.quote
        t = ""
        if q:
            last = q.get("last")
            chg = q.get("change")
            pct = q.get("change_pct")
            o, h, l = q.get("open"), q.get("high"), q.get("low")
            t = q.get("time") or ""
        else:
            dt = self.vm.daily_trend or {}
            closes = dt.get("close") or []
            last = closes[-1] if closes else None
            chg = pct = None
            o = h = l = None
        clr = cs.up_down_color(chg)
        arrow = "▲" if (chg or 0) > 0 else ("▼" if (chg or 0) < 0 else "")
        self.lbl_price.configure(
            text=f"{last:,.2f}" if last is not None else "—", text_color=clr)
        if chg is not None and pct is not None:
            sign = "+" if chg > 0 else ""
            self.lbl_change.configure(
                text=f"{arrow} {sign}{chg:,.2f}  ({sign}{pct:.2f}%)",
                text_color=clr)
        else:
            self.lbl_change.configure(text="")
        parts = []
        if o:
            parts.append(f"開 {o:,.2f}")
        if h:
            parts.append(f"高 {h:,.2f}")
        if l:
            parts.append(f"低 {l:,.2f}")
        if t:
            parts.append(f"⏱ {t}")
        self.lbl_ohl.configure(text="　".join(parts))

    def _clear(self, body):
        for w in body.winfo_children():
            w.destroy()

    def _note(self, body, text):
        self._clear(body)
        ctk.CTkLabel(body, text=text, font=ctk.CTkFont(size=12),
                     text_color="gray").pack(pady=20)

    def _render_trend(self, dt):
        self._render_kpi()      # 若尚無快照，用日線收盤補 KPI
        if not HAS_MPL or not _HAS_TKAGG:
            return self._note(self.trend_body, "（需安裝 matplotlib）")
        if not dt or dt.get("error"):
            return self._note(self.trend_body,
                              (dt or {}).get("error", "（無日線資料）"))
        dates = dt.get("dates") or []
        if not dates:
            return self._note(self.trend_body, "（無日線資料）")
        # 只顯示最後 N 根
        s = slice(-_MAX_DAILY_BARS, None)
        dates = dates[s]
        close = dt["close"][s]
        o = (dt.get("open") or dt["close"])[s]
        h = (dt.get("high") or dt["close"])[s]
        l = (dt.get("low") or dt["close"])[s]
        ma = dt["ma"][s]
        bb_up = dt["bb_up"][s]
        bb_dn = dt["bb_dn"][s]
        vol = dt["vol_lots"][s]
        up = dt["up"][s]
        n = len(close)
        xs = list(range(n))
        # K 棒 OHLC 防呆（缺值以收盤補、保證 high≥body≥low）
        O, H, L = [], [], []
        for i in range(n):
            ci = close[i]
            oi = o[i] if o[i] and o[i] > 0 else ci
            hi = h[i] if h[i] and h[i] > 0 else max(oi, ci)
            li = l[i] if l[i] and l[i] > 0 else min(oi, ci)
            O.append(oi)
            H.append(max(hi, oi, ci))
            L.append(min(li, oi, ci))
        self._clear(self.trend_body)

        fig = Figure(figsize=(4.35, 2.6), dpi=_TREND_DPI, facecolor=cs.BG,
                     layout="constrained")
        fig.get_layout_engine().set(w_pad=0.01, h_pad=0.01)
        ax = fig.add_subplot(111)
        ax.set_facecolor(cs.BG)
        colors = [cs.RED if close[i] >= O[i] else cs.GREEN for i in range(n)]

        # 布林通道（填色 + 虛線邊）
        xb = [i for i in xs if bb_up[i] is not None]
        if xb:
            up_v = [bb_up[i] for i in xb]
            dn_v = [bb_dn[i] for i in xb]
            ax.fill_between(xb, dn_v, up_v, color=cs.FLAT, alpha=0.09, zorder=2)
            ax.plot(xb, up_v, color=cs.FLAT, lw=0.8, alpha=0.5, zorder=3,
                    linestyle=(0, (4, 3)))
            ax.plot(xb, dn_v, color=cs.FLAT, lw=0.8, alpha=0.5, zorder=3,
                    linestyle=(0, (4, 3)))
        # 月線 MA20
        xm = [i for i in xs if ma[i] is not None]
        if xm:
            ax.plot(xm, [ma[i] for i in xm], color=_MA_CLR, lw=1.3, zorder=4,
                    label="月線", solid_capstyle="round")
        # K 棒：實體＝開收、影線＝高低
        ax.vlines(xs, L, H, colors=colors, linewidth=0.8, zorder=5)
        span = (max(H) - min(L)) or 1.0
        bottoms = [min(O[i], close[i]) for i in range(n)]
        heights = [abs(close[i] - O[i]) or span * 0.003 for i in range(n)]
        ax.bar(xs, heights, bottom=bottoms, width=0.62, color=colors,
               linewidth=0, zorder=5)
        # 現價端點光點 + 標籤
        ax.scatter([xs[-1]], [close[-1]], s=26, color=_CLOSE_CLR, zorder=8,
                   edgecolors=cs.BG, linewidths=1.2)
        ax.annotate(f"{close[-1]:,.2f}", (xs[-1], close[-1]),
                    xytext=(-6, 9), textcoords="offset points", ha="right",
                    fontsize=8.5, color=_CLOSE_CLR, fontweight="bold", zorder=8,
                    path_effects=[withStroke(linewidth=2.5, foreground=cs.BG)])
        # 均線扣抵位置（20日）：往回第20根＝明日將被扣抵的收盤價（尖尖綠三角）
        if n >= _DEDUCT_PERIOD:
            xi = n - _DEDUCT_PERIOD
            yi = close[xi]
            ax.plot([xi], [yi], marker="^", markersize=8, color=_DEDUCT_CLR,
                    markeredgecolor="white", markeredgewidth=0.5, linestyle="",
                    zorder=9, clip_on=False, label="扣抵")
            ax.annotate(f"扣抵 {yi:,.2f}", xy=(xi, yi), xytext=(0, -11),
                        textcoords="offset points", ha="center", va="top",
                        fontsize=8, color=_DEDUCT_CLR, zorder=9,
                        path_effects=[withStroke(linewidth=2.5,
                                                 foreground=cs.BG)])
        # y 範圍（含布林通道，留白）
        lo = min(L + ([bb_dn[i] for i in xb] if xb else []))
        hi = max(H + ([bb_up[i] for i in xb] if xb else []))
        pad = (hi - lo) * 0.06 or 1.0
        ax.set_ylim(lo - pad, hi + pad)
        # 軸樣式
        for sp in ax.spines.values():
            sp.set_visible(False)
        ax.grid(True, axis="y", alpha=0.13, color=cs.GRID, linewidth=0.6)
        ax.tick_params(colors=cs.TXT, labelsize=7.5, length=0)
        ax.xaxis.set_major_locator(
            mticker.MaxNLocator(6, integer=True, prune="both"))
        ax.xaxis.set_major_formatter(mticker.FuncFormatter(
            lambda v, _: dates[int(v)][5:] if 0 <= int(v) < n else ""))
        ax.set_xlim(-0.8, n - 0.2)
        ax.legend(loc="upper right", fontsize=7.5, framealpha=0.0,
                  labelcolor=cs.TXT, handlelength=1.2, borderpad=0.1)
        # 底部量棒（量價同框）
        cs.volume_overlay(ax, xs, vol, up)

        # ---- 查價線（十字游標）+ OHLC 資訊框 ----
        vline = ax.axvline(0, color=cs.TXT, lw=0.7, alpha=0.6, zorder=12,
                           visible=False)
        hline = ax.axhline(lo, color=cs.TXT, lw=0.7, alpha=0.6, zorder=12,
                           visible=False)
        ptag = ax.text(1.0, lo, "", transform=ax.get_yaxis_transform(),
                       ha="left", va="center", fontsize=7.5, color="#0d0d0f",
                       zorder=13, visible=False,
                       bbox=dict(boxstyle="round,pad=0.22", fc=cs.TXT,
                                 ec="none"))
        info = ax.text(0.016, 0.97, "", transform=ax.transAxes, ha="left",
                       va="top", fontsize=7.5, color=cs.TXT, linespacing=1.55,
                       zorder=13,
                       bbox=dict(boxstyle="round,pad=0.34", fc="#1b1c1f",
                                 ec="#3a3b40", alpha=0.9))
        self._trend = {"ax": ax, "vline": vline, "hline": hline, "ptag": ptag,
                       "info": info, "n": n, "dates": dates, "O": O, "H": H,
                       "L": L, "close": close, "vol": vol}
        info.set_text(self._trend_info(self._trend, n - 1))

        canvas = FigureCanvasTkAgg(fig, master=self.trend_body)
        canvas.draw()
        w = canvas.get_tk_widget()
        w.configure(bg=cs.BG, highlightthickness=0)
        w.pack(fill="both", expand=True)
        canvas.mpl_connect("motion_notify_event", self._on_trend_hover)
        self._trend["canvas"] = canvas
        self._trend_canvas = canvas      # 保留參考避免 GC

    def _trend_info(self, d, i: int) -> str:
        """查價資訊框文字：日期 + OHLC + 漲跌% + 量。"""
        O, H, L, C, V = d["O"], d["H"], d["L"], d["close"], d["vol"]
        c = C[i]
        prev = C[i - 1] if i > 0 else c
        chg = c - prev
        pct = (chg / prev * 100) if prev else 0.0
        sign = "+" if chg > 0 else ""
        return (f"{d['dates'][i]}\n"
                f"開 {O[i]:,.2f}   高 {H[i]:,.2f}\n"
                f"低 {L[i]:,.2f}   收 {c:,.2f}\n"
                f"漲跌 {sign}{chg:,.2f} ({sign}{pct:.2f}%)　量 {V[i]:,} 張")

    def _on_trend_hover(self, ev):
        """滑鼠移動：更新查價十字線 + 資訊框；移出圖區則恢復最新一根。"""
        d = getattr(self, "_trend", None)
        if not d:
            return
        if ev.inaxes is not d["ax"] or ev.xdata is None:
            for a in (d["vline"], d["hline"], d["ptag"]):
                a.set_visible(False)
            d["info"].set_text(self._trend_info(d, d["n"] - 1))
            d["canvas"].draw_idle()
            return
        i = max(0, min(d["n"] - 1, int(round(ev.xdata))))
        d["vline"].set_xdata([i, i])
        d["vline"].set_visible(True)
        d["hline"].set_ydata([ev.ydata, ev.ydata])
        d["hline"].set_visible(True)
        d["ptag"].set_y(ev.ydata)
        d["ptag"].set_text(f" {ev.ydata:,.2f} ")
        d["ptag"].set_visible(True)
        d["info"].set_text(self._trend_info(d, i))
        d["canvas"].draw_idle()

    def _render_intraday(self, intra):
        if not HAS_MPL:
            return self._note(self.intra_body, "（需安裝 matplotlib）")
        if not intra or intra.get("error"):
            return self._note(self.intra_body,
                              (intra or {}).get("error", "（無即時資料）"))
        bars = intra.get("bars") or []
        if not bars:
            return self._note(self.intra_body, "（暫無分 K 資料）")
        times = [b["time"] for b in bars]
        close = [b["close"] for b in bars]
        vol = [b["volume"] for b in bars]
        up = [b["close"] >= b["open"] for b in bars]
        n = len(bars)
        xs = list(range(n))
        base = bars[0]["open"]
        clr = cs.RED if close[-1] >= base else cs.GREEN
        self._clear(self.intra_body)

        fig, ax = cs.new_fig(_TREND_W, _TREND_H)
        cs.gradient_fill(ax, xs, close, base, clr)
        ax.axhline(base, color=cs.FLAT, linewidth=1.0, linestyle=(0, (4, 3)),
                   alpha=0.5, zorder=2)
        cs.line(ax, xs, close, clr, width=1.5, zorder=5)
        cs.end_marker(ax, xs[-1], close[-1], clr, f"{close[-1]:,.2f}")
        cs.style_axis(ax, thousands=False)
        cs.volume_overlay(ax, xs, vol, up)
        step = max(n // 6, 1)
        ticks = list(range(0, n, step))
        if ticks and ticks[-1] != n - 1:
            ticks.append(n - 1)
        ax.set_xticks(ticks)
        ax.set_xticklabels([times[t] for t in ticks])
        ax.set_xlim(-0.6, n - 0.4)
        lo, hi = min(min(close), base), max(max(close), base)
        pad = (hi - lo) * 0.08 or 0.5
        ax.set_ylim(lo - pad, hi + pad)
        cs.embed(self.intra_body, fig, _TREND_W, _TREND_H)

    def _render_peers(self, peers):
        self._clear(self.peer_body)
        if not peers:
            ctk.CTkLabel(self.peer_body, text="（無同類股資料）",
                         font=ctk.CTkFont(size=12), text_color="gray").pack(
                pady=16)
            return
        cols = [("code", "代碼", 52, "center"), ("name", "名稱", 84, "w"),
                ("close", "價", 70, "e"), ("vol", "量(張)", 66, "e"),
                ("chg", "漲跌%", 64, "e")]
        header = ctk.CTkFrame(self.peer_body, fg_color="transparent")
        header.pack(fill="x", padx=6, pady=(2, 2))
        for i, (_k, t, w, a) in enumerate(cols):
            header.grid_columnconfigure(i, minsize=w, weight=1 if _k == "name"
                                        else 0)
            ctk.CTkLabel(header, text=t, font=ctk.CTkFont(size=12,
                         weight="bold"), text_color="#8a8a8e",
                         anchor=("w" if a == "w" else "e" if a == "e"
                                 else "center")).grid(
                row=0, column=i, sticky="ew", padx=3)
        for idx, p in enumerate(peers, 1):
            rowf = ctk.CTkFrame(self.peer_body,
                                fg_color="#1d1e21" if idx % 2 == 0
                                else "transparent", corner_radius=4)
            rowf.pack(fill="x", padx=6, pady=1)
            for i, (_k, _t, w, _a) in enumerate(cols):
                rowf.grid_columnconfigure(i, minsize=w, weight=1
                                          if _k == "name" else 0)
            chg = p.get("change_pct")
            cclr = cs.up_down_color(chg)
            cells = [
                (p["code"], "#d4d4d4", "center"),
                (p["name"], "#e6e6e6", "w"),
                (f"{p['close']:,.2f}", cclr, "e"),
                (f"{p['volume_lots']:,}", "#c7c7cc", "e"),
                (f"{chg:+.2f}" if chg is not None else "—", cclr, "e"),
            ]
            for i, (text, clr, a) in enumerate(cells):
                ctk.CTkLabel(rowf, text=text, text_color=clr,
                             font=ctk.CTkFont(size=12),
                             anchor=("w" if a == "w" else "e" if a == "e"
                                     else "center")).grid(
                    row=0, column=i, sticky="ew", padx=3, pady=2)

    def _render_warrant(self, w):
        self._clear(self.warr_body)
        if not w:
            ctk.CTkLabel(self.warr_body, text="（無權證資料）",
                         font=ctk.CTkFont(size=12), text_color="gray").pack(
                pady=16)
            return
        bias = w.get("bias", "中性")
        share = w.get("long_share", 50.0)      # 多方佔比%
        clr = _WARRANT_CLR.get(bias, "#8a8a8e")
        top = ctk.CTkFrame(self.warr_body, fg_color="transparent")
        top.pack(fill="x", padx=16, pady=(6, 4))
        ctk.CTkLabel(top, text=bias, height=30, corner_radius=8,
                     fg_color=clr, text_color="#ffffff",
                     font=ctk.CTkFont(size=18, weight="bold")).pack(
            side="left", padx=(0, 12), ipadx=10)
        ctk.CTkLabel(top, text=f"多方佔比 {share:.1f}%",
                     font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=clr).pack(side="left")
        # 多空比例橫條（紅=多、綠=空）
        bar = ctk.CTkFrame(self.warr_body, height=22, corner_radius=6,
                           fg_color=cs.GREEN)
        bar.pack(fill="x", padx=16, pady=(6, 4))
        bar.pack_propagate(False)
        longf = ctk.CTkFrame(bar, corner_radius=0, fg_color=cs.RED)
        longf.place(relx=0, rely=0, relwidth=max(0.0, min(1.0, share / 100.0)),
                    relheight=1.0)
        legend = ctk.CTkFrame(self.warr_body, fg_color="transparent")
        legend.pack(fill="x", padx=16, pady=(0, 4))
        ctk.CTkLabel(legend, text=f"多方 {share:.1f}%", text_color=cs.RED,
                     font=ctk.CTkFont(size=12)).pack(side="left")
        ctk.CTkLabel(legend, text=f"空方 {100 - share:.1f}%", text_color=cs.GREEN,
                     font=ctk.CTkFont(size=12)).pack(side="right")
        ctk.CTkLabel(
            self.warr_body,
            text="註：權證多空為當日彙總（認購/牛=多、認售/熊=空，依成交金額），非逐筆即時。",
            font=ctk.CTkFont(size=11), text_color="#6a6a6a",
            wraplength=460, justify="left").pack(anchor="w", padx=16,
                                                 pady=(4, 4))

    def _render_top_brokers(self, data):
        self._clear(self.tb_body)
        if not data:
            return
        sessions = data.get("sessions", 0)
        brokers = data.get("brokers") or []
        # 副標：說明實際統計交易日數（可能不足 120）
        note = f"統計 {sessions} 個交易日" if sessions >= 120 else \
            f"統計 {sessions} 個交易日（資料不足 120，以現有為準）"
        self.tb_label.configure(text=note)
        if data.get("error"):
            ctk.CTkLabel(self.tb_body, text=f"（載入失敗：{data['error']}）",
                         font=ctk.CTkFont(size=12), text_color="gray").pack(
                pady=12)
            return
        if not brokers:
            ctk.CTkLabel(self.tb_body, text="（無分點交易資料）",
                         font=ctk.CTkFont(size=12), text_color="gray").pack(
                pady=12)
            return
        cols = [("rank", "#", 28, "center"), ("name", "分點", 118, "w"),
                ("buy", "買均價", 76, "e"), ("sell", "賣均價", 76, "e"),
                ("bs", "買/賣(張)", 108, "e"),
                ("pnl", "區間損益(千)", 96, "e"),
                ("last", "近一日(張)", 84, "e")]
        header = ctk.CTkFrame(self.tb_body, fg_color="transparent")
        header.pack(fill="x", padx=8, pady=(2, 2))
        for i, (_k, t, w, a) in enumerate(cols):
            header.grid_columnconfigure(i, minsize=w, weight=1 if _k == "name"
                                        else 0)
            ctk.CTkLabel(header, text=t, font=ctk.CTkFont(size=12,
                         weight="bold"), text_color="#8a8a8e",
                         anchor=("w" if a == "w" else "e" if a == "e"
                                 else "center")).grid(
                row=0, column=i, sticky="ew", padx=4)
        for idx, b in enumerate(brokers, 1):
            rowf = ctk.CTkFrame(self.tb_body,
                                fg_color="#1d1e21" if idx % 2 == 0
                                else "transparent", corner_radius=4)
            rowf.pack(fill="x", padx=8, pady=1)
            for i, (_k, _t, w, _a) in enumerate(cols):
                rowf.grid_columnconfigure(i, minsize=w, weight=1
                                          if _k == "name" else 0)
            ln = b["last_net_lots"]
            lclr = cs.RED if ln > 0 else cs.GREEN if ln < 0 else "#8a8a8e"
            pnl_k = b["pnl"] / 1000
            pclr = cs.RED if pnl_k > 0 else cs.GREEN if pnl_k < 0 else "#c7c7cc"
            cells = [
                (str(idx), "#7a7a7e", "center"),
                (b["name"], "#e6e6e6", "w"),
                (f"{b['buy_avg']:,.2f}", "#c7c7cc", "e"),
                (f"{b['sell_avg']:,.2f}", "#c7c7cc", "e"),
                (f"{b['buy_lots']:,}/{b['sell_lots']:,}", "#c7c7cc", "e"),
                (f"{pnl_k:+,.0f}", pclr, "e"),
                (f"{ln:+,}", lclr, "e"),
            ]
            for i, (text, clr, a) in enumerate(cells):
                ctk.CTkLabel(rowf, text=text, text_color=clr,
                             font=ctk.CTkFont(size=12),
                             anchor=("w" if a == "w" else "e" if a == "e"
                                     else "center")).grid(
                    row=0, column=i, sticky="ew", padx=4, pady=2)

    # ================================================================ Close
    def _on_close(self):
        try:
            self.vm.shutdown()
        except Exception:
            pass
        self.destroy()
