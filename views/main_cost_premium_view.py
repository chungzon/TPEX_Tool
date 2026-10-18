"""主力追價 — 近5日主力買入均價高於5日均價的標的。

版面、篩選、搜尋、監控彈窗全部沿用 TurnoverMonitorView，只換欄位：
拿掉「5日振幅」，在量之後插入 5日主力買均 / 5日均價 / 溢價% / 5日主力淨 / 分點日；
「MA斜率」與「布林位階」合併成單一欄「MA斜率/布林」；隱藏「隔日」欄；
代碼與名稱合併為一欄（上代碼、下名稱）；收盤附漲跌幅、短沖/隔日沖同格兩行；
溢價%附迷你橫條、分點日以 5 顆點呈現；表頭置中色帶、核心指標標題強調色、列 hover；資料列加高，葛蘭碧、MA斜率/布林、自營/避險、同類股漲跌皆分兩行以縮窄欄寬。
"""

from __future__ import annotations

import customtkinter as ctk

from views import chart_style as cs
from views.turnover_monitor_view import _COLS, TurnoverMonitorView

_EXTRA = [
    ("mc5", "5日\n主力買均", 64, "e", 0),
    ("vwap5", "5日\n均價", 64, "e", 0),
    ("prem", "溢價%", 58, "e", 0),
    ("mn5", "5日主力\n淨(張)", 58, "e", 0),
    ("bdays", "分點\n日", 46, "center", 0),
]


_TECH = ("tech", "MA斜率\n/布林", 54, "e", 0)

# 表頭換行 + 縮窄欄寬（key → (標題, 寬px)）。寬度以內容實際所需為下限，
# 再窄會讓資料列撐開欄位、與表頭錯位。
_HEAD = {
    "vol": ("量(張)", 56),
    "turn": ("周轉\n率%", 46),
    "mf": ("短沖/隔日沖\n買入%", 76),
    "gran": ("葛蘭碧", 80),
    "foreign": ("外資淨\n(張)", 60),
    "dealer": ("自營/避險\n淨(張)", 62),
    "mnet": ("主力\n買賣超", 58),
    "conc": ("集中\n度%", 52),
    "mcost": ("主力\n均價", 62),
    "volchg": ("量增\n減%", 50),
    "peer": ("同類股\n漲跌(家數)", 70),
}


# 代碼 + 名稱合併為一欄（上代碼、下名稱），沿用原「名稱」欄的伸縮權重
_STOCK = ("stock", "代碼\n名稱", 66, "w", 1)


def _build_cols():
    # 合併欄放在原「MA斜率」/「代碼」的位置
    cols = [_TECH if c[0] == "slope" else _STOCK if c[0] == "code" else c
            for c in _COLS if c[0] not in ("nd", "amp", "bb", "name")]
    cols = [(k, *_HEAD[k], a, wt) if k in _HEAD else (k, t, w, a, wt)
            for k, t, w, a, wt in cols]
    at = next(i for i, c in enumerate(cols) if c[0] == "vol") + 1
    return cols[:at] + _EXTRA + cols[at:]


class MainCostPremiumView(TurnoverMonitorView):
    """主力追價 tab page。"""

    TITLE = "主力追價"
    COLS = _build_cols()
    GRAN_WRAP = True
    GRAN_BADGE_H = 34
    ROW_MIN_H = 40
    CELL_WRAP = True
    ALIGN = "e"            # 水平靠右（垂直置中由列高 + sticky="ew" 自然達成）
    ALIGN_KEEP = ("stock",)  # 代碼/名稱欄靠左
    HEADER_ALIGN = "center"  # 表頭一律置中
    HEADER_BG = "#202126"
    HEADER_ACCENT = ("mc5", "vwap5", "prem", "mn5", "bdays")  # 本頁核心指標
    ROW_HOVER = "#2b2d33"

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
        self._fill_tech(rowf, col, r)
        self._stack(rowf, col["stock"],
                    [(r["stock_code"], "#d4d4d4"),
                     (r.get("stock_name", ""), "#e6e6e6", True)],
                    anchor="w")
        self._fill_bdays(rowf, col, r)

    def _fill_prem(self, rowf, col, r):
        """溢價%：上數值、下迷你橫條（以本表最大溢價為滿格）。"""
        pm = r.get("premium_pct")
        if pm is None:
            self._stack(rowf, col["prem"], [("—", "#6a6a6a")])
            return
        pclr = cs.RED if pm > 0 else cs.GREEN if pm < 0 else cs.FLAT
        cell = ctk.CTkFrame(rowf, fg_color="transparent")
        cell.grid(row=0, column=col["prem"], sticky="ew", padx=3, pady=2)
        ctk.CTkLabel(cell, text=f"{pm:+.2f}", text_color=pclr, height=18,
                     anchor="e",
                     font=ctk.CTkFont(size=12, weight="bold")).pack(
            side="top", fill="x")
        # width 只是請求寬（CTkFrame 預設 200 會撐開欄位），實際由 fill="x" 撐滿
        track = ctk.CTkFrame(cell, fg_color="#2e3036", height=4, width=40,
                             corner_radius=2)
        track.pack(side="top", fill="x", pady=(2, 0))
        frac = max(0.04, min(1.0, abs(pm) / (self._prem_max or 1.0)))
        # 由右往左長（與靠右對齊的數字同側）
        ctk.CTkFrame(track, fg_color=pclr, height=4, corner_radius=2).place(
            relx=1.0, rely=0, anchor="ne", relwidth=frac, relheight=1.0)

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

    def _fill_tech(self, rowf, col, r):
        """MA斜率 / 布林位階 同格兩行（上斜率、下布林），上色規則同高周轉率監控。"""
        sl = r.get("ma_slope")
        bb = r.get("bb_pos")
        stale = r.get("price_stale")
        star = "*" if stale else ""    # 價格資料過期：暗色＋*
        if sl is None:
            st = ("—", "#6a6a6a")
        elif stale:
            st = (f"{sl:+.2f}{star}", "#6a6a6a")
        else:
            st = (f"{sl:+.2f}",
                  cs.RED if sl > 0 else cs.GREEN if sl < 0 else cs.FLAT)
        if bb is None:
            bt = ("—", "#6a6a6a")
        elif stale:
            bt = (f"{bb:+.0f}{star}", "#6a6a6a")
        else:
            bt = (f"{bb:+.0f}",
                  cs.RED if bb >= 8 else cs.GREEN if bb <= -8 else cs.TXT,
                  abs(bb) >= 8)
        self._stack(rowf, col["tech"], [st, bt])
