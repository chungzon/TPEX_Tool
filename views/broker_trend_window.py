"""分點買賣超趨勢視窗 — 單一分點 × 單一個股的日線（CTkToplevel）。

由監控彈窗「買賣超前15名」點列開啟。版面採一般看盤軟體的上下分割：
- 上圖：股價日 K（紅漲綠跌）+ 累計買賣超線（右軸），看分點進出與股價的關係。
- 下圖：每日買賣超長條（買超紅、賣超綠），即一般看盤軟體的量棒表示法。
兩圖共用同一條 x 軸（日期索引），縮放/對齊一致。

股價缺漏日（StockDailySummary 的 OHLC 為空）不畫 K 線，但量棒照畫。
"""

from __future__ import annotations

import customtkinter as ctk

from viewmodels.broker_trend_viewmodel import BrokerTrendViewModel
from views import chart_style as cs
from views.chart_style import HAS_MPL

_W, _H = 10.4, 6.4          # 圖的邏輯英吋
_MAX_BARS = 120             # 最多顯示根數（太多會擠成一片）
_CUM_CLR = cs.AMBER         # 累計買賣超線


class BrokerTrendWindow(ctk.CTkToplevel):
    """單一分點買賣超日線趨勢彈窗。"""

    def __init__(self, master, stock_code: str, stock_name: str,
                 broker_code: str, broker_name: str, start: str, end: str):
        super().__init__(master)
        self.vm = BrokerTrendViewModel(stock_code, stock_name, broker_code,
                                       broker_name, start, end)
        self.title(f"分點買賣超 · {broker_name} · {stock_code} {stock_name}")
        self.geometry("1120x780")
        self.configure(fg_color="#151517")
        self.transient(master)
        self.after(50, self.lift)

        self._build_ui()
        self.vm.bind("series",
                     lambda v: self.after(0, lambda: self._render(v)))
        self.vm.bind("status",
                     lambda v: self.after(0, lambda: self.status.configure(
                         text=v or "")))
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(150, self.vm.load)

    # ================================================================ UI
    def _build_ui(self):
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=16, pady=(14, 4))
        ctk.CTkLabel(head, text=self.vm.broker_name,
                     font=ctk.CTkFont(size=19, weight="bold")).pack(side="left")
        ctk.CTkLabel(head,
                     text=f"  {self.vm.code} {self.vm.stock_name}",
                     font=ctk.CTkFont(size=14),
                     text_color="#9a9a9e").pack(side="left")

        # 區間調整（與監控彈窗同一套輸入慣例：Enter 或按鈕套用）
        rng = ctk.CTkFrame(head, fg_color="transparent")
        rng.pack(side="right")
        ctk.CTkLabel(rng, text="區間", font=ctk.CTkFont(size=12),
                     text_color="#8a8a8e").pack(side="left", padx=(0, 6))
        self.e_start = ctk.CTkEntry(rng, width=104, height=26,
                                    justify="center",
                                    font=ctk.CTkFont(size=12))
        self.e_start.insert(0, self.vm.start)
        self.e_start.pack(side="left")
        ctk.CTkLabel(rng, text="~", font=ctk.CTkFont(size=12),
                     text_color="#8a8a8e").pack(side="left", padx=4)
        self.e_end = ctk.CTkEntry(rng, width=104, height=26, justify="center",
                                  font=ctk.CTkFont(size=12))
        self.e_end.insert(0, self.vm.end)
        self.e_end.pack(side="left")
        ctk.CTkButton(rng, text="套用", width=56, height=26, corner_radius=6,
                      font=ctk.CTkFont(size=12, weight="bold"),
                      command=self._apply_range).pack(side="left", padx=(6, 0))
        for e in (self.e_start, self.e_end):
            e.bind("<Return>", lambda _e: self._apply_range())

        self.status = ctk.CTkLabel(self, text="載入中…",
                                   font=ctk.CTkFont(size=11),
                                   text_color="gray", anchor="w",
                                   justify="left", wraplength=1060)
        self.status.pack(fill="x", padx=18, pady=(0, 6))

        card = ctk.CTkFrame(self, corner_radius=12, fg_color="#1b1c1f")
        card.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.body = ctk.CTkFrame(card, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=8, pady=8)

    def _apply_range(self):
        self.vm.set_range(self.e_start.get().strip(), self.e_end.get().strip())

    # ================================================================ Render
    def _render(self, s):
        for w in self.body.winfo_children():
            w.destroy()
        if not s:
            return
        if s.get("error"):
            ctk.CTkLabel(self.body, text=f"（{s['error']}）",
                         font=ctk.CTkFont(size=13),
                         text_color="gray").pack(pady=40)
            return
        if not HAS_MPL:
            ctk.CTkLabel(self.body, text="（未安裝 matplotlib，無法繪圖）",
                         font=ctk.CTkFont(size=13),
                         text_color="gray").pack(pady=40)
            return
        dates = s.get("dates") or []
        if not dates:
            ctk.CTkLabel(self.body, text="（此區間無資料）",
                         font=ctk.CTkFont(size=13),
                         text_color="gray").pack(pady=40)
            return
        # 只取最後 _MAX_BARS 根，太多根會擠成一片看不出型態
        sl = slice(max(0, len(dates) - _MAX_BARS), len(dates))
        d = dates[sl]
        o, h, lo, c = (s["open"][sl], s["high"][sl], s["low"][sl],
                       s["close"][sl])
        net = s["net_lots"][sl]
        cum = s["cum_lots"][sl]
        xs = list(range(len(d)))

        from matplotlib.figure import Figure
        import matplotlib.ticker as mticker

        fig = Figure(figsize=(_W, _H), dpi=100 * 2, facecolor=cs.BG,
                     layout="constrained")
        fig.get_layout_engine().set(w_pad=0.02, h_pad=0.02, wspace=0,
                                    hspace=0.04)
        # 上下 2:1：價格為主、買賣超為輔（一般看盤軟體的比例）
        gs = fig.add_gridspec(3, 1)
        ax_p = fig.add_subplot(gs[0:2, 0])
        ax_v = fig.add_subplot(gs[2, 0], sharex=ax_p)
        for ax in (ax_p, ax_v):
            ax.set_facecolor(cs.BG)

        # ---- 上：股價 K 線（缺價日跳過）----
        idx = [i for i in xs if c[i]]
        if idx:
            cs.candles(ax_p, idx, [o[i] for i in idx], [h[i] for i in idx],
                       [lo[i] for i in idx], [c[i] for i in idx], width=0.62)
        else:
            ax_p.text(0.5, 0.5, "此區間無股價資料（僅顯示買賣超）",
                      transform=ax_p.transAxes, ha="center", va="center",
                      color=cs.FLAT, fontsize=18)
        cs.style_axis(ax_p, ylabel="股價")

        # ---- 上：累計買賣超（右軸，看分點是持續吃貨還是出貨）----
        ax_c = ax_p.twinx()
        ax_c.plot(xs, cum, color=_CUM_CLR, linewidth=1.5 * 2,
                  solid_capstyle="round", zorder=6, alpha=0.95)
        ax_c.axhline(0, color=cs.FLAT, linewidth=0.8, alpha=0.35)
        ax_c.tick_params(colors=_CUM_CLR, labelsize=14, length=0)
        ax_c.set_ylabel("累計買賣超(張)", fontsize=14, color=_CUM_CLR)
        ax_c.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda v, _: f"{int(v):,}"))
        for sp in ax_c.spines.values():
            sp.set_visible(False)

        # ---- 下：每日買賣超長條（買超紅、賣超綠）----
        colors = [cs.RED if v > 0 else cs.GREEN if v < 0 else cs.FLAT
                  for v in net]
        ax_v.bar(xs, net, width=0.68, color=colors, linewidth=0)
        ax_v.axhline(0, color=cs.GRID, linewidth=1.0)
        cs.style_axis(ax_v, ylabel="買賣超(張)")

        # x 軸：等距取約 8 個日期標籤（避免重疊）
        step = max(1, len(d) // 8)
        ticks = list(range(0, len(d), step))
        ax_v.set_xticks(ticks)
        ax_v.set_xticklabels([d[i][5:] for i in ticks], fontsize=14,
                             color=cs.TXT)
        ax_p.tick_params(labelbottom=False)
        ax_v.set_xlim(-1, len(d))

        cs.embed(self.body, fig, _W, _H)

    # ================================================================ Close
    def _on_close(self):
        try:
            self.vm.shutdown()
        except Exception:
            pass
        self.destroy()
