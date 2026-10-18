"""主力追價 — 近5日主力買入均價高於5日均價的標的。

版面、篩選、搜尋、監控彈窗與精簡表格樣式全部沿用 TurnoverMonitorView，只換欄位：
隱藏「隔日」「5日振幅」，在量之後插入 5日主力買均 / 5日均價 / 溢價% / 5日主力淨 /
分點日；溢價%附迷你橫條、分點日以 5 顆點呈現。
"""

from __future__ import annotations

import customtkinter as ctk

from views import chart_style as cs
from views.turnover_monitor_view import TurnoverMonitorView, styled_cols

_EXTRA = [
    ("mc5", "5日\n主力買均", 64, "e", 0),
    ("vwap5", "5日\n均價", 64, "e", 0),
    ("prem", "溢價%", 58, "e", 0),
    ("mn5", "5日主力\n淨(張)", 58, "e", 0),
    ("bdays", "分點\n日", 46, "center", 0),
]



def _build_cols():
    # 精簡版面同高周轉率監控；另隱藏「隔日」「5日振幅」，在量之後插入本頁欄位
    cols = styled_cols(drop=("nd", "amp"))
    at = next(i for i, c in enumerate(cols) if c[0] == "vol") + 1
    return cols[:at] + _EXTRA + cols[at:]


class MainCostPremiumView(TurnoverMonitorView):
    """主力追價 tab page。"""

    TITLE = "主力追價"
    COLS = _build_cols()
    HEADER_ACCENT = ("mc5", "vwap5", "prem", "mn5", "bdays")  # 本頁核心指標
    TURN_BAR = False       # 本頁以溢價% 橫條為主，周轉率不再畫

    def _render(self, rows):
        # 溢價橫條以本次表格最大溢價為滿格，先算好再畫
        vals = [r.get("premium_pct") or 0 for r in (rows or [])]
        self._prem_max = max([v for v in vals if v > 0] or [1.0])
        super()._render(rows)

    def _fill_extra(self, rowf, col, r):
        def lbl(key, text, clr, anc="e", bold=False):
            if self.ALIGN:
                anc = self.ALIGN
            ctk.CTkLabel(
                rowf, text=text, text_color=clr,
                font=ctk.CTkFont(size=12, weight="bold" if bold else "normal"),
                anchor=("center" if anc == "center"
                        else "e" if anc == "e" else "w")).grid(
                row=0, column=col[key], sticky="ew", padx=3, pady=2)

        mc = r.get("main_cost5")
        lbl("mc5", f"{mc:,.2f}" if mc is not None else "—",
            cs.RED if mc is not None else "#6a6a6a", "e", bold=True)
        vw = r.get("vwap5")
        lbl("vwap5", f"{vw:,.2f}" if vw is not None else "—", "#c7c7cc", "e")
        self._fill_prem(rowf, col, r)
        mn = r.get("main_net5_lots")
        if mn is None:
            lbl("mn5", "—", "#6a6a6a", "e")
        else:
            lbl("mn5", f"{mn:+,}", cs.RED if mn > 0 else cs.FLAT, "e",
                bold=mn >= 1000)
        self._fill_bdays(rowf, col, r)

    def _fill_prem(self, rowf, col, r):
        """溢價%：上數值、下迷你橫條（以本表最大溢價為滿格）。"""
        pm = r.get("premium_pct")
        if pm is None:
            self._stack(rowf, col["prem"], [("—", "#6a6a6a")])
            return
        pclr = cs.RED if pm > 0 else cs.GREEN if pm < 0 else cs.FLAT
        self._fill_bar(rowf, col["prem"], f"{pm:+.2f}", pm, self._prem_max,
                       pclr)

    def _fill_bdays(self, rowf, col, r):
        """分點日：5 顆點，實心＝該日有分點資料；不足 5 日以琥珀色提醒
        （爬蟲漏抓，均價只以有資料的日子計）。"""
        bd = r.get("broker_days")
        if bd is None:
            self._stack(rowf, col["bdays"], [("—", "#6a6a6a")],
                        anchor="center")
            return
        bd = max(0, min(5, bd))
        full = bd == 5
        on = "#6f8f86" if full else cs.AMBER
        cell = ctk.CTkFrame(rowf, fg_color="transparent")
        cell.grid(row=0, column=col["bdays"], padx=3, pady=2)
        dots = ctk.CTkFrame(cell, fg_color="transparent")
        dots.pack(side="top")
        for i in range(5):
            ctk.CTkFrame(dots, width=5, height=5, corner_radius=3,
                         fg_color=on if i < bd else "#3a3c42").pack(
                side="left", padx=1)
        ctk.CTkLabel(cell, text=f"{bd}/5", height=14,
                     text_color="#7a7a7e" if full else cs.AMBER,
                     font=ctk.CTkFont(size=10,
                                      weight="normal" if full else "bold")
                     ).pack(side="top", pady=(2, 0))
