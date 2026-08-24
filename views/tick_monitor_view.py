"""即時神手（連次連量多檔監控）分頁。

輸入股票代碼 → 開始監控 → 表格即時顯示各檔連次連量、內外盤、大單。
需正式環境登入永豐才有即時逐筆。UI 更新一律 self.after marshal；表格以
定時 refresh 節流刷新（tick 高頻，不逐筆重繪）。
"""

from __future__ import annotations

import customtkinter as ctk

from viewmodels.tick_monitor_viewmodel import TickMonitorViewModel
from views import chart_style as cs

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


class TickMonitorView(ctk.CTkFrame):
    """即時神手 tab page。"""

    def __init__(self, parent, viewmodel: TickMonitorViewModel):
        super().__init__(parent, fg_color="transparent")
        self.vm = viewmodel
        self._row_widgets: list = []
        self._build_ui()
        self._bind_vm()
        self._tick_refresh()

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

        card = ctk.CTkFrame(self, corner_radius=12)
        card.pack(fill="both", expand=True, padx=16, pady=(0, 16))
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

        ctk.CTkLabel(
            self, text="註：外盤=買方主動成交(紅)、內盤=賣方主動(綠)；連次=連續同向筆數、"
            "連量=該段累計張數，反向即歸零。需登入永豐正式環境且盤中才有即時逐筆。",
            font=ctk.CTkFont(size=11), text_color="#6a6a6a",
            anchor="w", justify="left", wraplength=1100).pack(
            fill="x", padx=22, pady=(0, 8))

    # ================================================================ Bindings
    def _bind_vm(self):
        self.vm.bind("rows", lambda v: self.after(0, lambda: self._render(v)))
        self.vm.bind("status", lambda v: self.after(
            0, lambda: self.status_label.configure(text=v or "")))

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
        # 連次（連買N / 連賣N）
        sd = r.get("streak_dir", 0)
        sc = r.get("streak_count", 0)
        if sd == 0 or sc == 0:
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
