"""高周轉率監控 — 沿用周轉率排行，加主力型態 / 均線斜率 / 布林位階。

全頁表格；風格沿用 Dashboard 深色卡片 + chip 徽章。重運算在 VM 背景執行緒，
callback 以 self.after(0, ...) marshal 回 UI 執行緒。
"""

from __future__ import annotations

import customtkinter as ctk

from viewmodels.turnover_monitor_viewmodel import TurnoverMonitorViewModel
from views import chart_style as cs
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
    ("rank", "#", 26, "center", 0),
    ("mkt", "", 30, "center", 0),
    ("code", "代碼", 48, "center", 0),
    ("name", "名稱", 60, "w", 1),
    ("close", "收盤", 76, "e", 0),
    ("vol", "量(張)", 62, "e", 0),
    ("turn", "周轉率%", 58, "e", 0),
    ("amp", "5日振幅", 54, "e", 0),
    ("mf", "主力型態", 70, "center", 0),
    ("slope", "MA斜率", 60, "e", 0),
    ("bb", "布林位階", 54, "e", 0),
    ("peeravg", "同類股漲跌", 78, "e", 0),
    ("peerratio", "同類股漲跌比例", 92, "e", 0),
]


class TurnoverMonitorView(ctk.CTkFrame):
    """高周轉率監控 tab page。"""

    def __init__(self, parent, viewmodel: TurnoverMonitorViewModel):
        super().__init__(parent, fg_color="transparent")
        self.vm = viewmodel
        self._row_widgets: list = []
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
            self._row_widgets.append(rowf)

    def _fill_row(self, rowf, idx, r):
        col = {c[0]: i for i, c in enumerate(_COLS)}

        def lbl(key, text, clr, anc="e", bold=False, size=12):
            ctk.CTkLabel(
                rowf, text=text, text_color=clr,
                font=ctk.CTkFont(size=size, weight="bold" if bold else "normal"),
                anchor=("center" if anc == "center"
                        else "e" if anc == "e" else "w")).grid(
                row=0, column=col[key], sticky="ew", padx=3, pady=2)

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
        # 主力型態徽章
        mf = r.get("main_force")
        style = _MF_STYLE.get(mf)
        if style:
            ctk.CTkLabel(rowf, text=mf, height=20, corner_radius=6,
                         fg_color=style[0], text_color=style[1],
                         font=ctk.CTkFont(size=11, weight="bold")).grid(
                row=0, column=col["mf"], sticky="ew", padx=6, pady=2)
        else:
            lbl("mf", "—", "#6a6a6a", "center")
        # MA 斜率
        sl = r.get("ma_slope")
        if sl is None:
            lbl("slope", "—", "#6a6a6a", "e")
        else:
            sclr = cs.RED if sl > 0 else cs.GREEN if sl < 0 else cs.FLAT
            lbl("slope", f"{sl:+.2f}", sclr, "e")
        # 布林位階
        bb = r.get("bb_pos")
        if bb is None:
            lbl("bb", "—", "#6a6a6a", "e")
        else:
            bclr = (cs.RED if bb >= 8 else cs.GREEN if bb <= -8 else cs.TXT)
            lbl("bb", f"{bb:+.0f}", bclr, "e", bold=abs(bb) >= 8)
        # 同類股平均漲跌幅%
        av = r.get("peer_avg_chg")
        if av is None:
            lbl("peeravg", "—", "#6a6a6a", "e")
        else:
            aclr = cs.RED if av > 0 else cs.GREEN if av < 0 else cs.FLAT
            lbl("peeravg", f"{av:+.2f}%", aclr, "e")
        # 同類股漲跌比例：上漲家數/總家數（紅=漲方多、綠=跌方多）
        up = r.get("peer_up")
        down = r.get("peer_down")
        total = r.get("peer_total")
        if up is None or total is None:
            lbl("peerratio", "—", "#6a6a6a", "e")
        else:
            rclr = cs.RED if up > down else cs.GREEN if up < down else cs.FLAT
            lbl("peerratio", f"{up}/{total}", rclr, "e")
