"""策略篩選 — screen stocks by predefined strategies."""

from __future__ import annotations

import threading

import customtkinter as ctk
from tkinter import ttk
from datetime import datetime, timedelta

from viewmodels.strategy_viewmodel import StrategyViewModel

# matplotlib for the per-stock detail chart popup
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.ticker as mticker
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
    from matplotlib.gridspec import GridSpec
    from matplotlib.patches import Rectangle
    matplotlib.rcParams["font.sans-serif"] = [
        "Microsoft JhengHei", "Microsoft YaHei", "PMingLiU", "DFKai-SB",
    ]
    matplotlib.rcParams["axes.unicode_minus"] = False
    HAS_MPL = True
except Exception:
    HAS_MPL = False


def _fmt(n: int) -> str:
    return f"{n:,}"


def _lots(shares: int) -> str:
    return _fmt(shares // 1000)


# Dark treeview style
_STYLE_INIT = False


def _ensure_style():
    global _STYLE_INIT
    if _STYLE_INIT:
        return
    _STYLE_INIT = True
    s = ttk.Style()
    name = "Strategy.Treeview"
    s.configure(name,
                background="#252526", foreground="#d4d4d4",
                fieldbackground="#252526", borderwidth=0,
                rowheight=30, font=("Microsoft JhengHei", 12))
    s.map(name,
          background=[("selected", "#264f78")],
          foreground=[("selected", "#ffffff")])
    s.configure(f"{name}.Heading",
                background="#2d2d2d", foreground="#cccccc",
                borderwidth=0, relief="flat",
                font=("Microsoft JhengHei", 12, "bold"))
    s.map(f"{name}.Heading",
          background=[("active", "#3e3e3e")])


class StrategyView(ctk.CTkFrame):
    """策略篩選 tab page."""

    def __init__(self, parent: ctk.CTkFrame, viewmodel: StrategyViewModel):
        super().__init__(parent, fg_color="transparent")
        self.vm = viewmodel
        self._tree: ttk.Treeview | None = None
        self._active_strategy = 1
        # 策略四：快取上一次結果以便 icon checkbox 切換時即時重繪
        self._sd_last_data: list | None = None
        self._build_ui()
        self._bind_vm()

    def _build_ui(self):
        container = ctk.CTkScrollableFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True)

        # Title
        ctk.CTkLabel(
            container, text="策略篩選",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(pady=(24, 8))
        ctk.CTkLabel(
            container,
            text="依據預設策略條件，篩選符合條件的股票標的",
            font=ctk.CTkFont(size=14), text_color="gray",
        ).pack(pady=(0, 20))

        # ============================================================
        # Strategy 1: 自營商避險
        # ============================================================
        card = ctk.CTkFrame(container, corner_radius=12)
        card.pack(padx=40, pady=8, fill="x")

        ctk.CTkLabel(
            card, text="策略一：自營商避險買超篩選",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=24, pady=(20, 4))

        ctk.CTkLabel(
            card,
            text="篩選自營商總部（如凱基、統一等）買超佔比 ≥ X%、買超金額 ≥ Y 萬、隔日沖佔比 ≥ Z% 的標的",
            font=ctk.CTkFont(size=13), text_color="gray",
        ).pack(anchor="w", padx=24, pady=(0, 12))

        # Parameters row
        param_row = ctk.CTkFrame(card, fg_color="transparent")
        param_row.pack(fill="x", padx=24, pady=4)

        ctk.CTkLabel(param_row, text="交易日期：",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.date_entry = ctk.CTkEntry(
            param_row, width=130, font=ctk.CTkFont(size=14),
            placeholder_text="yyyy-mm-dd")
        self.date_entry.pack(side="left", padx=(4, 16))
        self.date_entry.insert(0, datetime.now().strftime("%Y-%m-%d"))

        ctk.CTkLabel(param_row, text="自營比 ≥",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.pct_entry = ctk.CTkEntry(
            param_row, width=60, font=ctk.CTkFont(size=14))
        self.pct_entry.pack(side="left", padx=(4, 2))
        self.pct_entry.insert(0, "10")
        ctk.CTkLabel(param_row, text="%",
                      font=ctk.CTkFont(size=14)).pack(side="left", padx=(0, 16))

        ctk.CTkLabel(param_row, text="買超金額 ≥",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.amt_entry = ctk.CTkEntry(
            param_row, width=80, font=ctk.CTkFont(size=14))
        self.amt_entry.pack(side="left", padx=(4, 2))
        self.amt_entry.insert(0, "1000")
        ctk.CTkLabel(param_row, text="萬",
                      font=ctk.CTkFont(size=14)).pack(side="left", padx=(0, 16))

        ctk.CTkLabel(param_row, text="隔日沖佔比 ≥",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.nd_entry = ctk.CTkEntry(
            param_row, width=60, font=ctk.CTkFont(size=14))
        self.nd_entry.pack(side="left", padx=(4, 2))
        self.nd_entry.insert(0, "10")
        ctk.CTkLabel(param_row, text="%",
                      font=ctk.CTkFont(size=14)).pack(side="left")

        # Button row
        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill="x", padx=24, pady=(8, 4))

        self.run_btn = ctk.CTkButton(
            btn_row, text="執行篩選", width=120, height=36,
            corner_radius=8,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#ab47bc", hover_color="#8e24aa",
            command=self._on_run,
        )
        self.run_btn.pack(side="left")

        self.status_label = ctk.CTkLabel(
            btn_row, text="", font=ctk.CTkFont(size=13),
            text_color="#4ECDC4")
        self.status_label.pack(side="left", padx=(12, 0))

        self.error_label = ctk.CTkLabel(
            btn_row, text="", font=ctk.CTkFont(size=13),
            text_color="#FF6B6B")
        self.error_label.pack(side="left", padx=(12, 0))

        # Results table for strategy 1
        self.result_frame = ctk.CTkFrame(card, fg_color="transparent")
        self.result_frame.pack(fill="both", expand=True, padx=16, pady=(4, 16))

        # ============================================================
        # Strategy 2: Bollinger Breakout + Dealer Buy
        # ============================================================
        card2 = ctk.CTkFrame(container, corner_radius=12)
        card2.pack(padx=40, pady=8, fill="x")

        ctk.CTkLabel(
            card2, text="策略二：布林通道突破 + 自營商買超",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=24, pady=(20, 4))

        ctk.CTkLabel(
            card2,
            text="篩選收盤價突破布林上軌（20MA+2σ）且自營商總部有買超的標的",
            font=ctk.CTkFont(size=13), text_color="gray",
        ).pack(anchor="w", padx=24, pady=(0, 12))

        param2_row = ctk.CTkFrame(card2, fg_color="transparent")
        param2_row.pack(fill="x", padx=24, pady=4)

        ctk.CTkLabel(param2_row, text="交易日期：",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.bb_date_entry = ctk.CTkEntry(
            param2_row, width=130, font=ctk.CTkFont(size=14),
            placeholder_text="yyyy-mm-dd")
        self.bb_date_entry.pack(side="left", padx=(4, 16))
        self.bb_date_entry.insert(0, datetime.now().strftime("%Y-%m-%d"))

        ctk.CTkLabel(param2_row, text="週期",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.bb_period_entry = ctk.CTkEntry(
            param2_row, width=50, font=ctk.CTkFont(size=14))
        self.bb_period_entry.pack(side="left", padx=(4, 2))
        self.bb_period_entry.insert(0, "20")

        ctk.CTkLabel(param2_row, text="倍數",
                      font=ctk.CTkFont(size=14)).pack(side="left", padx=(12, 0))
        self.bb_k_entry = ctk.CTkEntry(
            param2_row, width=50, font=ctk.CTkFont(size=14))
        self.bb_k_entry.pack(side="left", padx=(4, 0))
        self.bb_k_entry.insert(0, "2")

        btn2_row = ctk.CTkFrame(card2, fg_color="transparent")
        btn2_row.pack(fill="x", padx=24, pady=(8, 4))

        self.bb_run_btn = ctk.CTkButton(
            btn2_row, text="執行篩選", width=120, height=36,
            corner_radius=8,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#ff6d00", hover_color="#e65100",
            command=self._on_run_bb,
        )
        self.bb_run_btn.pack(side="left")

        self.bb_status_label = ctk.CTkLabel(
            btn2_row, text="", font=ctk.CTkFont(size=13),
            text_color="#4ECDC4")
        self.bb_status_label.pack(side="left", padx=(12, 0))

        self.bb_error_label = ctk.CTkLabel(
            btn2_row, text="", font=ctk.CTkFont(size=13),
            text_color="#FF6B6B")
        self.bb_error_label.pack(side="left", padx=(12, 0))

        # Results table for strategy 2
        self.bb_result_frame = ctk.CTkFrame(card2, fg_color="transparent")
        self.bb_result_frame.pack(fill="both", expand=True, padx=16, pady=(4, 16))

        # ============================================================
        # Strategy 3: 主力集中度即將黃金交叉
        # ============================================================
        card3 = ctk.CTkFrame(container, corner_radius=12)
        card3.pack(padx=40, pady=8, fill="x")

        ctk.CTkLabel(
            card3, text="策略三：主力集中度即將黃金交叉",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=24, pady=(20, 4))

        ctk.CTkLabel(
            card3,
            text="掃描所有個股，找出短期還沒上穿長期、但 gap 已收窄到閾值內"
                 "的候選；集中度不限正負（兩線皆負代表賣壓中即將反轉）；"
                 "依 gap 收窄速率推估距離交叉的天數；可加掛三大法人連續"
                 "買超驗證「建倉」、TDCC 大戶持股增加驗證「籌碼集中」",
            font=ctk.CTkFont(size=13), text_color="gray",
            wraplength=860, justify="left",
        ).pack(anchor="w", padx=24, pady=(0, 12))

        # Param row 1: date + windows
        param3a = ctk.CTkFrame(card3, fg_color="transparent")
        param3a.pack(fill="x", padx=24, pady=4)
        ctk.CTkLabel(param3a, text="交易日期：",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.ic_date_entry = ctk.CTkEntry(
            param3a, width=130, font=ctk.CTkFont(size=14),
            placeholder_text="yyyy-mm-dd")
        self.ic_date_entry.pack(side="left", padx=(4, 16))
        self.ic_date_entry.insert(0, datetime.now().strftime("%Y-%m-%d"))

        ctk.CTkLabel(param3a, text="短期",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.ic_short_entry = ctk.CTkEntry(
            param3a, width=50, font=ctk.CTkFont(size=14), justify="center")
        self.ic_short_entry.pack(side="left", padx=(4, 2))
        self.ic_short_entry.insert(0, "5")
        ctk.CTkLabel(param3a, text="日　長期",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.ic_long_entry = ctk.CTkEntry(
            param3a, width=50, font=ctk.CTkFont(size=14), justify="center")
        self.ic_long_entry.pack(side="left", padx=(4, 2))
        self.ic_long_entry.insert(0, "15")
        ctk.CTkLabel(param3a, text="日　主力前",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.ic_topn_entry = ctk.CTkEntry(
            param3a, width=50, font=ctk.CTkFont(size=14), justify="center")
        self.ic_topn_entry.pack(side="left", padx=(4, 2))
        self.ic_topn_entry.insert(0, "15")
        ctk.CTkLabel(param3a, text="家",
                      font=ctk.CTkFont(size=14)).pack(side="left")

        # Param row 2: gap + narrowing
        param3b = ctk.CTkFrame(card3, fg_color="transparent")
        param3b.pack(fill="x", padx=24, pady=4)
        ctk.CTkLabel(param3b, text="最大 gap ≤",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.ic_gap_entry = ctk.CTkEntry(
            param3b, width=60, font=ctk.CTkFont(size=14), justify="center")
        self.ic_gap_entry.pack(side="left", padx=(4, 2))
        self.ic_gap_entry.insert(0, "2.0")
        ctk.CTkLabel(param3b, text="%　",
                      font=ctk.CTkFont(size=14)).pack(side="left")

        self.ic_narrow_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            param3b, text="要求 gap 正在收窄（短期已開始追上來）",
            variable=self.ic_narrow_var,
            font=ctk.CTkFont(size=13),
        ).pack(side="left")

        # Param row 3: 三大法人連續買超（建倉偵測）
        param3c = ctk.CTkFrame(card3, fg_color="transparent")
        param3c.pack(fill="x", padx=24, pady=(2, 4))
        ctk.CTkLabel(param3c, text="法人建倉：",
                      font=ctk.CTkFont(size=14)).pack(side="left", padx=(0, 4))
        self.ic_foreign_var = ctk.BooleanVar(value=False)
        self.ic_trust_var = ctk.BooleanVar(value=False)
        self.ic_dealer_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            param3c, text="外資", variable=self.ic_foreign_var,
            font=ctk.CTkFont(size=13),
        ).pack(side="left", padx=4)
        ctk.CTkCheckBox(
            param3c, text="投信", variable=self.ic_trust_var,
            font=ctk.CTkFont(size=13),
        ).pack(side="left", padx=4)
        ctk.CTkCheckBox(
            param3c, text="自營", variable=self.ic_dealer_var,
            font=ctk.CTkFont(size=13),
        ).pack(side="left", padx=4)
        ctk.CTkLabel(param3c, text="　連續買超 ≥",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.ic_days_entry = ctk.CTkEntry(
            param3c, width=50, font=ctk.CTkFont(size=14), justify="center")
        self.ic_days_entry.pack(side="left", padx=(4, 2))
        self.ic_days_entry.insert(0, "3")
        ctk.CTkLabel(param3c, text="天",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        ctk.CTkLabel(
            param3c,
            text="　（三項全未勾 → 不套用法人過濾）",
            font=ctk.CTkFont(size=12), text_color="gray",
        ).pack(side="left")

        # Param row 4: 籌碼過濾（大戶持股增加）
        param3d = ctk.CTkFrame(card3, fg_color="transparent")
        param3d.pack(fill="x", padx=24, pady=(2, 4))
        self.ic_chip_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            param3d, text="籌碼過濾：大戶持股增加",
            variable=self.ic_chip_var,
            font=ctk.CTkFont(size=13),
        ).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(param3d, text="比較期",
                      font=ctk.CTkFont(size=13),
                      text_color="#c0c0c0").pack(side="left", padx=(0, 4))
        self.ic_chip_weeks_entry = ctk.CTkEntry(
            param3d, width=46, font=ctk.CTkFont(size=13), justify="center")
        self.ic_chip_weeks_entry.pack(side="left")
        self.ic_chip_weeks_entry.insert(0, "4")
        ctk.CTkLabel(param3d, text="週　大戶 ≥ +",
                      font=ctk.CTkFont(size=13),
                      text_color="#c0c0c0").pack(side="left", padx=(2, 4))
        self.ic_chip_big_entry = ctk.CTkEntry(
            param3d, width=64, font=ctk.CTkFont(size=13), justify="center")
        self.ic_chip_big_entry.pack(side="left")
        self.ic_chip_big_entry.insert(0, "0.0")
        ctk.CTkLabel(param3d, text="%（可填小數）",
                      font=ctk.CTkFont(size=13),
                      text_color="#c0c0c0").pack(side="left")
        ctk.CTkLabel(
            param3d,
            text="　（未勾 → 不套用，依賴 TDCC 集保週報資料）",
            font=ctk.CTkFont(size=12), text_color="gray",
        ).pack(side="left")

        btn3_row = ctk.CTkFrame(card3, fg_color="transparent")
        btn3_row.pack(fill="x", padx=24, pady=(8, 4))
        self.ic_run_btn = ctk.CTkButton(
            btn3_row, text="執行篩選", width=120, height=36,
            corner_radius=8,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#42a5f5", hover_color="#1e88e5",
            command=self._on_run_ic)
        self.ic_run_btn.pack(side="left")
        self.ic_status_label = ctk.CTkLabel(
            btn3_row, text="", font=ctk.CTkFont(size=13),
            text_color="#4ECDC4")
        self.ic_status_label.pack(side="left", padx=(12, 0))
        self.ic_error_label = ctk.CTkLabel(
            btn3_row, text="", font=ctk.CTkFont(size=13),
            text_color="#FF6B6B")
        self.ic_error_label.pack(side="left", padx=(12, 0))

        self.ic_result_frame = ctk.CTkFrame(card3, fg_color="transparent")
        self.ic_result_frame.pack(fill="both", expand=True,
                                    padx=16, pady=(4, 16))

        # ============================================================
        # Strategy 4: 放空當沖標的
        # ============================================================
        card4 = ctk.CTkFrame(container, corner_radius=12)
        card4.pack(padx=40, pady=8, fill="x")

        ctk.CTkLabel(
            card4, text="策略四：放空當沖標的（黑K獵手）",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=24, pady=(20, 4))

        ctk.CTkLabel(
            card4,
            text="高位階（基期高） + 主力近10日出貨 + 月線下彎 + 短線振幅大"
                 "（帶寬>20）→ 黑K放空當沖候選。依位階 desc 排序，"
                 "搭配年線乖離越大代表近期走勢越強、放空風險越高，"
                 "故僅適合「短空」。",
            font=ctk.CTkFont(size=13), text_color="gray",
            wraplength=860, justify="left",
        ).pack(anchor="w", padx=24, pady=(0, 12))

        # Param row 1: date + 主10 + 帶寬 + 月斜率
        param4a = ctk.CTkFrame(card4, fg_color="transparent")
        param4a.pack(fill="x", padx=24, pady=4)
        ctk.CTkLabel(param4a, text="交易日期：",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.sd_date_entry = ctk.CTkEntry(
            param4a, width=130, font=ctk.CTkFont(size=14),
            placeholder_text="yyyy-mm-dd")
        self.sd_date_entry.pack(side="left", padx=(4, 16))
        self.sd_date_entry.insert(0, datetime.now().strftime("%Y-%m-%d"))

        ctk.CTkLabel(param4a, text="主10 <",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.sd_conc_entry = ctk.CTkEntry(
            param4a, width=56, font=ctk.CTkFont(size=14), justify="center")
        self.sd_conc_entry.pack(side="left", padx=(4, 2))
        self.sd_conc_entry.insert(0, "0")
        ctk.CTkLabel(param4a, text="%　帶寬 >",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.sd_band_entry = ctk.CTkEntry(
            param4a, width=56, font=ctk.CTkFont(size=14), justify="center")
        self.sd_band_entry.pack(side="left", padx=(4, 2))
        self.sd_band_entry.insert(0, "20")
        ctk.CTkLabel(param4a, text="%　月斜率 <",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.sd_slope_entry = ctk.CTkEntry(
            param4a, width=56, font=ctk.CTkFont(size=14), justify="center")
        self.sd_slope_entry.pack(side="left", padx=(4, 2))
        self.sd_slope_entry.insert(0, "0")
        ctk.CTkLabel(param4a, text="%",
                      font=ctk.CTkFont(size=14)).pack(side="left")

        # Param row 2: 年線乖離 + 主力家數
        # 註：位階改以布林通道為基準（上軌=+10、中軌=0、下軌=-10），
        # 不再需要「位階窗口」輸入；rank_window 在 VM/service 已棄用
        param4b = ctk.CTkFrame(card4, fg_color="transparent")
        param4b.pack(fill="x", padx=24, pady=4)
        # 乖離條件預設僅勾雙週(MA12)，其餘需手動勾
        self.sd_bias_use_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            param4b, text="年線乖離 ≥", variable=self.sd_bias_use_var,
            font=ctk.CTkFont(size=14),
        ).pack(side="left")
        self.sd_bias_entry = ctk.CTkEntry(
            param4b, width=56, font=ctk.CTkFont(size=14), justify="center")
        self.sd_bias_entry.pack(side="left", padx=(6, 2))
        self.sd_bias_entry.insert(0, "10")
        ctk.CTkLabel(param4b, text="%　主力前",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.sd_topn_entry = ctk.CTkEntry(
            param4b, width=56, font=ctk.CTkFont(size=14), justify="center")
        self.sd_topn_entry.pack(side="left", padx=(4, 2))
        self.sd_topn_entry.insert(0, "15")
        ctk.CTkLabel(param4b, text="家",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        ctk.CTkLabel(
            param4b,
            text="　（勾選才套用；年線乖離越大 = 越強勢，MA250 資料不足者排除）",
            font=ctk.CTkFont(size=12), text_color="gray",
        ).pack(side="left", padx=(12, 0))

        # Param row 3: 短中期 MA 乖離弱勢門檻
        param4c = ctk.CTkFrame(card4, fg_color="transparent")
        param4c.pack(fill="x", padx=24, pady=4)
        self.sd_b6_use_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            param4c, text="周乖離(MA6) ≤", variable=self.sd_b6_use_var,
            font=ctk.CTkFont(size=14),
        ).pack(side="left")
        self.sd_b6_entry = ctk.CTkEntry(
            param4c, width=56, font=ctk.CTkFont(size=14), justify="center")
        self.sd_b6_entry.pack(side="left", padx=(6, 2))
        self.sd_b6_entry.insert(0, "-3")
        ctk.CTkLabel(param4c, text="%　",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.sd_b12_use_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            param4c, text="雙週(MA12) ≤", variable=self.sd_b12_use_var,
            font=ctk.CTkFont(size=14),
        ).pack(side="left")
        self.sd_b12_entry = ctk.CTkEntry(
            param4c, width=56, font=ctk.CTkFont(size=14), justify="center")
        self.sd_b12_entry.pack(side="left", padx=(6, 2))
        self.sd_b12_entry.insert(0, "-4.5")
        ctk.CTkLabel(param4c, text="%　",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.sd_b20_use_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            param4c, text="月線(MA20) ≤", variable=self.sd_b20_use_var,
            font=ctk.CTkFont(size=14),
        ).pack(side="left")
        self.sd_b20_entry = ctk.CTkEntry(
            param4c, width=56, font=ctk.CTkFont(size=14), justify="center")
        self.sd_b20_entry.pack(side="left", padx=(6, 2))
        self.sd_b20_entry.insert(0, "-7")
        ctk.CTkLabel(param4c, text="%　",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.sd_b72_use_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            param4c, text="季線(MA72) ≤", variable=self.sd_b72_use_var,
            font=ctk.CTkFont(size=14),
        ).pack(side="left")
        self.sd_b72_entry = ctk.CTkEntry(
            param4c, width=56, font=ctk.CTkFont(size=14), justify="center")
        self.sd_b72_entry.pack(side="left", padx=(6, 2))
        self.sd_b72_entry.insert(0, "-11")
        ctk.CTkLabel(param4c, text="%",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        ctk.CTkLabel(
            param4c,
            text="　（勾選才套用；負乖離越深 = 越弱勢）",
            font=ctk.CTkFont(size=12), text_color="gray",
        ).pack(side="left", padx=(12, 0))

        # Param row 4: 訊號門檻（勾選 = 該訊號 icon 會顯示在列表上）
        # 不是過濾條件 — 候選列表不會因此被剔除，只控制 ▼助空/▲警訊 欄的圖示
        param4d = ctk.CTkFrame(card4, fg_color="transparent")
        param4d.pack(fill="x", padx=24, pady=(2, 4))
        ctk.CTkLabel(param4d, text="訊號門檻：",
                      font=ctk.CTkFont(size=14)).pack(side="left", padx=(0, 8))

        # 隔日沖佔比
        self.sd_show_nflip_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            param4d, text="隔日沖佔比 ≥", variable=self.sd_show_nflip_var,
            font=ctk.CTkFont(size=13),
            command=self._on_sd_show_toggle,
        ).pack(side="left")
        self.sd_sig_nflip_entry = ctk.CTkEntry(
            param4d, width=46, font=ctk.CTkFont(size=13), justify="center")
        self.sd_sig_nflip_entry.pack(side="left", padx=(6, 2))
        self.sd_sig_nflip_entry.insert(0, "2")
        ctk.CTkLabel(param4d, text="%　", font=ctk.CTkFont(size=13),
                      text_color="#c0c0c0").pack(side="left")

        # 外資連賣
        self.sd_show_fstreak_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            param4d, text="外資連賣 ≥", variable=self.sd_show_fstreak_var,
            font=ctk.CTkFont(size=13),
            command=self._on_sd_show_toggle,
        ).pack(side="left")
        self.sd_sig_fstreak_entry = ctk.CTkEntry(
            param4d, width=40, font=ctk.CTkFont(size=13), justify="center")
        self.sd_sig_fstreak_entry.pack(side="left", padx=(6, 2))
        self.sd_sig_fstreak_entry.insert(0, "3")
        ctk.CTkLabel(param4d, text="天　", font=ctk.CTkFont(size=13),
                      text_color="#c0c0c0").pack(side="left")

        # 自營賣
        self.sd_show_dump_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            param4d, text="自營賣 ≥", variable=self.sd_show_dump_var,
            font=ctk.CTkFont(size=13),
            command=self._on_sd_show_toggle,
        ).pack(side="left")
        self.sd_sig_dump_entry = ctk.CTkEntry(
            param4d, width=46, font=ctk.CTkFont(size=13), justify="center")
        self.sd_sig_dump_entry.pack(side="left", padx=(6, 2))
        self.sd_sig_dump_entry.insert(0, "200")
        ctk.CTkLabel(param4d, text="張　", font=ctk.CTkFont(size=13),
                      text_color="#c0c0c0").pack(side="left")

        # 融資增
        self.sd_show_mchase_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            param4d, text="融資增 ≥", variable=self.sd_show_mchase_var,
            font=ctk.CTkFont(size=13),
            command=self._on_sd_show_toggle,
        ).pack(side="left")
        self.sd_sig_mchase_entry = ctk.CTkEntry(
            param4d, width=46, font=ctk.CTkFont(size=13), justify="center")
        self.sd_sig_mchase_entry.pack(side="left", padx=(6, 2))
        self.sd_sig_mchase_entry.insert(0, "5")
        ctk.CTkLabel(param4d, text="%　", font=ctk.CTkFont(size=13),
                      text_color="#c0c0c0").pack(side="left")

        # 券資比
        self.sd_show_squeeze_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            param4d, text="券資比 ≥", variable=self.sd_show_squeeze_var,
            font=ctk.CTkFont(size=13),
            command=self._on_sd_show_toggle,
        ).pack(side="left")
        self.sd_sig_sratio_entry = ctk.CTkEntry(
            param4d, width=46, font=ctk.CTkFont(size=13), justify="center")
        self.sd_sig_sratio_entry.pack(side="left", padx=(6, 2))
        self.sd_sig_sratio_entry.insert(0, "30")
        ctk.CTkLabel(param4d, text="%",
                      font=ctk.CTkFont(size=13),
                      text_color="#c0c0c0").pack(side="left")

        # Legend：助空 / 警訊 圖示說明
        legend4 = ctk.CTkFrame(card4, fg_color="transparent")
        legend4.pack(fill="x", padx=24, pady=(2, 4))
        ctk.CTkLabel(
            legend4,
            text=("主10勢 ▲=近 5 日遞增（主力慢慢進場、不利空）"
                  "／▼=遞減（出貨持續、強化助空）　　"
                  "▼助空：隔=隔日沖大買、外N=外資連賣 N 天、自=自營大賣、"
                  "戶+=集保戶數爆增、資追=融資逆勢追高（散戶套牢）　　"
                  "▲警訊：投=投信第一天買、大=大戶持股增加、"
                  "戶-=集保戶數下降、軋=券資比過高軋空風險"),
            font=ctk.CTkFont(size=11), text_color="#888",
            wraplength=860, justify="left",
        ).pack(anchor="w")

        btn4_row = ctk.CTkFrame(card4, fg_color="transparent")
        btn4_row.pack(fill="x", padx=24, pady=(8, 4))
        self.sd_run_btn = ctk.CTkButton(
            btn4_row, text="執行篩選", width=120, height=36,
            corner_radius=8,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#26a69a", hover_color="#00897b",
            command=self._on_run_sd)
        self.sd_run_btn.pack(side="left")
        self.sd_status_label = ctk.CTkLabel(
            btn4_row, text="", font=ctk.CTkFont(size=13),
            text_color="#4ECDC4")
        self.sd_status_label.pack(side="left", padx=(12, 0))
        self.sd_error_label = ctk.CTkLabel(
            btn4_row, text="", font=ctk.CTkFont(size=13),
            text_color="#FF6B6B")
        self.sd_error_label.pack(side="left", padx=(12, 0))

        self.sd_result_frame = ctk.CTkFrame(card4, fg_color="transparent")
        self.sd_result_frame.pack(fill="both", expand=True,
                                  padx=16, pady=(4, 16))

        # ============================================================
        # Strategy 5: 主力進場 + 大戶集中（做多候選）
        # ============================================================
        card5 = ctk.CTkFrame(container, corner_radius=12)
        card5.pack(padx=40, pady=8, fill="x")

        ctk.CTkLabel(
            card5, text="策略五：主力進場 + 大戶集中（做多獵手）",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=24, pady=(20, 4))

        ctk.CTkLabel(
            card5,
            text="主10 近 5 日趨勢遞增（主力慢慢進場）+ 大戶持股 N 週前→現在"
                 "增幅達門檻 → 做多候選；依「主10 遞增量」desc 排序。"
                 "雙擊個股可看技術圖 + 主10 走勢 + 大戶/散戶 比例變化。",
            font=ctk.CTkFont(size=13), text_color="gray",
            wraplength=860, justify="left",
        ).pack(anchor="w", padx=24, pady=(0, 12))

        param5a = ctk.CTkFrame(card5, fg_color="transparent")
        param5a.pack(fill="x", padx=24, pady=4)
        ctk.CTkLabel(param5a, text="交易日期：",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.ma_date_entry = ctk.CTkEntry(
            param5a, width=130, font=ctk.CTkFont(size=14),
            placeholder_text="yyyy-mm-dd")
        self.ma_date_entry.pack(side="left", padx=(4, 16))
        self.ma_date_entry.insert(0, datetime.now().strftime("%Y-%m-%d"))

        ctk.CTkLabel(param5a, text="主10勢 ≥ +",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.ma_cdelta_entry = ctk.CTkEntry(
            param5a, width=56, font=ctk.CTkFont(size=14), justify="center")
        self.ma_cdelta_entry.pack(side="left", padx=(4, 2))
        self.ma_cdelta_entry.insert(0, "0.3")
        ctk.CTkLabel(param5a, text="　大戶比較期",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.ma_weeks_entry = ctk.CTkEntry(
            param5a, width=50, font=ctk.CTkFont(size=14), justify="center")
        self.ma_weeks_entry.pack(side="left", padx=(4, 2))
        self.ma_weeks_entry.insert(0, "4")
        ctk.CTkLabel(param5a, text="週　大戶 ≥ +",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.ma_bdelta_entry = ctk.CTkEntry(
            param5a, width=56, font=ctk.CTkFont(size=14), justify="center")
        self.ma_bdelta_entry.pack(side="left", padx=(4, 2))
        self.ma_bdelta_entry.insert(0, "0")
        ctk.CTkLabel(param5a, text="%　主力前",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.ma_topn_entry = ctk.CTkEntry(
            param5a, width=50, font=ctk.CTkFont(size=14), justify="center")
        self.ma_topn_entry.pack(side="left", padx=(4, 2))
        self.ma_topn_entry.insert(0, "15")
        ctk.CTkLabel(param5a, text="家",
                      font=ctk.CTkFont(size=14)).pack(side="left")

        btn5_row = ctk.CTkFrame(card5, fg_color="transparent")
        btn5_row.pack(fill="x", padx=24, pady=(8, 4))
        self.ma_run_btn = ctk.CTkButton(
            btn5_row, text="執行篩選", width=120, height=36,
            corner_radius=8,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#ef5350", hover_color="#c62828",
            command=self._on_run_ma)
        self.ma_run_btn.pack(side="left")
        self.ma_status_label = ctk.CTkLabel(
            btn5_row, text="", font=ctk.CTkFont(size=13),
            text_color="#4ECDC4")
        self.ma_status_label.pack(side="left", padx=(12, 0))
        self.ma_error_label = ctk.CTkLabel(
            btn5_row, text="", font=ctk.CTkFont(size=13),
            text_color="#FF6B6B")
        self.ma_error_label.pack(side="left", padx=(12, 0))

        self.ma_result_frame = ctk.CTkFrame(card5, fg_color="transparent")
        self.ma_result_frame.pack(fill="both", expand=True,
                                   padx=16, pady=(4, 16))

    # Events

    def _on_run(self):
        self._active_strategy = 1
        date = self.date_entry.get().strip()
        try:
            pct = float(self.pct_entry.get().strip())
        except ValueError:
            pct = 10.0
        try:
            amt = float(self.amt_entry.get().strip()) * 10000  # 萬 → 元
        except ValueError:
            amt = 10_000_000
        try:
            nd_pct = float(self.nd_entry.get().strip())
        except ValueError:
            nd_pct = 10.0
        self.vm.run_dealer_hedge_strategy(date, pct, amt, nd_pct)

    def _on_run_bb(self):
        self._active_strategy = 2
        date = self.bb_date_entry.get().strip()
        try:
            period = int(self.bb_period_entry.get().strip())
        except ValueError:
            period = 20
        try:
            k = float(self.bb_k_entry.get().strip())
        except ValueError:
            k = 2.0
        self.vm.run_bollinger_strategy(date, period, k)

    def _on_run_ic(self):
        """執行策略三：主力集中度即將黃金交叉。"""
        self._active_strategy = 3
        date = self.ic_date_entry.get().strip()

        def _pi(entry, default):
            try:
                v = int(entry.get().strip())
                return v if v > 0 else default
            except ValueError:
                return default

        def _pf(entry, default):
            try:
                return float(entry.get().strip())
            except ValueError:
                return default

        short_w = _pi(self.ic_short_entry, 5)
        long_w = _pi(self.ic_long_entry, 15)
        top_n = _pi(self.ic_topn_entry, 15)
        max_gap = _pf(self.ic_gap_entry, 2.0)
        require_narrow = bool(self.ic_narrow_var.get())

        # 法人建倉勾選 → 組 set；都沒勾就傳空 set 略過法人過濾
        sel = set()
        if self.ic_foreign_var.get():
            sel.add("foreign")
        if self.ic_trust_var.get():
            sel.add("trust")
        if self.ic_dealer_var.get():
            sel.add("dealer")
        min_days = _pi(self.ic_days_entry, 3)

        chip_on = bool(self.ic_chip_var.get())
        chip_weeks = _pi(self.ic_chip_weeks_entry, 4)
        chip_big_gain = _pf(self.ic_chip_big_entry, 0.0)

        self.vm.run_imminent_cross_strategy(
            date, short_window=short_w, long_window=long_w,
            top_n=top_n, max_gap_pct=max_gap,
            require_narrowing=require_narrow,
            insti_types=sel, insti_min_days=min_days,
            chip_filter=chip_on, chip_weeks=chip_weeks,
            chip_big_gain=chip_big_gain,
        )

    def _on_run_sd(self):
        """執行策略四：放空當沖標的。"""
        self._active_strategy = 4
        date = self.sd_date_entry.get().strip()

        def _pf(entry, default):
            try:
                return float(entry.get().strip())
            except ValueError:
                return default

        def _pi(entry, default):
            try:
                v = int(entry.get().strip())
                return v if v > 0 else default
            except ValueError:
                return default

        conc_max = _pf(self.sd_conc_entry, 0.0)
        band_min = _pf(self.sd_band_entry, 20.0)
        slope_max = _pf(self.sd_slope_entry, 0.0)
        bias_min = _pf(self.sd_bias_entry, 10.0)
        top_n = _pi(self.sd_topn_entry, 15)
        b6 = _pf(self.sd_b6_entry, -3.0)
        b12 = _pf(self.sd_b12_entry, -4.5)
        b20 = _pf(self.sd_b20_entry, -7.0)
        b72 = _pf(self.sd_b72_entry, -11.0)
        use_bias = bool(self.sd_bias_use_var.get())
        use_b6 = bool(self.sd_b6_use_var.get())
        use_b12 = bool(self.sd_b12_use_var.get())
        use_b20 = bool(self.sd_b20_use_var.get())
        use_b72 = bool(self.sd_b72_use_var.get())

        sig_nflip = _pf(self.sd_sig_nflip_entry, 2.0)
        sig_fstreak = _pi(self.sd_sig_fstreak_entry, 3)
        sig_dump = _pi(self.sd_sig_dump_entry, 200)
        sig_mchase = _pf(self.sd_sig_mchase_entry, 5.0)
        sig_sratio = _pf(self.sd_sig_sratio_entry, 30.0)

        self.vm.run_short_daytrade_strategy(
            date,
            conc_max=conc_max, band_min=band_min, slope_max=slope_max,
            bias_min=bias_min, top_n=top_n,
            bias6_max=b6, bias12_max=b12,
            bias20_max=b20, bias72_max=b72,
            use_bias_min=use_bias,
            use_bias6=use_b6, use_bias12=use_b12,
            use_bias20=use_b20, use_bias72=use_b72,
            sig_next_flip_pct=sig_nflip,
            sig_foreign_streak=sig_fstreak,
            sig_dealer_dump_lots=sig_dump,
            sig_margin_chase_pct=sig_mchase,
            sig_short_ratio=sig_sratio,
        )

    def _on_run_ma(self):
        """執行策略五：主力進場 + 大戶集中。"""
        self._active_strategy = 5
        date = self.ma_date_entry.get().strip()

        def _pf(entry, default):
            try:
                return float(entry.get().strip())
            except ValueError:
                return default

        def _pi(entry, default):
            try:
                v = int(entry.get().strip())
                return v if v > 0 else default
            except ValueError:
                return default

        cdelta = _pf(self.ma_cdelta_entry, 0.3)
        weeks = _pi(self.ma_weeks_entry, 4)
        bdelta = _pf(self.ma_bdelta_entry, 0.0)
        topn = _pi(self.ma_topn_entry, 15)

        self.vm.run_main_accumulation_strategy(
            date,
            conc_delta_min=cdelta,
            chip_weeks=weeks,
            big_delta_min=bdelta,
            top_n=topn,
        )

    # Bindings

    def _bind_vm(self):
        self.vm.bind("results", self._on_results)
        self.vm.bind("loading", self._on_loading)
        self.vm.bind("error_text", self._on_error)
        self.vm.bind("status_text", self._on_status)

    def _on_loading(self, v):
        def _u():
            if v:
                if self._active_strategy == 1:
                    self.run_btn.configure(state="disabled", text="篩選中...")
                elif self._active_strategy == 2:
                    self.bb_run_btn.configure(state="disabled", text="篩選中...")
                elif self._active_strategy == 3:
                    self.ic_run_btn.configure(state="disabled", text="篩選中...")
                elif self._active_strategy == 4:
                    self.sd_run_btn.configure(state="disabled", text="篩選中...")
                else:
                    self.ma_run_btn.configure(state="disabled", text="篩選中...")
            else:
                self.run_btn.configure(state="normal", text="執行篩選")
                self.bb_run_btn.configure(state="normal", text="執行篩選")
                self.ic_run_btn.configure(state="normal", text="執行篩選")
                self.sd_run_btn.configure(state="normal", text="執行篩選")
                self.ma_run_btn.configure(state="normal", text="執行篩選")
        self.after(0, _u)

    def _on_error(self, v):
        def _u():
            if self._active_strategy == 1:
                self.error_label.configure(text=v)
            elif self._active_strategy == 2:
                self.bb_error_label.configure(text=v)
            elif self._active_strategy == 3:
                self.ic_error_label.configure(text=v)
            elif self._active_strategy == 4:
                self.sd_error_label.configure(text=v)
            else:
                self.ma_error_label.configure(text=v)
        self.after(0, _u)

    def _on_status(self, v):
        def _u():
            if self._active_strategy == 1:
                self.status_label.configure(text=v)
            elif self._active_strategy == 2:
                self.bb_status_label.configure(text=v)
            elif self._active_strategy == 3:
                self.ic_status_label.configure(text=v)
            elif self._active_strategy == 4:
                self.sd_status_label.configure(text=v)
            else:
                self.ma_status_label.configure(text=v)
        self.after(0, _u)

    def _on_results(self, data):
        def _u():
            if self._active_strategy == 1:
                self._render_strategy1(data)
            elif self._active_strategy == 2:
                self._render_strategy2(data)
            elif self._active_strategy == 3:
                self._render_strategy3(data)
            elif self._active_strategy == 4:
                self._render_strategy4(data)
            else:
                self._render_strategy5(data)
        self.after(0, _u)

    def _render_strategy1(self, data):
        for w in self.result_frame.winfo_children():
            w.destroy()
        self._tree = None

        if data is None or len(data) == 0:
            return

        _ensure_style()

        columns = ("rank", "code", "name", "price",
                   "dealer_pct", "nd_pct", "dealer_buy", "buy_amt")
        tree = ttk.Treeview(
            self.result_frame, columns=columns, show="headings",
            style="Strategy.Treeview", height=min(len(data), 20))

        headings = {
            "rank": "#", "code": "代碼", "name": "名稱",
            "price": "收盤",
            "dealer_pct": "自營比%", "nd_pct": "隔沖%",
            "dealer_buy": "自營買(張)", "buy_amt": "買超金額(萬)",
        }
        widths = {
            "rank": 35, "code": 60, "name": 90, "price": 70,
            "dealer_pct": 70, "nd_pct": 60,
            "dealer_buy": 85, "buy_amt": 100,
        }
        anchors = {
            "rank": "center", "code": "center", "name": "w",
            "price": "e", "dealer_pct": "e", "nd_pct": "e",
            "dealer_buy": "e", "buy_amt": "e",
        }

        for c in columns:
            tree.heading(c, text=headings[c])
            tree.column(c, width=widths[c], anchor=anchors[c], stretch=True)

        tree.tag_configure("hot", foreground="#ab47bc")
        tree.tag_configure("buy", foreground="#ef5350")

        for i, r in enumerate(data, 1):
            d_pct = r.get("dealer_pct", 0)
            nd_pct = r.get("next_day_pct", 0)
            tag = "hot" if d_pct >= 20 else "buy"
            tree.insert("", "end", values=(
                i, r["stock_code"], r["stock_name"],
                f"{r['close_price']:,.2f}",
                f"{d_pct:.1f}", f"{nd_pct:.1f}",
                _lots(r.get("dealer_buy", 0)),
                f"{r.get('dealer_buy_amount', 0) / 10000:,.0f}",
            ), tags=(tag,))

        sb = ttk.Scrollbar(
            self.result_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True,
                  padx=(4, 0), pady=4)
        sb.pack(side="right", fill="y", padx=(0, 4), pady=4)
        self._tree = tree

    def _render_strategy2(self, data):
        for w in self.bb_result_frame.winfo_children():
            w.destroy()

        if data is None or len(data) == 0:
            return

        _ensure_style()

        columns = ("rank", "code", "name", "price",
                   "bb_upper", "bb_diff", "dealer_pct", "dealer_buy")
        tree = ttk.Treeview(
            self.bb_result_frame, columns=columns, show="headings",
            style="Strategy.Treeview", height=min(len(data), 20))

        headings = {
            "rank": "#", "code": "代碼", "name": "名稱",
            "price": "收盤", "bb_upper": "BB上軌",
            "bb_diff": "突破%", "dealer_pct": "自營比%",
            "dealer_buy": "自營買(張)",
        }
        widths = {
            "rank": 35, "code": 60, "name": 90, "price": 70,
            "bb_upper": 70, "bb_diff": 65,
            "dealer_pct": 70, "dealer_buy": 85,
        }
        anchors = {
            "rank": "center", "code": "center", "name": "w",
            "price": "e", "bb_upper": "e", "bb_diff": "e",
            "dealer_pct": "e", "dealer_buy": "e",
        }

        for c in columns:
            tree.heading(c, text=headings[c])
            tree.column(c, width=widths[c], anchor=anchors[c], stretch=True)

        tree.tag_configure("hot", foreground="#ff6d00")
        tree.tag_configure("strong", foreground="#ef5350")

        for i, r in enumerate(data, 1):
            diff = r.get("bb_diff_pct", 0)
            tag = "hot" if diff >= 3 else "strong"
            tree.insert("", "end", values=(
                i, r["stock_code"], r["stock_name"],
                f"{r['close_price']:,.2f}",
                f"{r.get('bb_upper', 0):,.2f}",
                f"+{diff:.1f}",
                f"{r.get('dealer_pct', 0):.1f}",
                _lots(r.get("dealer_buy", 0)),
            ), tags=(tag,))

        sb = ttk.Scrollbar(
            self.bb_result_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True,
                  padx=(4, 0), pady=4)
        sb.pack(side="right", fill="y", padx=(0, 4), pady=4)

    def _render_strategy3(self, data):
        for w in self.ic_result_frame.winfo_children():
            w.destroy()

        if data is None or len(data) == 0:
            return

        _ensure_style()

        columns = ("rank", "code", "name", "price",
                   "short", "long", "gap", "narrow", "eta",
                   "foreign", "trust", "dealer",
                   "big_d", "retail_d")
        tree = ttk.Treeview(
            self.ic_result_frame, columns=columns, show="headings",
            style="Strategy.Treeview", height=min(len(data), 20))

        headings = {
            "rank": "#", "code": "代碼", "name": "名稱",
            "price": "收盤",
            "short": "短期%", "long": "長期%", "gap": "gap%",
            "narrow": "收窄%", "eta": "預估交叉(日)",
            "foreign": "外資", "trust": "投信", "dealer": "自營",
            "big_d": "大戶Δ%", "retail_d": "散戶Δ%",
        }
        widths = {
            "rank": 35, "code": 60, "name": 90, "price": 70,
            "short": 65, "long": 65, "gap": 55,
            "narrow": 60, "eta": 95,
            "foreign": 50, "trust": 50, "dealer": 50,
            "big_d": 60, "retail_d": 60,
        }
        anchors = {
            "rank": "center", "code": "center", "name": "w",
            "price": "e", "short": "e", "long": "e", "gap": "e",
            "narrow": "e", "eta": "e",
            "foreign": "center", "trust": "center", "dealer": "center",
            "big_d": "e", "retail_d": "e",
        }

        for c in columns:
            tree.heading(c, text=headings[c])
            tree.column(c, width=widths[c], anchor=anchors[c], stretch=True)

        # 越接近交叉著色越強
        tree.tag_configure("imminent", foreground="#ff6d00")  # ≤ 2 日
        tree.tag_configure("near", foreground="#42a5f5")      # ≤ 5 日
        tree.tag_configure("watch", foreground="#d4d4d4")     # 其他

        def _streak_str(n):
            return f"{n}d" if n and n > 0 else "—"

        def _delta(v):
            return f"{v:+.2f}" if v is not None else "—"

        for i, r in enumerate(data, 1):
            eta = r.get("eta_days")
            if eta is None:
                tag = "watch"
                eta_txt = "—"
            else:
                eta_txt = f"{eta:.1f}"
                tag = ("imminent" if eta <= 2 else
                       "near" if eta <= 5 else "watch")
            tree.insert("", "end", values=(
                i, r["stock_code"], r["stock_name"],
                f"{r['close_price']:,.2f}",
                f"{r['conc_short']:+.2f}",
                f"{r['conc_long']:+.2f}",
                f"{r['gap']:.2f}",
                f"{r['narrowing']:+.2f}",
                eta_txt,
                _streak_str(r.get("foreign_streak", 0)),
                _streak_str(r.get("trust_streak", 0)),
                _streak_str(r.get("dealer_streak", 0)),
                _delta(r.get("chip_big_delta")),
                _delta(r.get("chip_retail_delta")),
            ), tags=(tag,))

        sb = ttk.Scrollbar(
            self.ic_result_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True,
                  padx=(4, 0), pady=4)
        sb.pack(side="right", fill="y", padx=(0, 4), pady=4)

    def _on_sd_show_toggle(self):
        """訊號 checkbox 切換 → 用快取資料即時重繪策略四結果表。"""
        if self._sd_last_data is not None:
            self._render_strategy4(self._sd_last_data)

    def _render_strategy4(self, data):
        # 快取最後一次資料，方便 toggle checkbox 後重繪
        self._sd_last_data = data
        for w in self.sd_result_frame.winfo_children():
            w.destroy()

        if data is None or len(data) == 0:
            return

        _ensure_style()

        columns = ("rank", "code", "name", "price",
                   "conc10", "ctrend", "band", "slope", "pos", "amp",
                   "b6", "b12", "b20", "b72", "b250",
                   "bear", "bull")
        tree = ttk.Treeview(
            self.sd_result_frame, columns=columns, show="headings",
            style="Strategy.Treeview", height=min(len(data), 20))

        headings = {
            "rank": "#", "code": "代碼", "name": "名稱",
            "price": "收盤",
            "conc10": "主10%", "ctrend": "主10勢",
            "band": "帶寬%",
            "slope": "月斜率%", "pos": "位階",
            "amp": "振幅%",
            "b6": "周乖%", "b12": "雙週乖%",
            "b20": "月乖%", "b72": "季乖%",
            "b250": "年乖%",
            "bear": "▼助空", "bull": "▲警訊",
        }
        widths = {
            "rank": 32, "code": 56, "name": 80, "price": 62,
            "conc10": 60, "ctrend": 72, "band": 58,
            "slope": 64, "pos": 52, "amp": 56,
            "b6": 52, "b12": 56, "b20": 52, "b72": 52, "b250": 52,
            "bear": 130, "bull": 110,
        }
        anchors = {
            "rank": "center", "code": "center", "name": "w",
            "price": "e", "conc10": "e", "ctrend": "center",
            "band": "e",
            "slope": "e", "pos": "e", "amp": "e",
            "b6": "e", "b12": "e", "b20": "e",
            "b72": "e", "b250": "e",
            "bear": "center", "bull": "center",
        }

        for c in columns:
            tree.heading(c, text=headings[c])
            tree.column(c, width=widths[c], anchor=anchors[c], stretch=True)

        # 位階越高越優先：高位階(>5) = 深綠（強放空候選），中段 = 灰白
        tree.tag_configure("strong", foreground="#26a69a")
        tree.tag_configure("mid", foreground="#80cbc4")
        tree.tag_configure("watch", foreground="#d4d4d4")

        # iid → (code, name) for double-click 開啟詳細視窗
        sd_lookup: dict[str, tuple[str, str]] = {}

        def _bf(v):
            return f"{v:+.2f}" if v is not None else "—"

        # 讀取 checkbox 狀態，未勾的訊號不顯示
        show_nflip = self.sd_show_nflip_var.get()
        show_fstreak = self.sd_show_fstreak_var.get()
        show_dump = self.sd_show_dump_var.get()
        show_mchase = self.sd_show_mchase_var.get()
        show_squeeze = self.sd_show_squeeze_var.get()

        def _icons(sig: dict | None) -> tuple[str, str]:
            """組「助空」與「警訊」字串，前綴 ▼/▲ 視覺強化。
            符號：
              ▼隔=隔日沖大買、▼外N=外資連賣 N 天、▼自=自營大賣、
              ▼戶+=戶數爆增、▼資追=融資逆勢追高
              ▲投=投信第一天買、▲大=大戶持股增加、
              ▲戶-=戶數下降、▲軋=券資比過高軋空風險
            """
            if not sig:
                return "", ""
            bear = []
            if show_nflip and sig.get("bearish_next_flip_buy"):
                bear.append("▼隔")
            fss = sig.get("bearish_foreign_sell_streak") or 0
            if show_fstreak and fss > 0:
                bear.append(f"▼外{fss}")
            if show_dump and sig.get("bearish_dealer_dump"):
                bear.append("▼自")
            if sig.get("bearish_holder_surge"):
                bear.append("▼戶+")
            if show_mchase and sig.get("bearish_margin_chasing"):
                bear.append("▼資追")
            bull = []
            if sig.get("bullish_trust_first_buy"):
                bull.append("▲投")
            if sig.get("bullish_big_pct_rising"):
                bull.append("▲大")
            if sig.get("bullish_holder_drop"):
                bull.append("▲戶-")
            if show_squeeze and sig.get("bullish_short_squeeze_risk"):
                bull.append("▲軋")
            return " ".join(bear), " ".join(bull)

        for i, r in enumerate(data, 1):
            pos = r.get("rank_pos", 0)
            # 位階以布林通道為基準：≥+8 強勢、≤-8 弱勢、其餘中性
            if pos >= 8:
                tag = "strong"
            elif pos <= -8:
                tag = "watch"
            else:
                tag = "mid"
            bear_txt, bull_txt = _icons(r.get("signals"))
            # 主10 趨勢：與 5 日前比較，>+0.3 = 遞增（警訊，主力慢慢進場）
            delta = r.get("conc_10_delta")
            if delta is None:
                ctrend = "—"
            elif delta > 0.3:
                ctrend = f"▲{delta:+.2f}"   # 遞增：警訊
            elif delta < -0.3:
                ctrend = f"▼{delta:+.2f}"   # 遞減：助空
            else:
                ctrend = f"={delta:+.2f}"   # 持平
            iid = f"sd-{i}"
            sd_lookup[iid] = (r["stock_code"], r["stock_name"])
            tree.insert("", "end", iid=iid, values=(
                i, r["stock_code"], r["stock_name"],
                f"{r['close_price']:,.2f}",
                f"{r['conc_10']:+.2f}",
                ctrend,
                f"{r['bb_bandwidth']:.2f}",
                f"{r['ma20_slope']:+.2f}",
                f"{int(pos):+d}",
                f"{r['amplitude']:.2f}",
                _bf(r.get("ma6_bias")),
                _bf(r.get("ma12_bias")),
                _bf(r.get("ma20_bias")),
                _bf(r.get("ma72_bias")),
                _bf(r.get("ma250_bias")),
                bear_txt, bull_txt,
            ), tags=(tag,))

        def _on_sd_double(event, tv=tree, lookup=sd_lookup):
            iid = tv.identify_row(event.y)
            if not iid:
                return
            info = lookup.get(iid)
            if info:
                self._open_strategy4_detail(*info)
        tree.bind("<Double-1>", _on_sd_double)
        tree.bind("<Return>", lambda e, tv=tree, lookup=sd_lookup:
                  self._open_strategy4_detail(*lookup[tv.focus()])
                  if tv.focus() in lookup else None)

        sb = ttk.Scrollbar(
            self.sd_result_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True,
                  padx=(4, 0), pady=4)
        sb.pack(side="right", fill="y", padx=(0, 4), pady=4)

    def _render_strategy5(self, data):
        for w in self.ma_result_frame.winfo_children():
            w.destroy()

        if data is None or len(data) == 0:
            return

        _ensure_style()

        columns = ("rank", "code", "name", "price",
                   "c10", "cdelta",
                   "big_now", "big_then", "big_delta",
                   "pos", "slope", "b20", "b250")
        tree = ttk.Treeview(
            self.ma_result_frame, columns=columns, show="headings",
            style="Strategy.Treeview", height=min(len(data), 20))

        headings = {
            "rank": "#", "code": "代碼", "name": "名稱",
            "price": "收盤",
            "c10": "主10%", "cdelta": "主10勢",
            "big_now": "大戶%", "big_then": "大戶前%",
            "big_delta": "大戶Δ%",
            "pos": "位階", "slope": "月斜率%",
            "b20": "月乖%", "b250": "年乖%",
        }
        widths = {
            "rank": 32, "code": 56, "name": 88, "price": 64,
            "c10": 60, "cdelta": 70,
            "big_now": 62, "big_then": 70, "big_delta": 70,
            "pos": 50, "slope": 64, "b20": 58, "b250": 58,
        }
        anchors = {
            "rank": "center", "code": "center", "name": "w",
            "price": "e", "c10": "e", "cdelta": "center",
            "big_now": "e", "big_then": "e", "big_delta": "e",
            "pos": "e", "slope": "e", "b20": "e", "b250": "e",
        }
        for c in columns:
            tree.heading(c, text=headings[c])
            tree.column(c, width=widths[c], anchor=anchors[c],
                        stretch=True)

        # 染色：大戶遞增越多 + 主10勢越強 = 越前面 = 深紅
        tree.tag_configure("hot", foreground="#ef5350")
        tree.tag_configure("warm", foreground="#ffa726")
        tree.tag_configure("watch", foreground="#d4d4d4")

        def _bf(v):
            return f"{v:+.2f}" if v is not None else "—"

        ma_lookup: dict[str, tuple[str, str]] = {}
        for i, r in enumerate(data, 1):
            big_d = r.get("big_pct_delta", 0)
            c_delta = r.get("conc_10_delta", 0)
            # 兩個訊號都強 → hot
            if big_d >= 1.0 and c_delta >= 1.0:
                tag = "hot"
            elif big_d >= 0.3 or c_delta >= 0.6:
                tag = "warm"
            else:
                tag = "watch"
            iid = f"ma-{i}"
            ma_lookup[iid] = (r["stock_code"], r["stock_name"])
            tree.insert("", "end", iid=iid, values=(
                i, r["stock_code"], r["stock_name"],
                f"{r['close_price']:,.2f}",
                f"{r['conc_10']:+.2f}",
                f"▲{c_delta:+.2f}" if c_delta > 0 else f"{c_delta:+.2f}",
                f"{r['big_pct_now']:.2f}",
                f"{r['big_pct_then']:.2f}",
                f"{big_d:+.2f}",
                f"{int(r['rank_pos']):+d}",
                f"{r['ma20_slope']:+.2f}",
                _bf(r.get("ma20_bias")),
                _bf(r.get("ma250_bias")),
            ), tags=(tag,))

        def _on_ma_double(event, tv=tree, lookup=ma_lookup):
            iid = tv.identify_row(event.y)
            if not iid:
                return
            info = lookup.get(iid)
            if info:
                self._open_strategy5_detail(*info)
        tree.bind("<Double-1>", _on_ma_double)
        tree.bind("<Return>", lambda e, tv=tree, lookup=ma_lookup:
                  self._open_strategy5_detail(*lookup[tv.focus()])
                  if tv.focus() in lookup else None)

        sb = ttk.Scrollbar(
            self.ma_result_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True,
                  padx=(4, 0), pady=4)
        sb.pack(side="right", fill="y", padx=(0, 4), pady=4)

    # =====================================================================
    # 策略四：個股技術分析 popup
    # =====================================================================

    def _open_strategy4_detail(self, stock_code: str, stock_name: str):
        """雙擊策略四 → 個股技術 popup（無大戶子圖）。"""
        self._open_stock_detail(stock_code, stock_name, include_chip=False)

    def _open_strategy5_detail(self, stock_code: str, stock_name: str):
        """雙擊策略五 → 個股技術 popup + 大戶/散戶 趨勢子圖。"""
        self._open_stock_detail(stock_code, stock_name, include_chip=True)

    def _open_stock_detail(self, stock_code: str, stock_name: str,
                            *, include_chip: bool = False):
        """彈窗顯示個股技術指標 + K 線/MA/布林通道圖。

        include_chip=True 時加一個子圖顯示 TDCC 大戶/散戶 比例變化。
        """
        # 讀對應策略的日期輸入（策略五優先）
        trade_date = ""
        try:
            if include_chip:
                trade_date = (self.ma_date_entry.get() or "").strip()
            else:
                trade_date = (self.sd_date_entry.get() or "").strip()
        except Exception:
            trade_date = ""
        if not trade_date:
            trade_date = datetime.now().strftime("%Y-%m-%d")

        win = ctk.CTkToplevel(self)
        win.title(f"{stock_code} {stock_name} — 技術分析（{trade_date}）")
        win.geometry("980x720")
        win.transient(self.winfo_toplevel())

        # Header
        header = ctk.CTkFrame(win, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(14, 6))
        ctk.CTkLabel(
            header,
            text=f"{stock_code}  {stock_name}",
            font=ctk.CTkFont(size=18, weight="bold"),
        ).pack(anchor="w")
        status_label = ctk.CTkLabel(
            header, text=f"分析日：{trade_date}　載入中…",
            font=ctk.CTkFont(size=12), text_color="#888")
        status_label.pack(anchor="w", pady=(2, 0))

        # Body：上排 = 技術指標表、下排 = 圖表
        body = ctk.CTkFrame(win, corner_radius=10)
        body.pack(fill="both", expand=True, padx=16, pady=(6, 14))

        info_frame = ctk.CTkFrame(body, fg_color="transparent")
        info_frame.pack(fill="x", padx=8, pady=(8, 4))

        chart_frame = ctk.CTkFrame(body, fg_color="#1c1c1e", corner_radius=8)
        chart_frame.pack(fill="both", expand=True, padx=8, pady=(4, 8))

        loading = ctk.CTkLabel(
            chart_frame, text="查詢中…",
            font=ctk.CTkFont(size=13), text_color="gray")
        loading.pack(pady=40)

        def _populate(prices: list[dict], conc_series: tuple[list, list],
                      chip_history: list[dict], err: str | None):
            loading.destroy()
            if err:
                ctk.CTkLabel(
                    chart_frame, text=f"查詢失敗：{err}",
                    font=ctk.CTkFont(size=13), text_color="#FF6B6B",
                ).pack(pady=24)
                status_label.configure(text=f"分析日：{trade_date}　錯誤",
                                       text_color="#FF6B6B")
                return
            if not prices:
                ctk.CTkLabel(
                    chart_frame, text="（該股票在 DB 沒有足夠的歷史價格）",
                    font=ctk.CTkFont(size=13), text_color="gray",
                ).pack(pady=24)
                status_label.configure(text=f"分析日：{trade_date}　無資料")
                return

            self._render_strategy4_info(info_frame, prices, trade_date)
            self._render_strategy4_chart(chart_frame, prices, stock_code,
                                          stock_name, conc_series,
                                          chip_history=chip_history)
            extra_parts = []
            if conc_series[0]:
                extra_parts.append(f"主10 走勢 {len(conc_series[0])} 點")
            if include_chip and chip_history:
                extra_parts.append(f"TDCC 週報 {len(chip_history)} 筆")
            extra = ("、" + "、".join(extra_parts)) if extra_parts else ""
            status_label.configure(
                text=f"分析日：{trade_date}　"
                     f"共 {len(prices)} 筆價格資料{extra}")

        def _work():
            from services.db_service import DbService
            from services.strategy_eval_service import compute_conc10_series
            db = DbService()
            err: str | None = None
            prices: list[dict] = []
            conc_dates: list[str] = []
            conc_vals: list[float] = []
            chip_history: list[dict] = []
            try:
                db.connect()
                # 抓 ~ 300 個交易日（≈ 420 日曆日，足夠算 MA250 + 圖示窗口）
                end_dt = datetime.strptime(trade_date, "%Y-%m-%d")
                start = (end_dt - timedelta(days=420)).strftime("%Y-%m-%d")
                prices = db.get_stock_prices(stock_code, start, trade_date)
                # 算主10 序列需要 broker history（給底部子圖用）
                try:
                    brokers = db.get_all_brokers_daily(
                        stock_code, start, trade_date)
                    conc_dates, conc_vals = compute_conc10_series(
                        brokers, main_window=10, top_n=15)
                except Exception:
                    pass  # 缺分點資料時不影響主圖
                # 策略五：撈 TDCC 大戶/散戶 比例變化
                if include_chip:
                    try:
                        chip_history = db.get_distribution_history(stock_code)
                    except Exception:
                        chip_history = []
            except Exception as e:
                err = str(e)
            finally:
                try:
                    db.close()
                except Exception:
                    pass
            self.after(0, lambda: _populate(
                prices, (conc_dates, conc_vals), chip_history, err))

        threading.Thread(target=_work, daemon=True).start()

    @staticmethod
    def _parse_price(v) -> float | None:
        try:
            return float(str(v).replace(",", ""))
        except (ValueError, TypeError, AttributeError):
            return None

    def _render_strategy4_info(self, parent, prices: list[dict],
                                trade_date: str):
        """渲染技術指標摘要：MA5/10/20/60/120/250 + BB + 位階 + 月斜率。"""
        # 解析收盤序列（升冪）
        closes: list[float] = []
        dates: list[str] = []
        for p in prices:
            c = self._parse_price(p.get("close_price"))
            if c is not None:
                closes.append(c)
                dates.append(str(p.get("trade_date"))[:10])

        if not closes:
            ctk.CTkLabel(parent, text="（收盤資料無法解析）",
                          text_color="#FF6B6B").pack()
            return

        close = closes[-1]
        prev_close = closes[-2] if len(closes) >= 2 else close

        def _ma(period: int) -> float | None:
            if len(closes) < period:
                return None
            return sum(closes[-period:]) / period

        ma5 = _ma(5)
        ma10 = _ma(10)
        ma20 = _ma(20)
        ma60 = _ma(60)
        ma120 = _ma(120)
        ma250 = _ma(250)

        # 布林通道
        bb_upper = bb_lower = bb_bw = None
        if ma20 is not None:
            win = closes[-20:]
            mid = ma20
            var = sum((x - mid) ** 2 for x in win) / 20
            sd = var ** 0.5
            bb_upper = mid + 2 * sd
            bb_lower = mid - 2 * sd
            bb_bw = (bb_upper - bb_lower) / mid * 100 if mid > 0 else None

            # 月線斜率
            if len(closes) >= 21:
                win_prev = closes[-21:-1]
                mid_prev = sum(win_prev) / 20
                slope = ((mid - mid_prev) / mid_prev * 100.0
                         if mid_prev > 0 else 0.0)
            else:
                slope = None

            # 位階 (round to integer level)
            band_half = 2 * sd
            if band_half > 0:
                pos = float(round((close - mid) / band_half * 10.0))
            else:
                pos = 0.0
        else:
            slope = None
            pos = None

        change = close - prev_close
        change_pct = (change / prev_close * 100) if prev_close > 0 else 0.0

        # ---- Row 1: 收盤 + 漲跌 ----
        row1 = ctk.CTkFrame(parent, fg_color="transparent")
        row1.pack(fill="x", pady=(2, 6))
        clr = "#ef5350" if change >= 0 else "#26a69a"
        ctk.CTkLabel(
            row1, text=f"收盤 {close:,.2f}",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=clr,
        ).pack(side="left", padx=(8, 16))
        ctk.CTkLabel(
            row1, text=f"{change:+.2f} ({change_pct:+.2f}%)",
            font=ctk.CTkFont(size=15, weight="bold"),
            text_color=clr,
        ).pack(side="left")
        ctk.CTkLabel(
            row1, text=f"  日期 {dates[-1]}",
            font=ctk.CTkFont(size=12), text_color="#888",
        ).pack(side="left", padx=(12, 0))

        # ---- Row 2: 均線 + 布林 + 位階 ----
        def _fmt(v):
            return f"{v:,.2f}" if v is not None else "—"

        def _bias_pct(v):
            if v is None or v <= 0:
                return "—"
            return f"{(close - v) / v * 100:+.2f}%"

        grid = ctk.CTkFrame(parent, fg_color="transparent")
        grid.pack(fill="x", pady=(0, 4))

        cells = [
            ("MA5", _fmt(ma5), _bias_pct(ma5)),
            ("MA10", _fmt(ma10), _bias_pct(ma10)),
            ("MA20 (月)", _fmt(ma20), _bias_pct(ma20)),
            ("MA60 (季)", _fmt(ma60), _bias_pct(ma60)),
            ("MA120 (半)", _fmt(ma120), _bias_pct(ma120)),
            ("MA250 (年)", _fmt(ma250), _bias_pct(ma250)),
            ("BB 上軌", _fmt(bb_upper), "—"),
            ("BB 下軌", _fmt(bb_lower), "—"),
            ("帶寬%", f"{bb_bw:.2f}" if bb_bw is not None else "—", "—"),
            ("月斜率%", f"{slope:+.2f}" if slope is not None else "—", "—"),
            ("位階",
             f"{int(pos):+d}" if pos is not None else "—", "—"),
        ]
        for i, (label, value, bias) in enumerate(cells):
            cell = ctk.CTkFrame(grid, fg_color="#2a2a2a", corner_radius=6)
            cell.grid(row=i // 6, column=i % 6, padx=3, pady=3,
                      sticky="nsew")
            ctk.CTkLabel(cell, text=label,
                          font=ctk.CTkFont(size=11),
                          text_color="#888").pack(padx=8, pady=(4, 0))
            ctk.CTkLabel(cell, text=value,
                          font=ctk.CTkFont(size=14, weight="bold"),
                          text_color="#d4d4d4").pack(padx=8)
            if bias != "—":
                bc = "#ef5350" if bias.startswith("+") else "#26a69a"
                ctk.CTkLabel(cell, text=bias,
                              font=ctk.CTkFont(size=10),
                              text_color=bc).pack(padx=8, pady=(0, 4))
            else:
                ctk.CTkLabel(cell, text=" ",
                              font=ctk.CTkFont(size=10)).pack(padx=8,
                                                              pady=(0, 4))
        for col in range(6):
            grid.grid_columnconfigure(col, weight=1)

    def _render_strategy4_chart(self, parent, prices: list[dict],
                                  stock_code: str, stock_name: str,
                                  conc_series: tuple[list, list] = (
                                      [], []),
                                  chip_history: list[dict] | None = None):
        """渲染 K 線 + MA5/10/20 + 布林通道；下方子圖顯示主10 走勢條狀圖。

        chip_history 非空時再加第三個子圖顯示 TDCC 大戶/散戶 比例走勢。
        """
        if not HAS_MPL:
            ctk.CTkLabel(
                parent,
                text="（缺 matplotlib，pip install matplotlib 後可看圖表）",
                font=ctk.CTkFont(size=13), text_color="gray",
            ).pack(pady=24)
            return

        labels: list[str] = []
        closes: list[float] = []
        opens: list[float] = []
        highs: list[float] = []
        lows: list[float] = []
        for p in prices:
            c = self._parse_price(p.get("close_price"))
            if c is None:
                continue
            o = self._parse_price(p.get("open_price"))
            h = self._parse_price(p.get("high_price"))
            lo = self._parse_price(p.get("low_price"))
            # 缺 OHLC 任一欄就用 close 補（避免 K 棒畫不出來）
            labels.append(str(p.get("trade_date"))[:10])
            closes.append(c)
            opens.append(o if o is not None else c)
            highs.append(h if h is not None else c)
            lows.append(lo if lo is not None else c)
        if len(closes) < 5:
            ctk.CTkLabel(parent, text="（資料筆數不足）",
                          text_color="gray").pack(pady=24)
            return

        # 取最後 120 筆畫圖（避免太擁擠）
        plot_n = min(len(closes), 120)
        labels = labels[-plot_n:]
        closes = closes[-plot_n:]
        opens = opens[-plot_n:]
        highs = highs[-plot_n:]
        lows = lows[-plot_n:]
        n = len(closes)
        xs = list(range(n))

        def _ma_series(period: int) -> tuple[list[int], list[float]]:
            if n < period:
                return [], []
            xs_out, ys = [], []
            for i in range(period - 1, n):
                xs_out.append(i)
                ys.append(sum(closes[i - period + 1: i + 1]) / period)
            return xs_out, ys

        ma5_x, ma5_y = _ma_series(5)
        ma10_x, ma10_y = _ma_series(10)
        ma20_x, ma20_y = _ma_series(20)

        # 布林通道（period 20, k 2）
        bb_xs: list[int] = []
        bb_upper: list[float] = []
        bb_lower: list[float] = []
        bb_mid: list[float] = []
        if n >= 20:
            for i in range(19, n):
                window = closes[i - 19: i + 1]
                mid = sum(window) / 20
                var = sum((x - mid) ** 2 for x in window) / 20
                sd = var ** 0.5
                bb_xs.append(i)
                bb_mid.append(mid)
                bb_upper.append(mid + 2 * sd)
                bb_lower.append(mid - 2 * sd)

        # ---- Dark theme ----
        bg = "#1c1c1e"
        txt = "#8e8e93"
        grid_clr = "#2c2c2e"

        # 子圖數依是否含 chip 決定
        has_chip = bool(chip_history)
        if has_chip:
            fig = Figure(figsize=(9.2, 6.4), dpi=100, facecolor=bg)
            gs = GridSpec(3, 1, figure=fig,
                          height_ratios=[3, 1, 1.2], hspace=0.18,
                          left=0.07, right=0.97, top=0.95, bottom=0.08)
            ax = fig.add_subplot(gs[0])
            ax_conc = fig.add_subplot(gs[1], sharex=ax)
            ax_chip = fig.add_subplot(gs[2])
            axes_iter = (ax, ax_conc, ax_chip)
        else:
            fig = Figure(figsize=(9.2, 5.2), dpi=100, facecolor=bg)
            gs = GridSpec(2, 1, figure=fig, height_ratios=[3, 1],
                          hspace=0.08,
                          left=0.07, right=0.97, top=0.94, bottom=0.10)
            ax = fig.add_subplot(gs[0])
            ax_conc = fig.add_subplot(gs[1], sharex=ax)
            ax_chip = None
            axes_iter = (ax, ax_conc)

        for ax_ in axes_iter:
            ax_.set_facecolor(bg)
            for sp in ax_.spines.values():
                sp.set_color(grid_clr)
            ax_.tick_params(axis="x", colors=txt, labelsize=8)
            ax_.tick_params(axis="y", colors=txt, labelsize=9)
            ax_.grid(True, alpha=0.2, color=grid_clr, linewidth=0.5)
        # 上方子圖 x 刻度藏起來，避免重複
        ax.tick_params(axis="x", which="both", labelbottom=False)

        # BB band fill + lines
        if bb_xs:
            ax.fill_between(bb_xs, bb_lower, bb_upper,
                            color="#b0b0b0", alpha=0.10, zorder=2)
            ax.plot(bb_xs, bb_upper, color="#b0b0b0", linewidth=0.7,
                    linestyle="--", alpha=0.7, zorder=3, label="BB 上軌")
            ax.plot(bb_xs, bb_lower, color="#b0b0b0", linewidth=0.7,
                    linestyle="--", alpha=0.7, zorder=3, label="BB 下軌")

        # MA lines
        if ma5_x:
            ax.plot(ma5_x, ma5_y, color="#ffeb3b", linewidth=1.1,
                    alpha=0.95, zorder=5, label="MA5")
        if ma10_x:
            ax.plot(ma10_x, ma10_y, color="#42a5f5", linewidth=1.1,
                    alpha=0.95, zorder=5, label="MA10")
        if ma20_x:
            ax.plot(ma20_x, ma20_y, color="#ab47bc", linewidth=1.3,
                    alpha=0.95, zorder=6, label="MA20")

        # K 棒蠟燭圖（台灣慣例：紅漲、綠跌；平盤 = 灰）
        red = "#ef5350"
        green = "#26a69a"
        flat = "#9e9e9e"
        body_w = 0.62
        # 圖例代理（只各加一筆，避免每根 K 都被列入 legend）
        legend_added = {"up": False, "down": False}
        for i, (o, h, lo_v, c) in enumerate(zip(opens, highs, lows, closes)):
            if c > o:
                col = red
                kind = "up"
                lbl = "K 紅(漲)" if not legend_added["up"] else None
                legend_added["up"] = True
            elif c < o:
                col = green
                kind = "down"
                lbl = "K 綠(跌)" if not legend_added["down"] else None
                legend_added["down"] = True
            else:
                col = flat
                lbl = None
            # 上下影線
            ax.plot([i, i], [lo_v, h], color=col, linewidth=0.9,
                    zorder=7)
            # 實體
            body_lo = min(o, c)
            body_hi = max(o, c)
            body_h = body_hi - body_lo
            if body_h < 1e-9:
                # 一字線 / doji
                ax.plot([i - body_w / 2, i + body_w / 2], [c, c],
                        color=col, linewidth=1.4, zorder=8, label=lbl)
            else:
                rect = Rectangle((i - body_w / 2, body_lo), body_w,
                                  body_h, facecolor=col, edgecolor=col,
                                  linewidth=0.5, zorder=8, label=lbl)
                ax.add_patch(rect)

        # X 軸刻度（取等距 ~10 個，放在底部子圖）
        step = max(n // 10, 1)
        tk_idx = list(range(0, n, step))
        if tk_idx[-1] != n - 1:
            tk_idx.append(n - 1)

        def _short(d: str) -> str:
            return d[5:7] + "/" + d[8:10] if len(d) >= 10 else d

        ax.set_xlim(-0.5, n - 0.5)
        ax_conc.set_xticks(tk_idx)
        ax_conc.set_xticklabels([_short(labels[i]) for i in tk_idx],
                                  rotation=35, ha="right", fontsize=8)
        ax_conc.set_xlim(-0.5, n - 0.5)

        # Y 軸：價格範圍含 K 棒高低與 BB
        lo, hi = min(lows), max(highs)
        if bb_xs:
            lo = min(lo, min(bb_lower))
            hi = max(hi, max(bb_upper))
        rng = hi - lo if hi > lo else 1.0
        ax.set_ylim(lo - rng * 0.05, hi + rng * 0.08)
        ax.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda x, _: f"{x:,.2f}"))

        # 標題（上方）
        ax.set_title(f"{stock_code} {stock_name}　近 {n} 個交易日",
                     color="#d4d4d4", fontsize=12,
                     fontweight="bold", pad=8)

        # ---- 底部子圖：主10 走勢條狀（紅=正/主力買、綠=負/主力賣） ----
        conc_dates, conc_vals = conc_series
        ax_conc.axhline(0, color=grid_clr, linewidth=0.6, zorder=3)
        if conc_dates and conc_vals:
            # 把 date 對應到 chart x 軸 index
            label_idx = {d: i for i, d in enumerate(labels)}
            bar_xs: list[int] = []
            bar_vals: list[float] = []
            for d, v in zip(conc_dates, conc_vals):
                if d in label_idx:
                    bar_xs.append(label_idx[d])
                    bar_vals.append(v)
            if bar_xs:
                cols = ["#ef5350" if v > 0 else "#26a69a" for v in bar_vals]
                ax_conc.bar(bar_xs, bar_vals, width=0.7, color=cols,
                             alpha=0.85, zorder=4)
                # 疊一條折線強化趨勢觀察
                ax_conc.plot(bar_xs, bar_vals, color="#ffeb3b",
                              linewidth=0.8, alpha=0.55, zorder=5)
        else:
            ax_conc.text(0.5, 0.5, "（無分點資料，無法計算主10 走勢）",
                          color="#888", fontsize=10, ha="center",
                          va="center", transform=ax_conc.transAxes)
        ax_conc.set_ylabel("主10%", color=txt, fontsize=9, labelpad=4)
        ax_conc.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda x, _: f"{x:.1f}"))

        # ---- 第三子圖：TDCC 大戶 / 散戶 比例走勢（策略五專用） ----
        if ax_chip is not None and chip_history:
            # 取最近 52 週，避免時間軸過長
            recent = chip_history[-52:] if len(chip_history) > 52 \
                else chip_history
            chip_dates = [str(w["report_date"])[:10] for w in recent]
            big_pcts = [float(w["big_pct"]) for w in recent]
            retail_pcts = [float(w["retail_pct"]) for w in recent]
            cxs = list(range(len(chip_dates)))

            ax_chip.plot(cxs, big_pcts, color="#ef5350", linewidth=1.4,
                          marker="o", markersize=2.5, alpha=0.95,
                          label="大戶%")
            ax_chip.plot(cxs, retail_pcts, color="#42a5f5",
                          linewidth=1.4, marker="o", markersize=2.5,
                          alpha=0.95, label="散戶%")

            # 加標起點/終點數字
            if big_pcts:
                ax_chip.annotate(
                    f"{big_pcts[-1]:.1f}",
                    xy=(cxs[-1], big_pcts[-1]),
                    xytext=(4, 0), textcoords="offset points",
                    color="#ef5350", fontsize=9, va="center")
            if retail_pcts:
                ax_chip.annotate(
                    f"{retail_pcts[-1]:.1f}",
                    xy=(cxs[-1], retail_pcts[-1]),
                    xytext=(4, 0), textcoords="offset points",
                    color="#42a5f5", fontsize=9, va="center")

            # 大戶趨勢箭頭（首尾比較）
            if len(big_pcts) >= 2:
                delta = big_pcts[-1] - big_pcts[0]
                arrow = "▲" if delta > 0 else ("▼" if delta < 0 else "＝")
                clr = "#ef5350" if delta > 0 else (
                    "#26a69a" if delta < 0 else "#888")
                ax_chip.text(
                    0.02, 0.92,
                    f"大戶 {arrow} {delta:+.2f}%（"
                    f"{chip_dates[0]} → {chip_dates[-1]}）",
                    color=clr, fontsize=10, fontweight="bold",
                    transform=ax_chip.transAxes,
                    va="top", ha="left")

            # X 刻度
            step_c = max(len(cxs) // 8, 1)
            tk_c = list(range(0, len(cxs), step_c))
            if tk_c and tk_c[-1] != len(cxs) - 1:
                tk_c.append(len(cxs) - 1)
            ax_chip.set_xticks(tk_c)
            ax_chip.set_xticklabels(
                [chip_dates[i][5:] for i in tk_c],
                rotation=35, ha="right", fontsize=8)
            ax_chip.set_xlim(-0.5, len(cxs) - 0.5)
            ax_chip.set_ylabel("持股 %", color=txt, fontsize=9, labelpad=4)
            ax_chip.yaxis.set_major_formatter(
                mticker.FuncFormatter(lambda x, _: f"{x:.1f}"))

            leg_chip = ax_chip.legend(
                loc="upper right", fontsize=9, framealpha=0.85,
                facecolor="#252526", edgecolor=grid_clr,
                labelcolor="#d4d4d4")
            for t in leg_chip.get_texts():
                t.set_color("#d4d4d4")
        elif ax_chip is not None:
            ax_chip.text(
                0.5, 0.5, "（無 TDCC 週報資料）", color="#888",
                fontsize=10, ha="center", va="center",
                transform=ax_chip.transAxes)
            ax_chip.set_xticks([])
            ax_chip.set_yticks([])

        # 圖例（上方）
        leg = ax.legend(loc="upper left", fontsize=9, framealpha=0.85,
                         facecolor="#252526", edgecolor=grid_clr,
                         labelcolor="#d4d4d4")
        for t in leg.get_texts():
            t.set_color("#d4d4d4")

        canvas = FigureCanvasTkAgg(fig, parent)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True,
                                    padx=4, pady=4)
