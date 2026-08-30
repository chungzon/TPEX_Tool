"""即時神手（連次連量多檔監控）分頁。

輸入股票代碼 → 開始監控 → 表格即時顯示各檔連次連量、內外盤、大單。
需正式環境登入永豐才有即時逐筆。UI 更新一律 self.after marshal；表格以
定時 refresh 節流刷新（tick 高頻，不逐筆重繪）。
"""

from __future__ import annotations

import customtkinter as ctk

from viewmodels.tick_monitor_viewmodel import TickMonitorViewModel
from views import chart_style as cs
from views.chart_style import HAS_MPL
from services import shenshou_record_service as rec

try:      # 互動式回放圖（比照日線趨勢圖：可滑鼠查價）
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    import matplotlib.ticker as mticker
    from matplotlib.patheffects import withStroke
    _HAS_TKAGG = True
except Exception:      # noqa: BLE001
    _HAS_TKAGG = False

# (key, 標題, 寬px, 對齊, 權重)
_COLS = [
    ("code", "代碼", 52, "center", 0),
    ("name", "名稱", 70, "w", 1),
    ("price", "成交價", 76, "e", 0),
    ("pct", "漲跌%", 62, "e", 0),
    ("side", "內外盤", 54, "center", 0),
    ("streak", "連次", 70, "center", 0),
    ("svol", "連量(張)", 70, "e", 0),
    ("oi", "外/內盤(張)", 120, "e", 0),
    ("oratio", "外盤比", 60, "e", 0),
    ("big", "大單", 68, "center", 0),
    ("total", "總量(張)", 76, "e", 0),
]

_SIDE_STYLE = {1: ("外盤", cs.RED), -1: ("內盤", cs.GREEN), 0: ("—", "#8a8a8e")}
_REFRESH_MS = 800       # 表格節流刷新間隔

_REPLAY_W, _REPLAY_H = 11.4, 3.0     # 回放江波圖邏輯英吋
_REPLAY_DPI = 130                    # 互動回放圖解析度（比照日線趨勢圖）
_LIVE_W_WIDE, _LIVE_H = 11.0, 2.6    # 即時江波圖（≤2 檔時單欄寬版）
_LIVE_W, _LIVE_H2 = 5.4, 2.5         # 即時江波圖（>2 檔時雙欄）
_LIVE_REFRESH_MS = 3000              # 即時江波圖重繪間隔（比表格慢，省 CPU）
_MAX_PLOT_POINTS = 3000              # 江波圖線/量最多描點（超過則等距抽樣）
# 事件標記樣式：賣盤竭盡=綠圈 o、買盤竭盡=紅圈、連買達標=紅三角、連賣達標=綠三角
_EV_STYLE = {
    rec.EV_SELL_EXHAUST: dict(marker="o", facecolors="none", edgecolors=cs.GREEN,
                              s=120, linewidths=1.8, label="賣盤竭盡"),
    rec.EV_BUY_EXHAUST: dict(marker="o", facecolors="none", edgecolors=cs.RED,
                             s=120, linewidths=1.8, label="買盤竭盡"),
    rec.EV_BUY_STREAK: dict(marker="^", color=cs.RED, s=80, label="連買達標"),
    rec.EV_SELL_STREAK: dict(marker="v", color=cs.GREEN, s=80, label="連賣達標"),
}
_EV_ORDER = [rec.EV_SELL_EXHAUST, rec.EV_BUY_EXHAUST,
             rec.EV_BUY_STREAK, rec.EV_SELL_STREAK]


class JiangboChart:
    """互動式江波圖（可重用）：價量走勢 + 事件標記，比照日線趨勢圖樣式。

    支援 滾輪縮放（以游標為中心，x 軸）、拖曳平移、雙擊還原、滑鼠查價十字線+資訊框。
    自帶狀態，同頁可建立多個實例（內嵌小圖 + 放大視窗）互不干擾。需 matplotlib+tkagg。
    """

    def __init__(self, parent, record, figsize=(_REPLAY_W, _REPLAY_H)):
        self.record = record or {}
        self._d: dict = {}
        self.canvas = None
        self._build(parent, figsize)

    def _build(self, parent, figsize):
        record = self.record
        samples = record.get("samples") or []
        events = record.get("events") or []
        base = (record.get("prev_close") or record.get("open")
                or (samples[0]["p"] if samples else 0.0) or 0.0)
        n = len(samples)
        if n == 0:
            return
        price = [s["p"] for s in samples]
        vol = [s["v"] for s in samples]
        times = [s["t"] for s in samples]
        side = [s.get("s", 0) for s in samples]
        # 等距抽樣（線/量）；事件與查價座標按抽樣比例對位
        k = max(1, n // _MAX_PLOT_POINTS)
        idx = list(range(0, n, k))
        xs = list(range(len(idx)))
        p_s = [price[j] for j in idx]
        v_s = [vol[j] for j in idx]
        t_s = [times[j] for j in idx]
        up_s = [side[j] >= 0 for j in idx]
        m = len(xs)
        clr = cs.RED if price[-1] >= base else cs.GREEN

        fig = Figure(figsize=figsize, dpi=_REPLAY_DPI, facecolor=cs.BG,
                     layout="constrained")
        fig.get_layout_engine().set(w_pad=0.01, h_pad=0.01)
        ax = fig.add_subplot(111)
        ax.set_facecolor(cs.BG)
        cs.gradient_fill(ax, xs, p_s, base, clr)
        ax.axhline(base, color=cs.FLAT, lw=1.0, linestyle=(0, (4, 3)),
                   alpha=0.5, zorder=2)
        ax.plot(xs, p_s, color=clr, lw=1.4, solid_capstyle="round", zorder=5)
        ax.scatter([xs[-1]], [p_s[-1]], s=26, color="#e8e8ea", zorder=8,
                   edgecolors=cs.BG, linewidths=1.2)
        ax.annotate(f"{p_s[-1]:,.2f}", (xs[-1], p_s[-1]), xytext=(-6, 9),
                    textcoords="offset points", ha="right", fontsize=9,
                    color="#e8e8ea", fontweight="bold", zorder=8,
                    path_effects=[withStroke(linewidth=2.5, foreground=cs.BG)])
        ev_at: dict[int, list] = {}
        groups: dict[str, tuple[list, list]] = {}
        for ev in events:
            i = ev.get("i")
            if i is None or not (0 <= i < n):
                continue
            gx = i / k
            gxg, gyg = groups.setdefault(ev["type"], ([], []))
            gxg.append(gx)
            gyg.append(price[i])
            ev_at.setdefault(int(round(gx)), []).append(
                _EV_STYLE.get(ev["type"], {}).get("label", ""))
        for et in _EV_ORDER:
            if et in groups:
                gx, gy = groups[et]
                ax.scatter(gx, gy, zorder=9, **_EV_STYLE[et])
        if groups:
            ax.legend(loc="upper right", fontsize=8.5, framealpha=0.0,
                      labelcolor=cs.TXT, handlelength=1.1, borderpad=0.2,
                      ncol=4, columnspacing=1.0)
        cs.volume_overlay(ax, xs, v_s, up_s)
        for sp in ax.spines.values():
            sp.set_visible(False)
        ax.grid(True, axis="y", alpha=0.13, color=cs.GRID, linewidth=0.6)
        ax.tick_params(colors=cs.TXT, labelsize=8.5, length=0)
        ax.xaxis.set_major_locator(
            mticker.MaxNLocator(8, integer=True, prune="both"))
        ax.xaxis.set_major_formatter(mticker.FuncFormatter(
            lambda v, _: t_s[int(v)][:5] if 0 <= int(v) < m else ""))
        ax.set_xlim(-0.6, m - 0.4)
        lo = min(min(p_s), base)
        hi = max(max(p_s), base)
        pad = (hi - lo) * 0.08 or 0.5
        ax.set_ylim(lo - pad, hi + pad)
        vline = ax.axvline(0, color=cs.TXT, lw=0.7, alpha=0.6, zorder=12,
                           visible=False)
        hline = ax.axhline(lo, color=cs.TXT, lw=0.7, alpha=0.6, zorder=12,
                           visible=False)
        ptag = ax.text(1.0, lo, "", transform=ax.get_yaxis_transform(),
                       ha="left", va="center", fontsize=8.5, color="#0d0d0f",
                       zorder=13, visible=False,
                       bbox=dict(boxstyle="round,pad=0.22", fc=cs.TXT, ec="none"))
        info = ax.text(0.014, 0.97, "", transform=ax.transAxes, ha="left",
                       va="top", fontsize=8.5, color=cs.TXT, linespacing=1.5,
                       zorder=13,
                       bbox=dict(boxstyle="round,pad=0.34", fc="#1b1c1f",
                                 ec="#3a3b40", alpha=0.9))
        self._d = {"ax": ax, "vline": vline, "hline": hline, "ptag": ptag,
                   "info": info, "m": m, "t": t_s, "p": p_s, "v": v_s,
                   "base": base, "ev_at": ev_at, "full_xlim": (-0.6, m - 0.4),
                   "full_ylim": (lo - pad, hi + pad), "pan": None}
        info.set_text(self._info(m - 1))

        canvas = FigureCanvasTkAgg(fig, master=parent)
        canvas.draw()
        wdg = canvas.get_tk_widget()
        wdg.configure(bg=cs.BG, highlightthickness=0)
        wdg.pack(fill="both", expand=True)
        canvas.mpl_connect("motion_notify_event", self._on_hover)
        canvas.mpl_connect("scroll_event", self._on_scroll)
        canvas.mpl_connect("button_press_event", self._on_press)
        canvas.mpl_connect("button_release_event", self._on_release)
        self._d["canvas"] = canvas
        self.canvas = canvas

    def _info(self, j: int) -> str:
        d = self._d
        j = max(0, min(d["m"] - 1, j))
        t, p, v, base = d["t"][j], d["p"][j], d["v"][j], d["base"]
        chg = p - base
        pct = (chg / base * 100) if base else 0.0
        sign = "+" if chg > 0 else ""
        txt = (f"{t}\n價 {p:,.2f}　量 {v:,}\n"
               f"漲跌 {sign}{chg:,.2f} ({sign}{pct:.2f}%)")
        evs = d["ev_at"].get(j)
        if evs:
            txt += "\n◆ " + "、".join(dict.fromkeys(evs))
        return txt

    def _autoscale_y(self, x0, x1):
        d = self._d
        p, m = d["p"], d["m"]
        i0 = max(0, int(x0))
        i1 = min(m - 1, int(x1) + 1)
        if i1 < i0:
            return
        seg = p[i0:i1 + 1]
        if not seg:
            return
        lo, hi = min(seg), max(seg)
        pad = (hi - lo) * 0.1 or 0.5
        d["ax"].set_ylim(lo - pad, hi + pad)

    def _on_hover(self, ev):
        d = self._d
        if not d:
            return
        pan = d.get("pan")
        if pan is not None and ev.x is not None:
            ax = d["ax"]
            x0, x1 = pan["xlim0"]
            width_px = ax.get_window_extent().width or 1
            ddata = (ev.x - pan["px"]) * (x1 - x0) / width_px
            nx0, nx1 = x0 - ddata, x1 - ddata
            lo, hi = d["full_xlim"]
            w = nx1 - nx0
            if nx0 < lo:
                nx0, nx1 = lo, lo + w
            if nx1 > hi:
                nx1, nx0 = hi, hi - w
            ax.set_xlim(nx0, nx1)
            self._autoscale_y(nx0, nx1)
            d["canvas"].draw_idle()
            return
        if ev.inaxes is not d["ax"] or ev.xdata is None:
            for a in (d["vline"], d["hline"], d["ptag"]):
                a.set_visible(False)
            d["info"].set_text(self._info(d["m"] - 1))
            d["canvas"].draw_idle()
            return
        j = max(0, min(d["m"] - 1, int(round(ev.xdata))))
        d["vline"].set_xdata([j, j])
        d["vline"].set_visible(True)
        d["hline"].set_ydata([ev.ydata, ev.ydata])
        d["hline"].set_visible(True)
        d["ptag"].set_y(ev.ydata)
        d["ptag"].set_text(f" {ev.ydata:,.2f} ")
        d["ptag"].set_visible(True)
        d["info"].set_text(self._info(j))
        d["canvas"].draw_idle()

    def _on_scroll(self, ev):
        d = self._d
        if not d or ev.inaxes is not d["ax"] or ev.xdata is None:
            return
        ax = d["ax"]
        x0, x1 = ax.get_xlim()
        cur = ev.xdata
        factor = 0.8 if ev.button == "up" else 1.25      # 上滾放大、下滾縮小
        full_lo, full_hi = d["full_xlim"]
        new_w = (x1 - x0) * factor
        new_w = max(5.0, min(new_w, full_hi - full_lo))  # 最小視窗 5 筆
        ratio = (cur - x0) / (x1 - x0) if x1 > x0 else 0.5
        nx0 = cur - ratio * new_w
        nx1 = nx0 + new_w
        if nx0 < full_lo:
            nx0, nx1 = full_lo, full_lo + new_w
        if nx1 > full_hi:
            nx1, nx0 = full_hi, full_hi - new_w
        ax.set_xlim(nx0, nx1)
        self._autoscale_y(nx0, nx1)
        d["canvas"].draw_idle()

    def _on_press(self, ev):
        d = self._d
        if not d or ev.inaxes is not d["ax"]:
            return
        if getattr(ev, "dblclick", False):
            d["pan"] = None
            d["ax"].set_xlim(*d["full_xlim"])
            d["ax"].set_ylim(*d["full_ylim"])
            d["canvas"].draw_idle()
            return
        if ev.button == 1 and ev.x is not None:
            d["pan"] = {"px": ev.x, "xlim0": d["ax"].get_xlim()}

    def _on_release(self, ev):
        if self._d:
            self._d["pan"] = None


class TickMonitorView(ctk.CTkFrame):
    """即時神手 tab page。"""

    def __init__(self, parent, viewmodel: TickMonitorViewModel):
        super().__init__(parent, fg_color="transparent")
        self.vm = viewmodel
        self._row_widgets: list = []
        self._record_map: dict[str, str] = {}     # 下拉顯示字串 → 檔案路徑
        self._build_ui()
        self._bind_vm()
        self._tick_refresh()
        self._live_refresh()

    # ================================================================ UI
    def _build_ui(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=(16, 4))
        ctk.CTkLabel(top, text="即時神手 · 連次連量",
                     font=ctk.CTkFont(size=22, weight="bold")).pack(side="left")

        ctrl = ctk.CTkFrame(self, fg_color="transparent")
        ctrl.pack(fill="x", padx=20, pady=(4, 4))
        ctk.CTkLabel(ctrl, text="監控標的：", font=ctk.CTkFont(size=13)).pack(
            side="left")
        self.entry = ctk.CTkEntry(
            ctrl, width=360, height=32,
            placeholder_text="輸入股票代碼，逗號或空白分隔（例：2330 2317 5483）")
        self.entry.pack(side="left", padx=(0, 8))
        self.start_btn = ctk.CTkButton(
            ctrl, text="開始監控", width=90, height=32, corner_radius=8,
            font=ctk.CTkFont(size=13, weight="bold"), command=self._on_start)
        self.start_btn.pack(side="left", padx=(0, 6))
        self.stop_btn = ctk.CTkButton(
            ctrl, text="停止", width=64, height=32, corner_radius=8,
            fg_color="#c0392b", hover_color="#a93226",
            font=ctk.CTkFont(size=13, weight="bold"), command=self._on_stop)
        self.stop_btn.pack(side="left", padx=(0, 6))
        self.clear_btn = ctk.CTkButton(
            ctrl, text="歸零", width=64, height=32, corner_radius=8,
            fg_color="#555", hover_color="#444",
            font=ctk.CTkFont(size=13), command=self.vm.clear_totals)
        self.clear_btn.pack(side="left")

        self.status_label = ctk.CTkLabel(
            self, text="尚未開始監控", font=ctk.CTkFont(size=12),
            text_color="gray", anchor="w")
        self.status_label.pack(fill="x", padx=22, pady=(0, 8))

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=0, pady=0)

        # ---- 下方：回放江波圖（固定高度，置底）----
        self._build_replay(body)

        # ---- 上方：監控顯示（表格 / 江波圖 可切換）----
        top_area = ctk.CTkFrame(body, fg_color="transparent")
        top_area.pack(side="top", fill="both", expand=True, padx=0, pady=0)
        modebar = ctk.CTkFrame(top_area, fg_color="transparent")
        modebar.pack(fill="x", padx=20, pady=(0, 4))
        ctk.CTkLabel(modebar, text="監控顯示：",
                     font=ctk.CTkFont(size=13)).pack(side="left")
        self.mode_switch = ctk.CTkSegmentedButton(
            modebar, values=["表格", "江波圖"], command=self._on_mode,
            font=ctk.CTkFont(size=13, weight="bold"))
        self.mode_switch.set("表格")
        self.mode_switch.pack(side="left", padx=(4, 0))
        self.chart_legend = ctk.CTkLabel(
            modebar,
            text="　江波圖標記：賣盤竭盡○綠圈、買盤竭盡○紅圈、連買▲、連賣▼",
            font=ctk.CTkFont(size=11), text_color="#8a8a8e")
        self.chart_legend.pack(side="left", padx=(8, 0))

        self.top_container = ctk.CTkFrame(top_area, fg_color="transparent")
        self.top_container.pack(fill="both", expand=True)

        # 表格檢視
        self.table_card = ctk.CTkFrame(self.top_container, corner_radius=12)
        self.table_card.pack(fill="both", expand=True, padx=16, pady=(0, 8))
        header = ctk.CTkFrame(self.table_card, fg_color="transparent")
        header.pack(fill="x", padx=12, pady=(12, 2))
        for i, (_k, title, w, anc, weight) in enumerate(_COLS):
            header.grid_columnconfigure(i, weight=weight, minsize=w)
            ctk.CTkLabel(header, text=title,
                         font=ctk.CTkFont(size=12, weight="bold"),
                         text_color="#8a8a8e",
                         anchor=("center" if anc == "center"
                                 else "e" if anc == "e" else "w")).grid(
                row=0, column=i, sticky="ew", padx=3)
        self.table = ctk.CTkScrollableFrame(self.table_card, fg_color="transparent")
        self.table.pack(fill="both", expand=True, padx=6, pady=(0, 10))
        for i, (_k, _t, w, _a, weight) in enumerate(_COLS):
            self.table.grid_columnconfigure(i, weight=weight, minsize=w)
        ctk.CTkLabel(
            self.table_card,
            text="註：外盤=買方主動成交(紅)、內盤=賣方主動(綠)；連次=連續同向筆數、"
            "連量=該段累計張數，反向即歸零。監控時逐筆與事件自動錄製，停止/收盤存成 JSON。",
            font=ctk.CTkFont(size=11), text_color="#6a6a6a",
            anchor="w", justify="left", wraplength=1100).pack(
            fill="x", padx=12, pady=(0, 8))

        # 江波圖檢視（監控幾檔顯示幾張；初始不 pack，切換時顯示）
        self.live_frame = ctk.CTkScrollableFrame(
            self.top_container, fg_color="transparent")

    # ---- 回放區（江波圖 + 事件標記）----
    def _build_replay(self, parent):
        card = ctk.CTkFrame(parent, corner_radius=12, height=360)
        card.pack(side="bottom", fill="x", padx=16, pady=(0, 12))
        card.pack_propagate(False)
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(top, text="回放 · 江波圖",
                     font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        self.replay_menu = ctk.CTkOptionMenu(
            top, values=["（尚無錄製檔）"], width=320, height=30,
            font=ctk.CTkFont(size=12))
        self.replay_menu.pack(side="left", padx=(12, 6))
        ctk.CTkButton(top, text="載入回放", width=88, height=30, corner_radius=8,
                      font=ctk.CTkFont(size=13, weight="bold"),
                      command=self._on_load_replay).pack(side="left", padx=(0, 6))
        ctk.CTkButton(top, text="🔍 放大", width=80, height=30, corner_radius=8,
                      font=ctk.CTkFont(size=13, weight="bold"),
                      command=self._on_enlarge_replay).pack(side="left", padx=(0, 6))
        ctk.CTkButton(top, text="重新整理", width=80, height=30, corner_radius=8,
                      fg_color="#555", hover_color="#444",
                      font=ctk.CTkFont(size=12),
                      command=self.vm.refresh_records).pack(side="left")
        # 竭盡事件回測（作多/作空）+ 勝率
        btrow = ctk.CTkFrame(card, fg_color="transparent")
        btrow.pack(fill="x", padx=12, pady=(0, 2))
        ctk.CTkLabel(btrow, text="竭盡回測：", font=ctk.CTkFont(size=13)).pack(
            side="left")
        self.bt_dir = ctk.StringVar(value="long")
        ctk.CTkRadioButton(btrow, text="作多", variable=self.bt_dir,
                           value="long", command=self._on_bt_change,
                           radiobutton_width=18, radiobutton_height=18,
                           font=ctk.CTkFont(size=13)).pack(side="left", padx=(0, 10))
        ctk.CTkRadioButton(btrow, text="作空", variable=self.bt_dir,
                           value="short", command=self._on_bt_change,
                           radiobutton_width=18, radiobutton_height=18,
                           font=ctk.CTkFont(size=13)).pack(side="left", padx=(0, 14))
        self.bt_reqvol = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(btrow, text="進場需出量", variable=self.bt_reqvol,
                        command=self._on_bt_change, checkbox_width=18,
                        checkbox_height=18,
                        font=ctk.CTkFont(size=13)).pack(side="left", padx=(0, 8))
        self.bt_volmult = ctk.CTkEntry(btrow, width=48, height=26,
                                       justify="center", font=ctk.CTkFont(size=13))
        self.bt_volmult.insert(0, "3")
        self.bt_volmult.pack(side="left", padx=(0, 2))
        self.bt_volmult.bind("<Return>", lambda e: self._on_bt_change())
        self.bt_volmult.bind("<FocusOut>", lambda e: self._on_bt_change())
        ctk.CTkLabel(btrow, text="倍均量", font=ctk.CTkFont(size=13),
                     text_color="#8a8a8e").pack(side="left", padx=(0, 14))
        self.bt_reqturn = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(btrow, text="出場需轉盤", variable=self.bt_reqturn,
                        command=self._on_bt_change, checkbox_width=18,
                        checkbox_height=18,
                        font=ctk.CTkFont(size=13)).pack(side="left", padx=(0, 14))
        # 結果獨立一列（全寬，避免被截斷）
        self.bt_result = ctk.CTkLabel(
            card, text="（載入回放後計算勝率）", font=ctk.CTkFont(size=13),
            text_color="#8a8a8e", anchor="w", justify="left", wraplength=1120)
        self.bt_result.pack(fill="x", padx=12, pady=(0, 2))
        self.replay_body = ctk.CTkFrame(card, fg_color="transparent")
        self.replay_body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.replay_hint = ctk.CTkLabel(
            self.replay_body,
            text="選擇已錄製的標的後按「載入回放」，畫出當日價量走勢並標記事件"
            "（賣盤竭盡○綠圈、買盤竭盡○紅圈、連買▲、連賣▼）。",
            font=ctk.CTkFont(size=12), text_color="gray")
        self.replay_hint.pack(pady=24)

    # ================================================================ Bindings
    def _bind_vm(self):
        self.vm.bind("rows", lambda v: self.after(0, lambda: self._render(v)))
        self.vm.bind("status", lambda v: self.after(
            0, lambda: self.status_label.configure(text=v or "")))
        self.vm.bind("records", lambda v: self.after(
            0, lambda: self._render_record_list(v)))
        self._render_record_list(self.vm.records)

    def _on_start(self):
        self.vm.start(self.entry.get())

    def _on_stop(self):
        self.vm.stop()
        self.status_label.configure(text="已停止監控")

    def _tick_refresh(self):
        """節流：定時把最新 tick 狀態推到 UI。"""
        try:
            self.vm.refresh()
        except Exception:
            pass
        self.after(_REFRESH_MS, self._tick_refresh)

    def _live_refresh(self):
        """即時江波圖重繪（僅在江波圖檢視時執行，較慢間隔省 CPU）。"""
        try:
            if self.mode_switch.get() == "江波圖":
                self._render_live_charts(self.vm.live_snapshot())
        except Exception:
            pass
        self.after(_LIVE_REFRESH_MS, self._live_refresh)

    def _on_mode(self, value):
        """切換表格 / 江波圖檢視。"""
        if value == "江波圖":
            self.table_card.pack_forget()
            self.live_frame.pack(fill="both", expand=True, padx=16, pady=(0, 8))
            self._render_live_charts(self.vm.live_snapshot())      # 立即繪一次
        else:
            self.live_frame.pack_forget()
            self.table_card.pack(fill="both", expand=True, padx=16, pady=(0, 8))
            self._render(self.vm.rows)                              # 立即重繪表格

    # ================================================================ Render
    def _render(self, rows):
        # 表格隱藏時（江波圖檢視）略過重繪，省資源
        if self.mode_switch.get() != "表格":
            return
        for w in self._row_widgets:
            w.destroy()
        self._row_widgets = []
        if not rows:
            return
        for idx, r in enumerate(rows, 1):
            # 竭盡當次刷新整列變色（買盤竭盡深紅/賣盤竭盡深綠），下次刷新自動恢復
            ex = r.get("exhaust", 0)
            if ex > 0:
                stripe = "#4a1414"
            elif ex < 0:
                stripe = "#0f3a2a"
            else:
                stripe = "#1d1e21" if idx % 2 == 0 else "transparent"
            rowf = ctk.CTkFrame(self.table, fg_color=stripe, corner_radius=4)
            rowf.grid(row=idx, column=0, columnspan=len(_COLS), sticky="ew",
                      pady=1)
            for i, (_k, _t, w, _a, weight) in enumerate(_COLS):
                rowf.grid_columnconfigure(i, weight=weight, minsize=w)
            self._fill_row(rowf, r)
            self._row_widgets.append(rowf)

    def _fill_row(self, rowf, r):
        col = {c[0]: i for i, c in enumerate(_COLS)}

        def lbl(key, text, clr, anc="e", bold=False, size=12):
            ctk.CTkLabel(rowf, text=text, text_color=clr,
                         font=ctk.CTkFont(size=size,
                                          weight="bold" if bold else "normal"),
                         anchor=("center" if anc == "center"
                                 else "e" if anc == "e" else "w")).grid(
                row=0, column=col[key], sticky="ew", padx=3, pady=3)

        chg = r.get("change_pct")
        cclr = cs.RED if (chg or 0) > 0 else cs.GREEN if (chg or 0) < 0 else cs.FLAT
        arrow = "▲" if (chg or 0) > 0 else "▼" if (chg or 0) < 0 else ""
        lbl("code", r["code"], "#d4d4d4", "center")
        lbl("name", r.get("name") or r["code"], "#e6e6e6", "w")
        price = r.get("price") or 0
        lbl("price", f"{arrow}{price:,.2f}" if price else "—", cclr, "e", bold=True)
        lbl("pct", f"{chg:+.2f}%" if chg is not None else "—", cclr, "e")
        # 內外盤（最近一筆方向）
        side = r.get("last_side", 0)
        s_txt, s_clr = _SIDE_STYLE.get(side, ("—", "#8a8a8e"))
        lbl("side", s_txt, s_clr, "center", bold=True)
        # 連次（連買N / 連賣N）；竭盡當次改顯示竭盡字樣
        ex = r.get("exhaust", 0)
        sd = r.get("streak_dir", 0)
        sc = r.get("streak_count", 0)
        if ex > 0:
            lbl("streak", "買盤竭盡", cs.RED, "center", bold=True)
        elif ex < 0:
            lbl("streak", "賣盤竭盡", cs.GREEN, "center", bold=True)
        elif sd == 0 or sc == 0:
            lbl("streak", "—", "#6a6a6a", "center")
        else:
            sclr = cs.RED if sd > 0 else cs.GREEN
            lbl("streak", f"{'連買' if sd > 0 else '連賣'}{sc}", sclr, "center",
                bold=sc >= 3)
        # 連量（帶方向符號）
        sv = r.get("streak_vol", 0)
        if sd == 0 or sv == 0:
            lbl("svol", "—", "#6a6a6a", "e")
        else:
            svclr = cs.RED if sd > 0 else cs.GREEN
            sign = "+" if sd > 0 else "-"
            lbl("svol", f"{sign}{sv:,}", svclr, "e", bold=True)
        # 外/內盤總量
        ov, iv = r.get("outer_vol", 0), r.get("inner_vol", 0)
        cell = ctk.CTkFrame(rowf, fg_color="transparent")
        cell.grid(row=0, column=col["oi"], sticky="ew", padx=3)
        ctk.CTkLabel(cell, text=f"{ov:,}", text_color=cs.RED,
                     font=ctk.CTkFont(size=12)).pack(side="right")
        ctk.CTkLabel(cell, text=" / ", text_color="#6a6a6a",
                     font=ctk.CTkFont(size=12)).pack(side="right")
        ctk.CTkLabel(cell, text=f"{iv:,}", text_color=cs.GREEN,
                     font=ctk.CTkFont(size=12)).pack(side="right")
        # 外盤比
        orat = r.get("outer_ratio", 0.0)
        oclr = cs.RED if orat > 50 else cs.GREEN if orat < 50 else cs.FLAT
        lbl("oratio", f"{orat:.0f}%", oclr, "e", bold=abs(orat - 50) >= 15)
        # 大單（最近一筆大單量，紅=外盤買、綠=內盤賣）
        lb = r.get("last_big", 0)
        bo = r.get("big_orders", 0)
        if lb:
            bclr = cs.RED if side > 0 else cs.GREEN if side < 0 else cs.FLAT
            lbl("big", f"● {lb}", bclr, "center", bold=True)
        elif bo:
            lbl("big", f"{bo}筆", "#8a8a8e", "center")
        else:
            lbl("big", "—", "#6a6a6a", "center")
        lbl("total", f"{r.get('total_vol', 0):,}", "#c7c7cc", "e")

    # ================================================================ Replay
    def _render_record_list(self, records):
        """更新回放下拉清單（顯示：代碼 名稱 · 日期 · 事件數）。"""
        records = records or []
        self._record_map = {}
        values = []
        for r in records:
            disp = (f"{r['code']} {r.get('name') or ''} · {r.get('date', '')}"
                    f" · {r.get('events', 0)}事件").strip()
            # 避免同名衝突
            if disp in self._record_map:
                disp += f" ({len(self._record_map)})"
            self._record_map[disp] = r["path"]
            values.append(disp)
        if values:
            self.replay_menu.configure(values=values)
            if self.replay_menu.get() not in values:
                self.replay_menu.set(values[0])
        else:
            self.replay_menu.configure(values=["（尚無錄製檔）"])
            self.replay_menu.set("（尚無錄製檔）")

    def _on_load_replay(self):
        disp = self.replay_menu.get()
        path = self._record_map.get(disp)
        if not path:
            return
        record = self.vm.load_record(path)
        self._current_record = record      # 供「放大」開大圖用
        self._render_replay(record)
        self._update_backtest()

    def _on_bt_change(self):
        """切換作多/作空 → 重新計算勝率。"""
        self._update_backtest()

    def _update_backtest(self):
        """依目前載入的錄製檔與作多/作空方向，計算竭盡事件回測勝率並顯示。"""
        record = getattr(self, "_current_record", None)
        if not record or not record.get("events"):
            self.bt_result.configure(text="（載入回放後計算勝率）",
                                     text_color="#8a8a8e")
            return
        direction = self.bt_dir.get()
        reqvol = bool(self.bt_reqvol.get())
        try:
            mult = float(self.bt_volmult.get())
            if mult <= 0:
                mult = 3.0
        except (ValueError, TypeError):
            mult = 3.0
        reqturn = bool(self.bt_reqturn.get())
        r = rec.backtest_exhaustion(record, direction, require_volume=reqvol,
                                    vol_mult=mult, require_turn=reqturn)
        vol_tag = f"＋出量(≥{r['vol_mult']:g}×均量)" if reqvol else ""
        turn_tag = ("＋轉內盤確認" if reqturn and direction == "long"
                    else "＋轉外盤確認" if reqturn else "")
        dtxt = ("作多（賣盤竭盡買進→買盤竭盡賣出）" if direction == "long"
                else "作空（買盤竭盡放空→賣盤竭盡回補）") + vol_tag + turn_tag
        if r["count"] == 0:
            self.bt_result.configure(
                text=f"{dtxt}：無完整交易（"
                + ("無出量竭盡進場訊號" if reqvol else "需成對的進出場竭盡事件")
                + "）", text_color="#8a8a8e")
            return
        tclr = (cs.RED if r["total_ret"] > 0
                else cs.GREEN if r["total_ret"] < 0 else "#e6e6e6")
        self.bt_result.configure(
            text=f"{dtxt}　勝率 {r['win_rate']:.1f}%"
            f"（{r['count']}筆：勝{r['wins']}／敗{r['losses']}）"
            f"　總報酬 {r['total_ret']:+.2f}%　平均 {r['avg_ret']:+.2f}%",
            text_color=tclr)

    def _clear_replay(self):
        for w in self.replay_body.winfo_children():
            w.destroy()

    def _jiangbo_figure(self, samples, events, base, w, h,
                        show_legend=False, show_end=True):
        """建江波圖 figure：價格線 + 漸層 + 底部量棒 + 事件標記。回 fig。

        供回放（全圖 + 圖例）與即時（每檔一張）共用。samples/events 同錄製格式；
        超大樣本等距抽樣（線/量），事件座標按抽樣比例對位。
        """
        n = len(samples)
        price = [s["p"] for s in samples]
        vol = [s["v"] for s in samples]
        times = [s["t"] for s in samples]
        base = base or (price[0] if price else 0.0)
        k = max(1, n // _MAX_PLOT_POINTS)
        idx = list(range(0, n, k))
        xs = list(range(len(idx)))
        p_s = [price[j] for j in idx]
        v_s = [vol[j] for j in idx]
        up_s = [samples[j]["s"] >= 0 for j in idx]      # 外盤(紅)/內盤(綠)

        clr = cs.RED if (price[-1] >= base) else cs.GREEN
        fig, ax = cs.new_fig(w, h)
        cs.gradient_fill(ax, xs, p_s, base, clr)
        ax.axhline(base, color=cs.FLAT, linewidth=1.0, linestyle=(0, (4, 3)),
                   alpha=0.5, zorder=2)
        cs.line(ax, xs, p_s, clr, width=1.4, zorder=5)
        cs.volume_overlay(ax, xs, v_s, up_s)
        cs.style_axis(ax, thousands=False)

        # 事件標記（依類型分組，一組一次 scatter → 圖例乾淨）
        groups: dict[str, tuple[list, list]] = {}
        for ev in (events or []):
            i = ev.get("i")
            if i is None or not (0 <= i < n):
                continue
            gx, gy = groups.setdefault(ev["type"], ([], []))
            gx.append(i / k)
            gy.append(price[i])
        has_legend = False
        for et in _EV_ORDER:
            if et not in groups:
                continue
            gx, gy = groups[et]
            ax.scatter(gx, gy, zorder=8, **_EV_STYLE[et])
            has_legend = True
        if show_legend and has_legend:
            ax.legend(loc="upper left", fontsize=13, framealpha=0.0,
                      labelcolor=cs.TXT, handlelength=1.1, borderpad=0.2,
                      ncol=4, columnspacing=1.0)
        if show_end and xs:
            cs.end_marker(ax, xs[-1], p_s[-1], clr, f"{p_s[-1]:,.2f}")

        # x 軸時間刻度
        m = len(xs)
        step = max(m // 8, 1)
        ticks = list(range(0, m, step))
        if ticks and ticks[-1] != m - 1:
            ticks.append(m - 1)
        ax.set_xticks(ticks)
        ax.set_xticklabels([times[idx[t]][:5] for t in ticks])
        ax.set_xlim(-0.6, m - 0.4)
        lo = min(min(p_s), base)
        hi = max(max(p_s), base)
        pad = (hi - lo) * 0.08 or 0.5
        ax.set_ylim(lo - pad, hi + pad)
        return fig

    def _render_replay(self, record):
        """回放：內嵌互動式江波圖（滾輪縮放/拖曳平移/雙擊還原/滑鼠查價）。"""
        self._clear_replay()
        if not HAS_MPL or not _HAS_TKAGG:
            ctk.CTkLabel(self.replay_body, text="（需安裝 matplotlib 才能繪製江波圖）",
                         font=ctk.CTkFont(size=12), text_color="gray").pack(
                pady=24)
            return
        samples = (record or {}).get("samples") or []
        if not samples:
            ctk.CTkLabel(self.replay_body, text="（此錄製檔無逐筆資料）",
                         font=ctk.CTkFont(size=12), text_color="gray").pack(
                pady=24)
            return
        # 互動圖獨立實例（可與放大視窗並存）
        self._replay_chart = JiangboChart(self.replay_body, record)
        ctk.CTkLabel(
            self.replay_body,
            text=f"{record.get('code', '')} {record.get('name', '')} · "
            f"{record.get('date', '')} · {len(samples):,} 筆逐筆 · "
            f"{len(record.get('events') or [])} 事件 · "
            f"{record.get('started_at', '')}–{record.get('ended_at', '')}"
            f"　（滾輪縮放 · 拖曳平移 · 雙擊還原 · 滑鼠查價；按「🔍 放大」開大圖）",
            font=ctk.CTkFont(size=11), text_color="#8a8a8e").pack(pady=(0, 2))

    def _on_enlarge_replay(self):
        """把目前載入的回放江波圖以大視窗開啟（同樣可縮放/平移/查價）。"""
        record = getattr(self, "_current_record", None)
        if not record or not (record.get("samples")):
            # 尚未載入 → 先依下拉選取載入一次
            self._on_load_replay()
            record = getattr(self, "_current_record", None)
        if not record or not record.get("samples"):
            self.status_label.configure(text="請先選擇並「載入回放」再放大")
            return
        if not HAS_MPL or not _HAS_TKAGG:
            return
        top = ctk.CTkToplevel(self)
        top.title(f"江波圖 · {record.get('code', '')} {record.get('name', '')} · "
                  f"{record.get('date', '')}")
        top.geometry("1360x820")
        top.configure(fg_color="#151517")
        top.transient(self.winfo_toplevel())
        top.after(60, top.lift)
        head = ctk.CTkFrame(top, fg_color="#1b1c1f", corner_radius=10)
        head.pack(fill="x", padx=12, pady=(12, 6))
        ctk.CTkLabel(
            head,
            text=f"{record.get('code', '')} {record.get('name', '')} · "
            f"{record.get('date', '')} · {len(record.get('samples') or []):,} 筆 · "
            f"{len(record.get('events') or [])} 事件 · "
            f"{record.get('started_at', '')}–{record.get('ended_at', '')}",
            font=ctk.CTkFont(size=15, weight="bold")).pack(side="left",
                                                           padx=14, pady=8)
        ctk.CTkLabel(head, text="滾輪縮放 · 拖曳平移 · 雙擊還原 · 滑鼠查價",
                     font=ctk.CTkFont(size=12), text_color="#8a8a8e").pack(
            side="right", padx=14)
        body = ctk.CTkFrame(top, fg_color="#1b1c1f", corner_radius=10)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        # 保留參考避免 GC（視窗關閉時一併釋放）
        top._chart = JiangboChart(body, record, figsize=(13.2, 6.6))

    # ================================================================ 即時江波圖
    def _render_live_charts(self, tapes):
        """監控中：每檔一張即時江波圖（依檔數 1~2 欄排版）。"""
        for w in self.live_frame.winfo_children():
            w.destroy()
        if not HAS_MPL:
            ctk.CTkLabel(self.live_frame, text="（需安裝 matplotlib 才能繪製江波圖）",
                         font=ctk.CTkFont(size=12), text_color="gray").pack(
                pady=24)
            return
        tapes = tapes or []
        if not tapes:
            ctk.CTkLabel(
                self.live_frame,
                text="開始監控後，這裡每檔即時顯示一張江波圖（價量走勢 + 事件標記）。",
                font=ctk.CTkFont(size=12), text_color="gray").pack(pady=24)
            return
        cols = 1 if len(tapes) <= 2 else 2
        for c in range(cols):
            self.live_frame.grid_columnconfigure(c, weight=1, uniform="jb")
        for i in range(cols, 3):      # 清除多餘欄權重
            self.live_frame.grid_columnconfigure(i, weight=0, uniform="")
        for idx, tp in enumerate(tapes):
            r, c = divmod(idx, cols)
            cell = ctk.CTkFrame(self.live_frame, corner_radius=10,
                                fg_color="#1b1c1f")
            cell.grid(row=r, column=c, sticky="nsew", padx=6, pady=6)
            self._render_one_live(cell, tp, wide=(cols == 1))

    def _render_one_live(self, parent, tp, wide):
        samples = tp.get("samples") or []
        events = tp.get("events") or []
        base = (tp.get("prev_close") or tp.get("open")
                or (samples[0]["p"] if samples else 0.0))
        last = samples[-1]["p"] if samples else 0.0
        chg = (last - base) if base else 0.0
        pct = (chg / base * 100) if base else 0.0
        cclr = cs.RED if chg > 0 else cs.GREEN if chg < 0 else cs.FLAT
        arrow = "▲" if chg > 0 else "▼" if chg < 0 else ""
        # 標題列：代碼 名稱 + 現價/漲跌 + 筆數/事件
        head = ctk.CTkFrame(parent, fg_color="transparent")
        head.pack(fill="x", padx=12, pady=(8, 0))
        ctk.CTkLabel(head, text=f"{tp.get('code', '')} {tp.get('name', '')}",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        ctk.CTkLabel(head, text=(f"  {arrow}{last:,.2f}  {chg:+.2f} ({pct:+.2f}%)"
                                 if last else "  —"),
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=cclr).pack(side="left")
        ctk.CTkLabel(head, text=f"{len(samples):,}筆 · {len(events)}事件",
                     font=ctk.CTkFont(size=11), text_color="#8a8a8e").pack(
            side="right")
        if not samples:
            ctk.CTkLabel(parent, text="（尚無成交逐筆）",
                         font=ctk.CTkFont(size=12), text_color="gray").pack(
                pady=18)
            return
        w, h = (_LIVE_W_WIDE, _LIVE_H) if wide else (_LIVE_W, _LIVE_H2)
        fig = self._jiangbo_figure(samples, events, base, w, h,
                                   show_legend=False)
        cs.embed(parent, fig, w, h)
