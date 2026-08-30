"""隔日沖監控 分頁。

Phase 1：載入前一交易日「自營避險買入」排行候選表，勾選要盤中監控的標的。
欄位：勾選 / 代碼 / 名稱 / 避險買(張) / 避險淨(張) / 三大法人(張) / 主力成本 /
漲跌% / 權證多方%。UI 更新一律 self.after marshal。
"""

from __future__ import annotations

import customtkinter as ctk

from viewmodels.nextday_monitor_viewmodel import NextDayMonitorViewModel
from views import chart_style as cs
from views.chart_style import HAS_MPL

try:
    from matplotlib.figure import Figure
    import matplotlib.ticker as mticker
    _HAS_MPL2 = True
except Exception:      # noqa: BLE001
    _HAS_MPL2 = False

# 回放事件標記樣式
_NR_EV_STYLE = {
    "sell_pressure": dict(marker="*", color=cs.RED, s=190, label="自營賣壓"),
    "call_dump": dict(marker="v", color="#ff9800", s=90, label="認購倒賣"),
    "put_load": dict(marker="^", color="#b57bd6", s=90, label="認售買進"),
    "below_cost": dict(marker="o", facecolors="none", edgecolors=cs.RED,
                       s=120, linewidths=1.8, label="破主力成本"),
}
_NR_EV_ORDER = ["sell_pressure", "call_dump", "put_load", "below_cost"]

# 候選表欄位 (key, 標題, 寬px, 對齊, 權重)
_COLS = [
    ("sel", "監控", 42, "center", 0),
    ("code", "代碼", 50, "center", 0),
    ("name", "名稱", 80, "w", 1),
    ("price", "現價", 62, "e", 0),
    ("chgval", "漲跌", 54, "e", 0),
    ("chg", "漲幅%", 58, "e", 0),
    ("vol", "成交量(張)", 82, "e", 0),
    ("turn", "周轉率%", 66, "e", 0),
    ("hbuy", "避險買(張)", 80, "e", 0),
    ("hnet", "避險淨(張)", 80, "e", 0),
    ("insti", "三大法人(張)", 90, "e", 0),
    ("cost", "主力成本", 70, "e", 0),
    ("warr", "權證多方%", 76, "e", 0),
]
# 即時監控欄位
_MON_COLS = [
    ("code", "代碼", 54, "center", 0),
    ("name", "名稱", 88, "w", 1),
    ("price", "現價", 72, "e", 0),
    ("chg", "漲跌%", 62, "e", 0),
    ("callnet", "認購買賣超", 88, "e", 0),
    ("callchg", "認購漲跌%", 82, "e", 0),
    ("putnet", "認售買賣超", 88, "e", 0),
    ("putchg", "認售漲跌%", 82, "e", 0),
    ("press", "自營賣壓", 84, "e", 0),
    ("sig", "訊號", 88, "center", 0),
]
_WARR_CLR = {"多": cs.RED, "空": cs.GREEN, "中性": "#8a8a8e"}
_MON_REFRESH_MS = 1000


class NextDayMonitorView(ctk.CTkFrame):
    """隔日沖監控 tab page。"""

    def __init__(self, parent, viewmodel: NextDayMonitorViewModel):
        super().__init__(parent, fg_color="transparent")
        self.vm = viewmodel
        self._row_widgets: list = []
        self._mon_widgets: list = []
        self._sel_vars: dict[str, ctk.BooleanVar] = {}
        self._record_map: dict[str, str] = {}
        self._build_ui()
        self._bind_vm()
        self._mon_refresh()

    # ================================================================ UI
    def _build_ui(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=(16, 4))
        ctk.CTkLabel(top, text="隔日沖監控 · 自營避險買入排行",
                     font=ctk.CTkFont(size=22, weight="bold")).pack(side="left")

        ctrl = ctk.CTkFrame(self, fg_color="transparent")
        ctrl.pack(fill="x", padx=20, pady=(4, 4))
        self.load_btn = ctk.CTkButton(
            ctrl, text="載入候選", width=96, height=32, corner_radius=8,
            font=ctk.CTkFont(size=13, weight="bold"), command=self._on_load)
        self.load_btn.pack(side="left", padx=(0, 8))
        self.monitor_btn = ctk.CTkButton(
            ctrl, text="開始監控所選", width=120, height=32, corner_radius=8,
            fg_color="#2e7d32", hover_color="#256628",
            font=ctk.CTkFont(size=13, weight="bold"), command=self._on_monitor)
        self.monitor_btn.pack(side="left", padx=(0, 6))
        self.stop_btn = ctk.CTkButton(
            ctrl, text="停止", width=64, height=32, corner_radius=8,
            fg_color="#c0392b", hover_color="#a93226",
            font=ctk.CTkFont(size=13, weight="bold"), command=self._on_stop)
        self.stop_btn.pack(side="left")

        self.status_label = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(size=12), text_color="gray",
            anchor="w", justify="left", wraplength=1100)
        self.status_label.pack(fill="x", padx=22, pady=(2, 6))

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=0, pady=0)

        # ---- 下方：盤中即時權證監控（固定高度，置底）----
        mon_card = ctk.CTkFrame(body, corner_radius=12, height=250)
        mon_card.pack(side="bottom", fill="x", padx=16, pady=(0, 8))
        mon_card.pack_propagate(False)
        montop = ctk.CTkFrame(mon_card, fg_color="transparent")
        montop.pack(fill="x", padx=12, pady=(10, 2))
        ctk.CTkLabel(montop, text="盤中即時 · 權證買賣壓（自營避險鬆動偵測）",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        self.mon_status = ctk.CTkLabel(
            montop, text="", font=ctk.CTkFont(size=11), text_color="#8a8a8e")
        self.mon_status.pack(side="left", padx=(10, 0))
        # 事件回放
        ctk.CTkButton(montop, text="回放", width=60, height=28, corner_radius=8,
                      font=ctk.CTkFont(size=12, weight="bold"),
                      command=self._on_load_replay).pack(side="right", padx=(6, 0))
        self.replay_menu = ctk.CTkOptionMenu(
            montop, values=["（尚無錄製）"], width=250, height=28,
            font=ctk.CTkFont(size=11))
        self.replay_menu.pack(side="right")
        ctk.CTkLabel(montop, text="事件回放：", font=ctk.CTkFont(size=12)).pack(
            side="right", padx=(0, 4))
        monhdr = ctk.CTkFrame(mon_card, fg_color="transparent")
        monhdr.pack(fill="x", padx=12, pady=(2, 2))
        for i, (_k, title, w, anc, weight) in enumerate(_MON_COLS):
            monhdr.grid_columnconfigure(i, weight=weight, minsize=w)
            ctk.CTkLabel(monhdr, text=title, font=ctk.CTkFont(size=12,
                         weight="bold"), text_color="#8a8a8e",
                         anchor=("center" if anc == "center" else "e"
                                 if anc == "e" else "w")).grid(
                row=0, column=i, sticky="ew", padx=3)
        self.mon_table = ctk.CTkScrollableFrame(mon_card, fg_color="transparent")
        self.mon_table.pack(fill="both", expand=True, padx=6, pady=(0, 8))
        for i, (_k, _t, w, _a, weight) in enumerate(_MON_COLS):
            self.mon_table.grid_columnconfigure(i, weight=weight, minsize=w)

        # ---- 上方：候選表（填滿剩餘空間）----
        card = ctk.CTkFrame(body, corner_radius=12)
        card.pack(side="top", fill="both", expand=True, padx=16, pady=(0, 8))
        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(12, 2))
        for i, (_k, title, w, anc, weight) in enumerate(_COLS):
            header.grid_columnconfigure(i, weight=weight, minsize=w)
            ctk.CTkLabel(header, text=title,
                         font=ctk.CTkFont(size=12, weight="bold"),
                         text_color="#8a8a8e",
                         anchor=("center" if anc == "center"
                                 else "e" if anc == "e" else "w")).grid(
                row=0, column=i, sticky="ew", padx=3)
        self.table = ctk.CTkScrollableFrame(card, fg_color="transparent")
        self.table.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        for i, (_k, _t, w, _a, weight) in enumerate(_COLS):
            self.table.grid_columnconfigure(i, weight=weight, minsize=w)
        ctk.CTkLabel(
            card, text="說明：自營商發行權證後以標的避險（認購→買股）。前一日「避險買入」較多者，"
            "隔日若認購被倒賣/認售被買進，自營可能賣股解避險。勾選後按「開始監控所選」即時追蹤其權證"
            "買賣壓；自營賣壓＝認售淨買−認購淨買，正值偏賣股。（期貨欄位待接 TAIFEX 個股期貨）",
            font=ctk.CTkFont(size=11), text_color="#6a6a6a",
            anchor="w", justify="left", wraplength=1120).pack(
            fill="x", padx=12, pady=(0, 8))

    # ================================================================ Bindings
    def _bind_vm(self):
        self.vm.bind("candidates",
                     lambda v: self.after(0, lambda: self._render(v)))
        self.vm.bind("status", lambda v: self.after(
            0, lambda: self.status_label.configure(text=v or "")))
        self.vm.bind("monitor_rows",
                     lambda v: self.after(0, lambda: self._render_monitor(v)))
        self.vm.bind("monitor_status", lambda v: self.after(
            0, lambda: self.mon_status.configure(text=v or "")))
        self.vm.bind("records",
                     lambda v: self.after(0, lambda: self._render_record_list(v)))
        self.status_label.configure(text=self.vm.status)
        self._render_record_list(self.vm.records)

    def _on_load(self):
        self.vm.load()

    def _on_monitor(self):
        if not self.vm.selected:
            self.status_label.configure(text="請先勾選至少一檔要監控的標的")
            return
        self.vm.start_monitor()

    def _on_stop(self):
        self.vm.stop_monitor()
        self.mon_status.configure(text="已停止監控")

    def _on_toggle(self, code: str):
        var = self._sel_vars.get(code)
        if var is not None:
            self.vm.toggle(code, bool(var.get()))

    def _mon_refresh(self):
        """節流：定時把最新權證彙總推到 UI。"""
        try:
            self.vm.refresh_monitor()
        except Exception:
            pass
        self.after(_MON_REFRESH_MS, self._mon_refresh)

    # ================================================================ Render
    def _render(self, data):
        for w in self._row_widgets:
            w.destroy()
        self._row_widgets = []
        rows = (data or {}).get("rows") or []
        if not rows:
            return
        col = {c[0]: i for i, c in enumerate(_COLS)}
        for idx, r in enumerate(rows, 1):
            rowf = ctk.CTkFrame(self.table,
                                fg_color="#1d1e21" if idx % 2 == 0
                                else "transparent", corner_radius=4)
            rowf.grid(row=idx, column=0, columnspan=len(_COLS), sticky="ew",
                      pady=1)
            for i, (_k, _t, w, _a, weight) in enumerate(_COLS):
                rowf.grid_columnconfigure(i, weight=weight, minsize=w)
            self._fill_row(rowf, r, col)
            self._row_widgets.append(rowf)

    def _fill_row(self, rowf, r, col):
        code = r["stock_code"]

        def lbl(key, text, clr, anc="e", bold=False):
            ctk.CTkLabel(rowf, text=text, text_color=clr,
                         font=ctk.CTkFont(size=12,
                                          weight="bold" if bold else "normal"),
                         anchor=("center" if anc == "center"
                                 else "e" if anc == "e" else "w")).grid(
                row=0, column=col[key], sticky="ew", padx=3, pady=3)

        # 勾選
        var = self._sel_vars.get(code)
        if var is None:
            var = ctk.BooleanVar(value=code in self.vm.selected)
            self._sel_vars[code] = var
        ctk.CTkCheckBox(rowf, text="", width=24, variable=var,
                        checkbox_width=18, checkbox_height=18,
                        command=lambda c=code: self._on_toggle(c)).grid(
            row=0, column=col["sel"], padx=3)

        lbl("code", code, "#d4d4d4", "center")
        lbl("name", r.get("name") or code, "#e6e6e6", "w")
        # 現價 / 漲跌元 / 漲幅%（同用漲跌色）
        chg = r.get("change_pct")
        cclr = cs.up_down_color(chg)
        price = r.get("close")
        lbl("price", f"{price:,.2f}" if price else "—", cclr, "e", bold=True)
        cv = r.get("change_val")
        lbl("chgval", f"{cv:+.2f}" if cv is not None else "—", cclr, "e")
        lbl("chg", f"{chg:+.2f}" if chg is not None else "—", cclr, "e")
        vl = r.get("volume_lots")
        lbl("vol", f"{vl:,}" if vl is not None else "—", "#c7c7cc", "e")
        tp = r.get("turnover_pct")
        lbl("turn", f"{tp:.2f}" if tp is not None else "—",
            cs.AMBER if (tp is not None and tp >= 10) else "#c7c7cc", "e",
            bold=(tp is not None and tp >= 10))
        # 避險買（紅＝買）
        lbl("hbuy", f"{r.get('hedge_buy_lots', 0):+,}", cs.RED, "e", bold=True)
        hn = r.get("hedge_net_lots", 0)
        lbl("hnet", f"{hn:+,}", cs.RED if hn > 0 else cs.GREEN if hn < 0
            else "#8a8a8e", "e")
        ins = r.get("three_insti_lots", 0)
        lbl("insti", f"{ins:+,}", cs.RED if ins > 0 else cs.GREEN if ins < 0
            else "#8a8a8e", "e")
        cost = r.get("main_buy_cost")
        lbl("cost", f"{cost:,.2f}" if cost else "—", "#c7c7cc", "e")
        ws = r.get("warrant_long_share")
        wclr = _WARR_CLR.get(r.get("warrant_bias"), "#8a8a8e")
        lbl("warr", f"{ws:.0f}" if ws is not None else "—", wclr, "e",
            bold=(ws is not None and (ws >= 55 or ws <= 45)))

    # ================================================================ 即時監控
    def _render_monitor(self, rows):
        for w in self._mon_widgets:
            w.destroy()
        self._mon_widgets = []
        rows = rows or []
        col = {c[0]: i for i, c in enumerate(_MON_COLS)}
        for idx, r in enumerate(rows, 1):
            press = r.get("sell_pressure", 0)
            # 自營賣壓高 → 整列淡紅底提示
            if press >= 200:
                bg = "#3a1414"
            elif press <= -200:
                bg = "#0f3020"
            else:
                bg = "#1d1e21" if idx % 2 == 0 else "transparent"
            rowf = ctk.CTkFrame(self.mon_table, fg_color=bg, corner_radius=4)
            rowf.grid(row=idx, column=0, columnspan=len(_MON_COLS), sticky="ew",
                      pady=1)
            for i, (_k, _t, w, _a, weight) in enumerate(_MON_COLS):
                rowf.grid_columnconfigure(i, weight=weight, minsize=w)
            self._fill_mon_row(rowf, r, col)
            self._mon_widgets.append(rowf)

    def _fill_mon_row(self, rowf, r, col):
        def lbl(key, text, clr, anc="e", bold=False):
            ctk.CTkLabel(rowf, text=text, text_color=clr,
                         font=ctk.CTkFont(size=12,
                                          weight="bold" if bold else "normal"),
                         anchor=("center" if anc == "center"
                                 else "e" if anc == "e" else "w")).grid(
                row=0, column=col[key], sticky="ew", padx=3, pady=3)

        chg = r.get("change_pct")
        cclr = cs.up_down_color(chg)
        price = r.get("price") or 0
        lbl("code", r.get("code", ""), "#d4d4d4", "center")
        lbl("name", r.get("name") or r.get("code", ""), "#e6e6e6", "w")
        lbl("price", f"{price:,.2f}" if price else "—", cclr, "e", bold=True)
        lbl("chg", f"{chg:+.2f}" if chg is not None else "—", cclr, "e")
        # 認購買賣超（外盤買為紅、內盤賣為綠）
        cn = r.get("call_net", 0)
        lbl("callnet", f"{cn:+,}", cs.RED if cn > 0 else cs.GREEN if cn < 0
            else "#8a8a8e", "e", bold=abs(cn) >= 100)
        cc = r.get("call_chg", 0.0)
        lbl("callchg", f"{cc:+.2f}", cs.up_down_color(cc), "e")
        pn = r.get("put_net", 0)
        lbl("putnet", f"{pn:+,}", cs.RED if pn > 0 else cs.GREEN if pn < 0
            else "#8a8a8e", "e", bold=abs(pn) >= 100)
        pc = r.get("put_chg", 0.0)
        lbl("putchg", f"{pc:+.2f}", cs.up_down_color(pc), "e")
        # 自營賣壓：正＝偏賣股（紅警示）、負＝偏加碼避險（綠）
        press = r.get("sell_pressure", 0)
        pclr = cs.RED if press > 0 else cs.GREEN if press < 0 else "#8a8a8e"
        lbl("press", f"{press:+,}", pclr, "e", bold=abs(press) >= 200)
        if press >= 200:
            sig, sclr = "⚠ 自營賣壓", cs.RED
        elif press <= -200:
            sig, sclr = "加碼避險", cs.GREEN
        else:
            sig, sclr = "—", "#6a6a6a"
        lbl("sig", sig, sclr, "center", bold=press >= 200)

    # ================================================================ 回放
    def _render_record_list(self, records):
        records = records or []
        self._record_map = {}
        values = []
        for r in records:
            disp = (f"{r['code']} {r.get('name') or ''} · {r.get('date', '')}"
                    f" · {r.get('events', 0)}事件").strip()
            if disp in self._record_map:
                disp += f" ({len(self._record_map)})"
            self._record_map[disp] = r["path"]
            values.append(disp)
        if values:
            self.replay_menu.configure(values=values)
            if self.replay_menu.get() not in values:
                self.replay_menu.set(values[0])
        else:
            self.replay_menu.configure(values=["（尚無錄製）"])
            self.replay_menu.set("（尚無錄製）")

    def _on_load_replay(self):
        path = self._record_map.get(self.replay_menu.get())
        if not path:
            self.mon_status.configure(text="尚無可回放的錄製檔（監控後自動產生）")
            return
        record = self.vm.load_record(path)
        if record and (record.get("samples")):
            self._open_replay(record)

    def _open_replay(self, record):
        """回放視窗：上＝標的價格走勢 + 事件標記（含主力成本線）；下＝自營賣壓。"""
        if not HAS_MPL or not _HAS_MPL2:
            self.mon_status.configure(text="（需安裝 matplotlib 才能回放）")
            return
        samples = record.get("samples") or []
        events = record.get("events") or []
        n = len(samples)
        price = [s["p"] for s in samples]
        sp = [s.get("sp", 0) for s in samples]
        times = [s.get("t", "") for s in samples]
        xs = list(range(n))
        cost = record.get("main_cost") or 0.0
        sp_th = record.get("sp_th", 200)

        top = ctk.CTkToplevel(self)
        top.title(f"隔日沖回放 · {record.get('code','')} {record.get('name','')}"
                  f" · {record.get('date','')}")
        top.geometry("1200x760")
        top.configure(fg_color="#151517")
        top.transient(self.winfo_toplevel())
        top.after(60, top.lift)
        head = ctk.CTkFrame(top, fg_color="#1b1c1f", corner_radius=10)
        head.pack(fill="x", padx=12, pady=(12, 6))
        ctk.CTkLabel(
            head, text=f"{record.get('code','')} {record.get('name','')} · "
            f"{record.get('date','')} · {n:,} 筆 · {len(events)} 事件 · "
            f"{record.get('started_at','')}–{record.get('ended_at','')}"
            + (f"　主力成本 {cost:,.2f}" if cost else ""),
            font=ctk.CTkFont(size=14, weight="bold")).pack(side="left",
                                                           padx=14, pady=8)
        body = ctk.CTkFrame(top, fg_color="#1b1c1f", corner_radius=10)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        fig = Figure(figsize=(12.0, 6.4), dpi=200, facecolor=cs.BG,
                     layout="constrained")
        gs = fig.add_gridspec(3, 1, hspace=0.10)
        ax1 = fig.add_subplot(gs[0:2, 0])
        ax2 = fig.add_subplot(gs[2, 0], sharex=ax1)
        for ax in (ax1, ax2):
            ax.set_facecolor(cs.BG)
        clr = cs.RED if (n and price[-1] >= (price[0] or 0)) else cs.GREEN
        ax1.plot(xs, price, color=clr, lw=2.4, solid_capstyle="round", zorder=5)
        if cost:
            ax1.axhline(cost, color=cs.AMBER, lw=1.6, linestyle=(0, (5, 3)),
                        alpha=0.8, zorder=3)
            ax1.annotate(f"主力成本 {cost:,.2f}", xy=(0, cost),
                         xytext=(4, 4), textcoords="offset points",
                         fontsize=15, color=cs.AMBER)
        # 事件標記（分組）
        groups = {}
        for ev in events:
            i = ev.get("i")
            if i is None or not (0 <= i < n):
                continue
            groups.setdefault(ev["type"], ([], []))
            groups[ev["type"]][0].append(i)
            groups[ev["type"]][1].append(price[i])
        for et in _NR_EV_ORDER:
            if et in groups:
                gx, gy = groups[et]
                ax1.scatter(gx, gy, zorder=9, **_NR_EV_STYLE[et])
        if groups:
            ax1.legend(loc="upper left", fontsize=14, framealpha=0.0,
                       labelcolor=cs.TXT, ncol=4, columnspacing=1.0,
                       handlelength=1.2)
        # 自營賣壓面板
        ax2.axhline(sp_th, color=cs.RED, lw=1.2, linestyle=(0, (4, 3)),
                    alpha=0.6)
        ax2.axhline(0, color=cs.FLAT, lw=1.0, alpha=0.4)
        pos = [max(v, 0) for v in sp]
        neg = [min(v, 0) for v in sp]
        ax2.fill_between(xs, 0, pos, color=cs.RED, alpha=0.30, zorder=2)
        ax2.fill_between(xs, 0, neg, color=cs.GREEN, alpha=0.30, zorder=2)
        ax2.plot(xs, sp, color=cs.TXT, lw=1.2, zorder=4)
        ax2.set_ylabel("自營賣壓", fontsize=14, color=cs.TXT)

        for ax in (ax1, ax2):
            for spn in ax.spines.values():
                spn.set_visible(False)
            ax.grid(True, axis="y", alpha=0.13, color=cs.GRID, linewidth=0.8)
            ax.tick_params(colors=cs.TXT, labelsize=13, length=0)
        m = max(n, 1)
        step = max(m // 8, 1)
        ticks = list(range(0, m, step))
        ax2.set_xticks(ticks)
        ax2.set_xticklabels([times[t][:5] if 0 <= t < n else "" for t in ticks])
        ax1.tick_params(labelbottom=False)
        ax1.set_xlim(-0.5, m - 0.5)
        top._embed = cs.embed(body, fig, 12.0, 6.4)
