"""高周轉率監控 — 沿用周轉率排行，加主力型態 / 均線斜率 / 布林位階。

全頁表格；風格沿用 Dashboard 深色卡片 + chip 徽章。重運算在 VM 背景執行緒，
callback 以 self.after(0, ...) marshal 回 UI 執行緒。
"""

from __future__ import annotations

import customtkinter as ctk

from viewmodels.turnover_monitor_viewmodel import TurnoverMonitorViewModel
from views import chart_style as cs
from views.monitor_detail_window import MonitorDetailWindow
from services import broker_tags as bt
from services.turnover_monitor_service import MF_SWING, MF_FLIP, MF_MIXED

# 主力型態徽章配色（沿用 broker_tags 色）
_MF_STYLE = {
    MF_SWING: (bt.TAG_COLORS[bt.TAG_SWING], "#10240f"),   # 波段 綠
    MF_FLIP: (bt.TAG_COLORS[bt.TAG_NEXT], "#2a0f0f"),     # 隔日沖 紅
    MF_MIXED: ("#8a8a8e", "#1a1a1c"),                     # 混合 灰
}
_MKT_STYLE = {"上市": ("市", "#3d6fb4"), "上櫃": ("櫃", "#b07a2e")}

# (key, 標題, 寬px, 對齊, 權重)
_COLS = [
    ("nd", "隔日", 44, "center", 0),
    ("rank", "#", 26, "center", 0),
    ("mkt", "", 30, "center", 0),
    ("code", "代碼", 48, "center", 0),
    ("name", "名稱", 60, "w", 1),
    ("close", "收盤", 76, "e", 0),
    ("vol", "量(張)", 62, "e", 0),
    ("turn", "周轉率%", 58, "e", 0),
    ("amp", "5日振幅", 54, "e", 0),
    ("mf", "主力型態", 92, "center", 0),
    ("foreign", "外資淨(張)", 74, "e", 0),
    ("dealer", "自營/避險淨(張)", 100, "e", 0),
    ("mnet", "主力買賣超", 78, "e", 0),
    ("conc", "集中度%", 62, "e", 0),
    ("mcost", "主力均價", 66, "e", 0),
    ("slope", "MA斜率", 60, "e", 0),
    ("bb", "布林位階", 54, "e", 0),
    ("warrant", "權證多空", 78, "center", 0),
    ("volchg", "量增減%", 64, "e", 0),
    ("peer", "同類股漲跌(家數)", 118, "e", 0),
]

# 隔日多空建議 icon（箭頭 + 強度色）：強多深紅、弱多淺紅、弱空淺綠、強空深綠
_ND_STYLE = {
    "強多": ("▲", "#ff2d2d"),
    "弱多": ("▲", "#e08a8a"),
    "中性": ("―", "#8a8a8e"),
    "弱空": ("▼", "#7fcbb8"),
    "強空": ("▼", "#12b48a"),
}

# 權證多空徽章配色（多=紅、空=綠、中性=灰）
_WARRANT_STYLE = {"多": ("#ef5350", "#2a0f0f"),
                  "空": ("#26a69a", "#0f2420"),
                  "中性": ("#8a8a8e", "#1a1a1c")}


class TurnoverMonitorView(ctk.CTkFrame):
    """高周轉率監控 tab page。"""

    def __init__(self, parent, viewmodel: TurnoverMonitorViewModel):
        super().__init__(parent, fg_color="transparent")
        self.vm = viewmodel
        self._row_widgets: list = []
        self._rows: list = []            # 最近一次資料，供切換監控模式重繪
        self._detail_windows: list = []  # 保留彈窗參考避免被 GC
        self._build_ui()
        self._bind_vm()
        self.after(300, self.vm.load)

    # ================================================================ UI
    def _build_ui(self):
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=(16, 4))
        ctk.CTkLabel(top, text="高周轉率監控",
                     font=ctk.CTkFont(size=22, weight="bold")).pack(side="left")
        self.refresh_btn = ctk.CTkButton(
            top, text="刷新", width=72, height=30, corner_radius=8,
            font=ctk.CTkFont(size=13, weight="bold"), command=self.vm.refresh)
        self.refresh_btn.pack(side="right")
        self.monitor_btn = ctk.CTkButton(
            top, text="監控", width=72, height=30, corner_radius=8,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self.vm.toggle_monitor)
        self.monitor_btn.pack(side="right", padx=(0, 8))
        self._btn_fg = self.monitor_btn.cget("fg_color")
        self._btn_hover = self.monitor_btn.cget("hover_color")

        self.status_label = ctk.CTkLabel(
            self, text="尚未載入", font=ctk.CTkFont(size=12), text_color="gray",
            anchor="w", justify="left")
        self.status_label.pack(fill="x", padx=22, pady=(0, 8))

        card = ctk.CTkFrame(self, corner_radius=12)
        card.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        # 表頭（固定於卡片頂）
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
        self.table.pack(fill="both", expand=True, padx=6, pady=(0, 10))
        for i, (_k, _t, w, _a, weight) in enumerate(_COLS):
            self.table.grid_columnconfigure(i, weight=weight, minsize=w)

    # ================================================================ Bindings
    def _bind_vm(self):
        self.vm.bind("monitor_rows", self._on_rows)
        self.vm.bind("monitor_status", self._on_status)
        self.vm.bind("is_loading", self._on_loading)
        self.vm.bind("monitor_mode", self._on_monitor_mode)

    def _on_monitor_mode(self, on):
        def _u():
            if on:
                self.monitor_btn.configure(text="監控中", fg_color="#c0392b",
                                           hover_color="#a93226")
            else:
                self.monitor_btn.configure(text="監控", fg_color=self._btn_fg,
                                           hover_color=self._btn_hover)
            self._render(self._rows)   # 重繪以套用/移除可點擊
        self.after(0, _u)

    def _on_status(self, v):
        self.after(0, lambda: self.status_label.configure(text=v or ""))

    def _on_loading(self, v):
        def _u():
            if v:
                self.refresh_btn.configure(state="disabled", text="載入中...")
            else:
                self.refresh_btn.configure(state="normal", text="刷新")
        self.after(0, _u)

    def _on_rows(self, rows):
        self.after(0, lambda: self._render(rows))

    # ================================================================ Render
    def _render(self, rows):
        for w in self._row_widgets:
            w.destroy()
        self._row_widgets = []
        self._rows = rows or []
        if not rows:
            return
        for idx, r in enumerate(rows, 1):
            stripe = "#1d1e21" if idx % 2 == 0 else "transparent"
            rowf = ctk.CTkFrame(self.table, fg_color=stripe, corner_radius=4)
            rowf.grid(row=idx, column=0, columnspan=len(_COLS), sticky="ew",
                      pady=1)
            for i, (_k, _t, w, _a, weight) in enumerate(_COLS):
                rowf.grid_columnconfigure(i, weight=weight, minsize=w)
            self._fill_row(rowf, idx, r)
            # 任何狀態皆可點列開監控彈窗（監控模式僅作視覺標示）
            self._bind_row_click(rowf, r)
            self._row_widgets.append(rowf)

    def _bind_row_click(self, rowf, r):
        """監控模式下：整列（含子元件）可點擊 → 開監控彈窗，游標改手形。"""
        def _open(_e=None):
            self._open_detail(r)
        rowf.configure(cursor="hand2")
        rowf.bind("<Button-1>", _open)
        for child in rowf.winfo_children():
            try:
                child.configure(cursor="hand2")
            except Exception:
                pass
            child.bind("<Button-1>", _open)

    def _open_detail(self, r):
        try:
            win = MonitorDetailWindow(
                self.winfo_toplevel(),
                stock_code=r["stock_code"], stock_name=r.get("stock_name", ""),
                market=r.get("market", ""), date=self.vm.data_date,
                ctx=self.vm.ctx, shioaji_svc=self.vm._sj)
            self._detail_windows = [w for w in self._detail_windows
                                    if w.winfo_exists()]
            self._detail_windows.append(win)
        except Exception as e:  # noqa: BLE001
            self.status_label.configure(text=f"開啟監控視窗失敗：{e}")

    def _fill_row(self, rowf, idx, r):
        col = {c[0]: i for i, c in enumerate(_COLS)}

        def lbl(key, text, clr, anc="e", bold=False, size=12):
            ctk.CTkLabel(
                rowf, text=text, text_color=clr,
                font=ctk.CTkFont(size=size, weight="bold" if bold else "normal"),
                anchor=("center" if anc == "center"
                        else "e" if anc == "e" else "w")).grid(
                row=0, column=col[key], sticky="ew", padx=3, pady=2)

        # 隔日多空建議：箭頭 + 分數（強度色）直接標在同一格
        nd = r.get("nd_bias")
        nds = _ND_STYLE.get(nd)
        if nds:
            sc = r.get("nd_score")
            txt = f"{nds[0]}{sc:+d}" if sc is not None else nds[0]
            lbl("nd", txt, nds[1], "center", bold=True)
        else:
            lbl("nd", "", "#6a6a6a", "center")
        lbl("rank", str(idx), "#7a7a7e", "center")
        # 市場徽章
        mkt = r.get("market", "")
        m = _MKT_STYLE.get(mkt)
        if m:
            ctk.CTkLabel(rowf, text=m[0], width=20, height=18, corner_radius=4,
                         fg_color=m[1], text_color="#ffffff",
                         font=ctk.CTkFont(size=10, weight="bold")).grid(
                row=0, column=col["mkt"], padx=3, pady=2)
        lbl("code", r["stock_code"], "#d4d4d4", "center")
        lbl("name", r["stock_name"], "#e6e6e6", "w")
        # 收盤（漲跌符號+色）
        chg = r.get("change")
        if chg is None or chg == 0:
            cclr, arrow = cs.FLAT, ""
        elif chg > 0:
            cclr, arrow = cs.RED, "▲"
        else:
            cclr, arrow = cs.GREEN, "▼"
        lbl("close", f"{arrow}{r['close']:,.2f}", cclr, "e", bold=True)
        lbl("vol", f"{r['volume_lots']:,}", "#c7c7cc", "e")
        turn = r["turnover_pct"]
        lbl("turn", f"{turn:.2f}", cs.RED if turn >= 20 else cs.TXT, "e",
            bold=True)
        amp = r.get("amp5")
        lbl("amp", f"{amp:.2f}" if amp is not None else "—", "#b0a17a", "e")
        # 主力型態徽章（含隔日沖買超佔比%）
        mf = r.get("main_force")
        style = _MF_STYLE.get(mf)
        if style:
            fr = r.get("flip_ratio")
            txt = f"{mf} {fr * 100:.0f}%" if fr is not None else mf
            ctk.CTkLabel(rowf, text=txt, height=20, corner_radius=6,
                         fg_color=style[0], text_color=style[1],
                         font=ctk.CTkFont(size=11, weight="bold")).grid(
                row=0, column=col["mf"], sticky="ew", padx=6, pady=2)
        else:
            lbl("mf", "—", "#6a6a6a", "center")
        # 外資：買賣超淨額（張，正買超紅／負賣超綠）
        fn = r.get("foreign_lots")
        if fn is None:
            lbl("foreign", "—", "#6a6a6a", "e")
        else:
            fclr = cs.RED if fn > 0 else cs.GREEN if fn < 0 else cs.FLAT
            lbl("foreign", f"{fn:+,}", fclr, "e")
        # 自營/避險：自營商自行 / 避險 買賣超淨額（張，正買超負賣超）
        sn = r.get("dealer_self_lots")
        hn = r.get("dealer_hedge_lots")
        if sn is None and hn is None:
            lbl("dealer", "—", "#6a6a6a", "e")
        else:
            hv = hn or 0
            dclr = cs.RED if hv > 0 else cs.GREEN if hv < 0 else cs.FLAT
            lbl("dealer", f"{sn or 0:+,}/{hn or 0:+,}", dclr, "e")
        # MA 斜率
        sl = r.get("ma_slope")
        if sl is None:
            lbl("slope", "—", "#6a6a6a", "e")
        else:
            sclr = cs.RED if sl > 0 else cs.GREEN if sl < 0 else cs.FLAT
            lbl("slope", f"{sl:+.2f}", sclr, "e")
        # 主力買賣超（前15家淨，張）：正買超紅、負賣超綠
        mn = r.get("main_net_lots")
        if mn is None:
            lbl("mnet", "—", "#6a6a6a", "e")
        else:
            mclr = cs.RED if mn > 0 else cs.GREEN if mn < 0 else cs.FLAT
            lbl("mnet", f"{mn:+,}", mclr, "e", bold=abs(mn) >= 1000)
        # 主力集中度%：正=籌碼集中買方紅、負=集中賣方綠
        cc = r.get("concentration")
        if cc is None:
            lbl("conc", "—", "#6a6a6a", "e")
        else:
            cclr2 = cs.RED if cc > 0 else cs.GREEN if cc < 0 else cs.FLAT
            lbl("conc", f"{cc:+.2f}", cclr2, "e", bold=abs(cc) >= 10)
        # 主力均價：集中度負(賣超為主)→賣均價(綠)；否則→買均價(紅)
        cc2 = r.get("concentration")
        if cc2 is not None and cc2 < 0:
            mc = r.get("main_sell_cost")
            mcclr = cs.GREEN
        else:
            mc = r.get("main_buy_cost")
            mcclr = cs.RED
        if mc is None:
            lbl("mcost", "—", "#6a6a6a", "e")
        else:
            lbl("mcost", f"{mc:,.2f}", mcclr, "e")
        # 布林位階
        bb = r.get("bb_pos")
        if bb is None:
            lbl("bb", "—", "#6a6a6a", "e")
        else:
            bclr = (cs.RED if bb >= 8 else cs.GREEN if bb <= -8 else cs.TXT)
            lbl("bb", f"{bb:+.0f}", bclr, "e", bold=abs(bb) >= 8)
        # 權證多空（徽章 + 多方佔比%）
        wb = r.get("warrant_bias")
        ws = _WARRANT_STYLE.get(wb)
        if ws:
            share = r.get("warrant_long_share")
            txt = f"{wb} {share:.0f}%" if share is not None else wb
            ctk.CTkLabel(rowf, text=txt, height=20, corner_radius=6,
                         fg_color=ws[0], text_color=ws[1],
                         font=ctk.CTkFont(size=11, weight="bold")).grid(
                row=0, column=col["warrant"], sticky="ew", padx=6, pady=2)
        else:
            lbl("warrant", "—", "#6a6a6a", "center")
        # 量增減%：當日成交量較前一交易日（正=量增紅、負=量縮綠）
        vc = r.get("vol_chg_pct")
        if vc is None:
            lbl("volchg", "—", "#6a6a6a", "e")
        else:
            vclr = cs.RED if vc > 0 else cs.GREEN if vc < 0 else cs.FLAT
            lbl("volchg", f"{vc:+.0f}%", vclr, "e", bold=abs(vc) >= 50)
        # 同類股漲跌（合併）：平均漲跌幅% + (上漲家數/總家數)
        av = r.get("peer_avg_chg")
        up = r.get("peer_up")
        total = r.get("peer_total")
        if av is None and (up is None or total is None):
            lbl("peer", "—", "#6a6a6a", "e")
        else:
            aclr = (cs.RED if (av or 0) > 0 else cs.GREEN if (av or 0) < 0
                    else cs.FLAT)
            avs = f"{av:+.2f}%" if av is not None else "—"
            cnt = f"({up}/{total})" if up is not None and total is not None \
                else ""
            lbl("peer", f"{avs} {cnt}".strip(), aclr, "e")
