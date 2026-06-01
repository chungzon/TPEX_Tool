"""效益評估 — 跑策略回測、顯示 KPI 與訊號明細。"""

from __future__ import annotations

import customtkinter as ctk
import tkinter as tk
from tkinter import ttk

from viewmodels.strategy_eval_viewmodel import (
    StrategyEvalViewModel, default_date_range,
)


def _add_int_entry(parent, label: str, default: int, width: int = 52):
    """Build a compact 「label [entry] 後綴」 row helper.

    Returns the CTkEntry so the caller can read it back later.
    """
    ctk.CTkLabel(parent, text=label,
                  font=ctk.CTkFont(size=13),
                  text_color="#c0c0c0").pack(side="left", padx=(0, 4))
    e = ctk.CTkEntry(parent, width=width, font=ctk.CTkFont(size=14),
                      justify="center")
    e.pack(side="left")
    e.insert(0, str(default))
    return e


class StrategyEvalView(ctk.CTkFrame):
    """效益評估 tab page。"""

    def __init__(self, parent: ctk.CTkFrame, viewmodel: StrategyEvalViewModel):
        super().__init__(parent, fg_color="transparent")
        self.vm = viewmodel
        self._table_kind = "breakout"
        self._build_ui()
        self._bind_vm()

    # ---------------------------------------------------------------- UI

    def _build_ui(self):
        container = ctk.CTkScrollableFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True)

        # --- Title ---
        ctk.CTkLabel(
            container, text="效益評估",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(pady=(24, 4))
        ctk.CTkLabel(
            container,
            text="對歷史資料跑策略回測，計算進場訊號的後續報酬",
            font=ctk.CTkFont(size=13), text_color="gray",
        ).pack(pady=(0, 20))

        # --- Strategy description card ---
        desc_card = ctk.CTkFrame(container, corner_radius=12)
        desc_card.pack(padx=40, pady=8, fill="x")

        ctk.CTkLabel(
            desc_card, text="策略一：主力集中度突破",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=20, pady=(14, 6))

        for line in [
            "範圍：上櫃股票（依「系統設定」的股票清單）",
            "進場條件：短期集中度上穿長期集中度（黃金交叉），"
            "不限正負（兩線皆負時的交叉視為賣壓減弱反轉）",
            "進場價：訊號日收盤　・　出場：訊號日後第 N 個交易日收盤",
            "集中度 = (買超前 K 家張數 − 賣超前 K 家張數) ÷ 區間成交量",
            "預設：短期 5 日、長期 15 日、持有 4 日、主力 15 家 "
            "（皆可下方自訂）",
            "可選：籌碼過濾 — 進場訊號當週的 TDCC 集保週報，大戶持股"
            "比例上升（籌碼向大戶集中；散戶Δ% 仍於結果表顯示供參考）",
        ]:
            ctk.CTkLabel(
                desc_card, text="• " + line,
                font=ctk.CTkFont(size=13), text_color="#c0c0c0",
                anchor="w", justify="left", wraplength=860,
            ).pack(anchor="w", padx=24, pady=1)

        ctk.CTkLabel(desc_card, text="", height=4).pack()  # spacer

        # --- Date / run card ---
        run_card = ctk.CTkFrame(container, corner_radius=12)
        run_card.pack(padx=40, pady=8, fill="x")

        date_row = ctk.CTkFrame(run_card, fg_color="transparent")
        date_row.pack(fill="x", padx=20, pady=(16, 6))
        ctk.CTkLabel(date_row, text="回測區間：",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.start_entry = ctk.CTkEntry(
            date_row, width=120, font=ctk.CTkFont(size=14),
            placeholder_text="yyyy-mm-dd")
        self.start_entry.pack(side="left", padx=(4, 4))
        ctk.CTkLabel(date_row, text="~",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.end_entry = ctk.CTkEntry(
            date_row, width=120, font=ctk.CTkFont(size=14),
            placeholder_text="yyyy-mm-dd")
        self.end_entry.pack(side="left", padx=(4, 8))
        sd, ed = default_date_range()
        self.start_entry.insert(0, sd)
        self.end_entry.insert(0, ed)

        ctk.CTkLabel(date_row, text="（預設近 1 年）",
                      font=ctk.CTkFont(size=12),
                      text_color="gray").pack(side="left")

        # Strategy params row (可彈性調整)
        param_row = ctk.CTkFrame(run_card, fg_color="transparent")
        param_row.pack(fill="x", padx=20, pady=(2, 4))
        ctk.CTkLabel(param_row, text="策略參數：",
                      font=ctk.CTkFont(size=14)).pack(side="left", padx=(0, 8))
        VM = StrategyEvalViewModel
        self.short_entry = _add_int_entry(
            param_row, "短期", VM.DEFAULT_SHORT_WINDOW)
        ctk.CTkLabel(param_row, text="日　", font=ctk.CTkFont(size=13),
                      text_color="#c0c0c0").pack(side="left")
        self.long_entry = _add_int_entry(
            param_row, "長期", VM.DEFAULT_LONG_WINDOW)
        ctk.CTkLabel(param_row, text="日　", font=ctk.CTkFont(size=13),
                      text_color="#c0c0c0").pack(side="left")
        self.hold_entry = _add_int_entry(
            param_row, "持有", VM.DEFAULT_HOLD_DAYS)
        ctk.CTkLabel(param_row, text="日　", font=ctk.CTkFont(size=13),
                      text_color="#c0c0c0").pack(side="left")
        self.topn_entry = _add_int_entry(
            param_row, "主力前", VM.DEFAULT_TOP_N)
        ctk.CTkLabel(param_row, text="家", font=ctk.CTkFont(size=13),
                      text_color="#c0c0c0").pack(side="left")

        # Chip-dispersion filter row (大戶減 + 散戶增)
        chip_row = ctk.CTkFrame(run_card, fg_color="transparent")
        chip_row.pack(fill="x", padx=20, pady=(2, 6))
        self.chip_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            chip_row, text="籌碼過濾：大戶持股增加",
            variable=self.chip_var,
            font=ctk.CTkFont(size=13),
        ).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(chip_row, text="比較期",
                      font=ctk.CTkFont(size=13),
                      text_color="#c0c0c0").pack(side="left", padx=(0, 4))
        self.chip_weeks_entry = ctk.CTkEntry(
            chip_row, width=46, font=ctk.CTkFont(size=13), justify="center")
        self.chip_weeks_entry.pack(side="left")
        self.chip_weeks_entry.insert(0, "4")
        ctk.CTkLabel(chip_row, text="週　大戶 ≥ +",
                      font=ctk.CTkFont(size=13),
                      text_color="#c0c0c0").pack(side="left", padx=(2, 4))
        self.chip_big_entry = ctk.CTkEntry(
            chip_row, width=64, font=ctk.CTkFont(size=13), justify="center")
        self.chip_big_entry.pack(side="left")
        self.chip_big_entry.insert(0, "0.0")
        ctk.CTkLabel(chip_row, text="%（可填小數）　",
                      font=ctk.CTkFont(size=13),
                      text_color="#c0c0c0").pack(side="left")
        ctk.CTkLabel(
            chip_row,
            text="（未勾 → 不套用、依賴 TDCC 集保週報資料）",
            font=ctk.CTkFont(size=11), text_color="gray",
        ).pack(side="left")

        self.error_label = ctk.CTkLabel(
            run_card, text="", font=ctk.CTkFont(size=12),
            text_color="#FF6B6B")
        self.error_label.pack(padx=20, pady=(2, 0))

        btn_row = ctk.CTkFrame(run_card, fg_color="transparent")
        btn_row.pack(pady=(8, 16))
        self.run_btn = ctk.CTkButton(
            btn_row, text="開始回測", width=140, height=38,
            corner_radius=8, font=ctk.CTkFont(size=14, weight="bold"),
            command=self._on_run)
        self.run_btn.pack(side="left", padx=4)
        self.cancel_btn = ctk.CTkButton(
            btn_row, text="取消", width=80, height=38,
            corner_radius=8, font=ctk.CTkFont(size=13),
            fg_color="#666", hover_color="#888",
            command=self._on_cancel, state="disabled")
        self.cancel_btn.pack(side="left", padx=4)

        # =========================================================
        # 策略四：放空當沖回測
        # =========================================================
        desc4_card = ctk.CTkFrame(container, corner_radius=12)
        desc4_card.pack(padx=40, pady=8, fill="x")

        ctk.CTkLabel(
            desc4_card, text="策略四：放空當沖（黑K獵手）",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=20, pady=(14, 6))

        for line in [
            "範圍：上櫃股票（依「系統設定」的股票清單）",
            "進場條件：當日符合策略四篩選 — 主10<0、帶寬>20、月線下彎，"
            "可選乖離過濾（高位階 + 主力出貨 + 短中期偏弱 → 黑K機會）",
            "進場：訊號日收盤放空　・　出場：訊號日後第 N 個交易日收盤回補",
            "報酬 = (進場價 − 出場價) / 進場價 × 100%（正值 = 放空獲利）",
            "預設持有 1 日（隔日回補）；勝率/期望值與策略一同視窗顯示",
        ]:
            ctk.CTkLabel(
                desc4_card, text="• " + line,
                font=ctk.CTkFont(size=13), text_color="#c0c0c0",
                anchor="w", justify="left", wraplength=860,
            ).pack(anchor="w", padx=24, pady=1)

        ctk.CTkLabel(desc4_card, text="", height=4).pack()

        run4_card = ctk.CTkFrame(container, corner_radius=12)
        run4_card.pack(padx=40, pady=8, fill="x")

        # 日期 + 持有
        date4_row = ctk.CTkFrame(run4_card, fg_color="transparent")
        date4_row.pack(fill="x", padx=20, pady=(16, 6))
        ctk.CTkLabel(date4_row, text="回測區間：",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.sd_start_entry = ctk.CTkEntry(
            date4_row, width=120, font=ctk.CTkFont(size=14),
            placeholder_text="yyyy-mm-dd")
        self.sd_start_entry.pack(side="left", padx=(4, 4))
        ctk.CTkLabel(date4_row, text="~",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.sd_end_entry = ctk.CTkEntry(
            date4_row, width=120, font=ctk.CTkFont(size=14),
            placeholder_text="yyyy-mm-dd")
        self.sd_end_entry.pack(side="left", padx=(4, 12))
        sd, ed = default_date_range()
        self.sd_start_entry.insert(0, sd)
        self.sd_end_entry.insert(0, ed)
        self.sd_hold_entry = _add_int_entry(date4_row, "持有", 1)
        ctk.CTkLabel(date4_row, text="日",
                      font=ctk.CTkFont(size=13),
                      text_color="#c0c0c0").pack(side="left")

        # 硬性條件：主10 / 帶寬 / 月斜率 / 主力家數
        # 位階改以布林通道為基準（上軌=+10、中軌=0、下軌=-10），無需窗口
        p4a = ctk.CTkFrame(run4_card, fg_color="transparent")
        p4a.pack(fill="x", padx=20, pady=(2, 4))
        ctk.CTkLabel(p4a, text="條件：",
                      font=ctk.CTkFont(size=14)).pack(side="left", padx=(0, 8))
        ctk.CTkLabel(p4a, text="主10 <",
                      font=ctk.CTkFont(size=13),
                      text_color="#c0c0c0").pack(side="left", padx=(0, 4))
        self.sd_conc_entry = ctk.CTkEntry(
            p4a, width=52, font=ctk.CTkFont(size=14), justify="center")
        self.sd_conc_entry.pack(side="left")
        self.sd_conc_entry.insert(0, "0")
        ctk.CTkLabel(p4a, text="%　帶寬 >",
                      font=ctk.CTkFont(size=13),
                      text_color="#c0c0c0").pack(side="left", padx=(2, 4))
        self.sd_band_entry = ctk.CTkEntry(
            p4a, width=52, font=ctk.CTkFont(size=14), justify="center")
        self.sd_band_entry.pack(side="left")
        self.sd_band_entry.insert(0, "20")
        ctk.CTkLabel(p4a, text="%　月斜率 <",
                      font=ctk.CTkFont(size=13),
                      text_color="#c0c0c0").pack(side="left", padx=(2, 4))
        self.sd_slope_entry = ctk.CTkEntry(
            p4a, width=52, font=ctk.CTkFont(size=14), justify="center")
        self.sd_slope_entry.pack(side="left")
        self.sd_slope_entry.insert(0, "0")
        ctk.CTkLabel(p4a, text="%　主力前",
                      font=ctk.CTkFont(size=13),
                      text_color="#c0c0c0").pack(side="left", padx=(2, 4))
        self.sd_topn_entry = ctk.CTkEntry(
            p4a, width=52, font=ctk.CTkFont(size=14), justify="center")
        self.sd_topn_entry.pack(side="left")
        self.sd_topn_entry.insert(0, "15")
        ctk.CTkLabel(p4a, text="家",
                      font=ctk.CTkFont(size=13),
                      text_color="#c0c0c0").pack(side="left", padx=(2, 0))

        # 乖離過濾 row 1: 年線
        p4b = ctk.CTkFrame(run4_card, fg_color="transparent")
        p4b.pack(fill="x", padx=20, pady=(2, 4))
        ctk.CTkLabel(p4b, text="乖離：",
                      font=ctk.CTkFont(size=14)).pack(side="left", padx=(0, 8))
        self.sd_bias_use_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            p4b, text="年線 ≥", variable=self.sd_bias_use_var,
            font=ctk.CTkFont(size=13),
        ).pack(side="left")
        self.sd_bias_entry = ctk.CTkEntry(
            p4b, width=52, font=ctk.CTkFont(size=14), justify="center")
        self.sd_bias_entry.pack(side="left", padx=(6, 0))
        self.sd_bias_entry.insert(0, "10")
        ctk.CTkLabel(p4b, text="%　", font=ctk.CTkFont(size=13),
                      text_color="#c0c0c0").pack(side="left")
        self.sd_b6_use_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            p4b, text="周 ≤", variable=self.sd_b6_use_var,
            font=ctk.CTkFont(size=13),
        ).pack(side="left")
        self.sd_b6_entry = ctk.CTkEntry(
            p4b, width=52, font=ctk.CTkFont(size=14), justify="center")
        self.sd_b6_entry.pack(side="left", padx=(6, 0))
        self.sd_b6_entry.insert(0, "-3")
        ctk.CTkLabel(p4b, text="%　", font=ctk.CTkFont(size=13),
                      text_color="#c0c0c0").pack(side="left")
        self.sd_b12_use_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            p4b, text="雙週 ≤", variable=self.sd_b12_use_var,
            font=ctk.CTkFont(size=13),
        ).pack(side="left")
        self.sd_b12_entry = ctk.CTkEntry(
            p4b, width=52, font=ctk.CTkFont(size=14), justify="center")
        self.sd_b12_entry.pack(side="left", padx=(6, 0))
        self.sd_b12_entry.insert(0, "-4.5")
        ctk.CTkLabel(p4b, text="%　", font=ctk.CTkFont(size=13),
                      text_color="#c0c0c0").pack(side="left")
        self.sd_b20_use_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            p4b, text="月 ≤", variable=self.sd_b20_use_var,
            font=ctk.CTkFont(size=13),
        ).pack(side="left")
        self.sd_b20_entry = ctk.CTkEntry(
            p4b, width=52, font=ctk.CTkFont(size=14), justify="center")
        self.sd_b20_entry.pack(side="left", padx=(6, 0))
        self.sd_b20_entry.insert(0, "-7")
        ctk.CTkLabel(p4b, text="%　", font=ctk.CTkFont(size=13),
                      text_color="#c0c0c0").pack(side="left")
        self.sd_b72_use_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            p4b, text="季 ≤", variable=self.sd_b72_use_var,
            font=ctk.CTkFont(size=13),
        ).pack(side="left")
        self.sd_b72_entry = ctk.CTkEntry(
            p4b, width=52, font=ctk.CTkFont(size=14), justify="center")
        self.sd_b72_entry.pack(side="left", padx=(6, 0))
        self.sd_b72_entry.insert(0, "-11")
        ctk.CTkLabel(p4b, text="%", font=ctk.CTkFont(size=13),
                      text_color="#c0c0c0").pack(side="left")

        self.sd_error_label = ctk.CTkLabel(
            run4_card, text="", font=ctk.CTkFont(size=12),
            text_color="#FF6B6B")
        self.sd_error_label.pack(padx=20, pady=(2, 0))

        btn4_row = ctk.CTkFrame(run4_card, fg_color="transparent")
        btn4_row.pack(pady=(8, 16))
        self.sd_run_btn = ctk.CTkButton(
            btn4_row, text="開始策略四回測", width=160, height=38,
            corner_radius=8, font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#26a69a", hover_color="#00897b",
            command=self._on_run_short)
        self.sd_run_btn.pack(side="left", padx=4)

        # --- Progress card ---
        prog_card = ctk.CTkFrame(container, corner_radius=12)
        prog_card.pack(padx=40, pady=8, fill="x")
        prog_top = ctk.CTkFrame(prog_card, fg_color="transparent")
        prog_top.pack(fill="x", padx=20, pady=(14, 4))
        self.status_label = ctk.CTkLabel(
            prog_top, text="就緒", font=ctk.CTkFont(size=12),
            text_color="gray")
        self.status_label.pack(side="left")
        self.progress_label = ctk.CTkLabel(
            prog_top, text="", font=ctk.CTkFont(size=12, weight="bold"))
        self.progress_label.pack(side="right")
        self.progress_bar = ctk.CTkProgressBar(prog_card, width=400)
        self.progress_bar.pack(padx=20, pady=(0, 14))
        self.progress_bar.set(0)

        # --- KPI summary card ---
        self.summary_card = ctk.CTkFrame(container, corner_radius=12)
        self.summary_card.pack(padx=40, pady=8, fill="x")
        ctk.CTkLabel(
            self.summary_card, text="績效摘要",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(anchor="w", padx=20, pady=(14, 4))
        self.kpi_frame = ctk.CTkFrame(self.summary_card, fg_color="transparent")
        self.kpi_frame.pack(fill="x", padx=12, pady=(0, 12))
        ctk.CTkLabel(
            self.kpi_frame, text="（尚未執行）",
            font=ctk.CTkFont(size=13), text_color="gray").pack(pady=8)

        # --- Signals table ---
        tbl_card = ctk.CTkFrame(container, corner_radius=12)
        tbl_card.pack(padx=40, pady=8, fill="both", expand=True)
        hdr = ctk.CTkFrame(tbl_card, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(14, 4))
        ctk.CTkLabel(
            hdr, text="訊號明細",
            font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        self.signal_count_label = ctk.CTkLabel(
            hdr, text="", font=ctk.CTkFont(size=13), text_color="gray")
        self.signal_count_label.pack(side="left", padx=(8, 0))

        self._ensure_tree_style()
        self.tree_f = ctk.CTkFrame(tbl_card, fg_color="transparent")
        self.tree_f.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self.tree: ttk.Treeview | None = None
        self._build_tree("breakout")

        # --- Log card ---
        log_card = ctk.CTkFrame(container, corner_radius=12)
        log_card.pack(padx=40, pady=8, fill="x")
        ctk.CTkLabel(
            log_card, text="執行紀錄",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(12, 4))
        self.log_textbox = ctk.CTkTextbox(
            log_card, height=140,
            font=ctk.CTkFont(size=12, family="Consolas"),
            state="disabled")
        self.log_textbox.pack(fill="x", padx=16, pady=(0, 12))

    def _build_tree(self, kind: str):
        """重建訊號表（依策略類型套不同欄位）。"""
        for w in self.tree_f.winfo_children():
            w.destroy()

        if kind == "short":
            columns_spec = [
                ("date",   "訊號日",  90, "center"),
                ("exit",   "回補日",  90, "center"),
                ("code",   "代碼",    56, "center"),
                ("name",   "名稱",    90, "w"),
                ("pos",    "位階",    52, "e"),
                ("c10",    "主10%",   60, "e"),
                ("slope",  "月斜%",   58, "e"),
                ("b20",    "月乖%",   58, "e"),
                ("b250",   "年乖%",   58, "e"),
                ("entry",  "放空價",  70, "e"),
                ("exit_p", "回補價",  70, "e"),
                ("ret",    "報酬%",   72, "e"),
            ]
        else:  # breakout
            columns_spec = [
                ("date",     "訊號日",   90, "center"),
                ("exit",     "出場日",   90, "center"),
                ("code",     "代碼",     56, "center"),
                ("name",     "名稱",     90, "w"),
                ("conc_s",   "短期%",    62, "e"),
                ("conc_l",   "長期%",    62, "e"),
                ("entry",    "進場價",   70, "e"),
                ("exit_p",   "出場價",   70, "e"),
                ("ret",      "報酬%",    70, "e"),
                ("big_d",    "大戶Δ%",   62, "e"),
                ("retail_d", "散戶Δ%",   62, "e"),
            ]

        cols = tuple(c[0] for c in columns_spec)
        tree = ttk.Treeview(
            self.tree_f, columns=cols, show="headings",
            style="StratEval.Treeview", height=14)
        for c, txt, w, anc in columns_spec:
            tree.heading(c, text=txt)
            tree.column(c, width=w, anchor=anc, stretch=True)
        tree.tag_configure("win", foreground="#ef5350")
        tree.tag_configure("loss", foreground="#26a69a")

        sb = ttk.Scrollbar(self.tree_f, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=2)
        sb.pack(side="right", fill="y", padx=(0, 4), pady=2)

        self.tree = tree
        self._table_kind = kind

    def _ensure_tree_style(self):
        s = ttk.Style()
        name = "StratEval.Treeview"
        s.configure(name,
                    background="#252526", foreground="#d4d4d4",
                    fieldbackground="#252526", borderwidth=0,
                    rowheight=26, font=("Microsoft JhengHei", 11))
        s.map(name,
              background=[("selected", "#264f78")],
              foreground=[("selected", "#ffffff")])
        s.configure(f"{name}.Heading",
                    background="#2d2d2d", foreground="#cccccc",
                    borderwidth=0, relief="flat",
                    font=("Microsoft JhengHei", 11, "bold"))
        s.map(f"{name}.Heading",
              background=[("active", "#3e3e3e")])

    # ------------------------------------------------------------ Events

    def _on_run(self):
        self.vm.start_eval(
            self.start_entry.get(), self.end_entry.get(),
            short_window=self.short_entry.get(),
            long_window=self.long_entry.get(),
            hold_days=self.hold_entry.get(),
            top_n=self.topn_entry.get(),
            chip_filter=bool(self.chip_var.get()),
            chip_weeks=self.chip_weeks_entry.get(),
            chip_big_gain=self.chip_big_entry.get(),
        )

    def _on_run_short(self):
        self.vm.start_short_eval(
            self.sd_start_entry.get(), self.sd_end_entry.get(),
            hold_days=self.sd_hold_entry.get(),
            conc_max=self.sd_conc_entry.get(),
            band_min=self.sd_band_entry.get(),
            slope_max=self.sd_slope_entry.get(),
            bias_min=self.sd_bias_entry.get(),
            top_n=self.sd_topn_entry.get(),
            bias6_max=self.sd_b6_entry.get(),
            bias12_max=self.sd_b12_entry.get(),
            bias20_max=self.sd_b20_entry.get(),
            bias72_max=self.sd_b72_entry.get(),
            use_bias_min=bool(self.sd_bias_use_var.get()),
            use_bias6=bool(self.sd_b6_use_var.get()),
            use_bias12=bool(self.sd_b12_use_var.get()),
            use_bias20=bool(self.sd_b20_use_var.get()),
            use_bias72=bool(self.sd_b72_use_var.get()),
        )

    def _on_cancel(self):
        self.vm.cancel()

    # ---------------------------------------------------------- Bindings

    def _bind_vm(self):
        self.vm.bind("status_text", self._on_status)
        self.vm.bind("progress", self._on_progress)
        self.vm.bind("progress_text", self._on_progress_text)
        self.vm.bind("is_running", self._on_running)
        self.vm.bind("log_text", self._on_log)
        self.vm.bind("error_text", self._on_error)
        self.vm.bind("signals_data", self._on_signals)
        self.vm.bind("summary_data", self._on_summary)
        self.vm.bind("eval_kind", self._on_eval_kind)

    def _on_eval_kind(self, kind: str):
        def _u():
            if kind != self._table_kind:
                self._build_tree(kind)
        self.after(0, _u)

    def _on_status(self, v: str):
        self.after(0, lambda: self.status_label.configure(text=v))

    def _on_progress(self, v: float):
        self.after(0, lambda: self.progress_bar.set(v))

    def _on_progress_text(self, v: str):
        self.after(0, lambda: self.progress_label.configure(text=v))

    def _on_running(self, v: bool):
        def _u():
            if v:
                self.run_btn.configure(state="disabled", text="回測中...")
                self.sd_run_btn.configure(state="disabled", text="回測中...")
                self.cancel_btn.configure(state="normal")
            else:
                self.run_btn.configure(state="normal", text="開始回測")
                self.sd_run_btn.configure(state="normal",
                                          text="開始策略四回測")
                self.cancel_btn.configure(state="disabled")
        self.after(0, _u)

    def _on_log(self, v: str):
        def _u():
            self.log_textbox.configure(state="normal")
            self.log_textbox.delete("1.0", "end")
            self.log_textbox.insert("1.0", v or "")
            self.log_textbox.see("end")
            self.log_textbox.configure(state="disabled")
        self.after(0, _u)

    def _on_error(self, v: str):
        def _u():
            # 同步顯示在兩張卡片上的 error label，避免使用者錯位看不到
            self.error_label.configure(text=v)
            self.sd_error_label.configure(text=v)
        self.after(0, _u)

    def _on_summary(self, data):
        self.after(0, lambda: self._render_summary(data))

    def _on_signals(self, data):
        self.after(0, lambda: self._render_signals(data))

    # ---------------------------------------------------------- Render

    def _render_summary(self, data: dict | None):
        for w in self.kpi_frame.winfo_children():
            w.destroy()
        if not data or data.get("count", 0) == 0:
            ctk.CTkLabel(
                self.kpi_frame, text="（無訊號或尚未執行）",
                font=ctk.CTkFont(size=13), text_color="gray").pack(pady=8)
            return

        def _clr(v): return "#ef5350" if v >= 0 else "#26a69a"

        # 兩排 KPI
        row1 = ctk.CTkFrame(self.kpi_frame, fg_color="transparent")
        row1.pack(fill="x", pady=2)
        row2 = ctk.CTkFrame(self.kpi_frame, fg_color="transparent")
        row2.pack(fill="x", pady=2)

        items1 = [
            ("訊號數", f"{data['count']}", "#d4d4d4"),
            ("勝率", f"{data['win_rate']}%",
             "#ef5350" if data["win_rate"] >= 50 else "#26a69a"),
            ("平均報酬", f"{data['avg_return']:+.2f}%",
             _clr(data["avg_return"])),
            ("中位數報酬", f"{data['median_return']:+.2f}%",
             _clr(data["median_return"])),
            ("期望值", f"{data['expectancy']:+.2f}%",
             _clr(data["expectancy"])),
        ]
        items2 = [
            ("平均勝幅", f"{data['avg_win']:+.2f}%", "#ef5350"),
            ("平均敗幅", f"{data['avg_loss']:+.2f}%", "#26a69a"),
            ("最佳", f"{data['best']:+.2f}%", "#ef5350"),
            ("最差", f"{data['worst']:+.2f}%", "#26a69a"),
            ("累計報酬", f"{data['total_return']:+.2f}%",
             _clr(data["total_return"])),
        ]
        for parent, items in [(row1, items1), (row2, items2)]:
            for label, value, color in items:
                f = ctk.CTkFrame(parent, fg_color="#1e1e1e", corner_radius=8)
                f.pack(side="left", padx=4, pady=2, fill="x", expand=True)
                ctk.CTkLabel(f, text=label, font=ctk.CTkFont(size=12),
                              text_color="gray").pack(padx=10, pady=(6, 0))
                ctk.CTkLabel(f, text=value,
                              font=ctk.CTkFont(size=17, weight="bold"),
                              text_color=color).pack(padx=10, pady=(0, 6))

    def _render_signals(self, signals: list | None):
        # 如果 signals 與目前表格類型不符，先重建（兜底，正常會由 eval_kind 觸發）
        if signals:
            needed = "short" if "rank_pos" in signals[0] else "breakout"
            if needed != self._table_kind:
                self._build_tree(needed)

        if self.tree is None:
            return
        self.tree.delete(*self.tree.get_children())
        if not signals:
            self.signal_count_label.configure(text="")
            return
        self.signal_count_label.configure(text=f"（共 {len(signals)} 筆）")

        def _delta(v):
            return f"{v:+.2f}" if v is not None else "—"

        if self._table_kind == "short":
            for s in signals:
                ret = s["return_pct"]
                tag = "win" if ret > 0 else "loss"
                self.tree.insert(
                    "", "end",
                    values=(
                        s["signal_date"], s["exit_date"],
                        s["stock_code"], s["stock_name"],
                        f"{s['rank_pos']:+.2f}",
                        f"{s['conc_10']:+.2f}",
                        f"{s['ma20_slope']:+.2f}",
                        _delta(s.get("ma20_bias")),
                        _delta(s.get("ma250_bias")),
                        f"{s['entry_price']:.2f}",
                        f"{s['exit_price']:.2f}",
                        f"{ret:+.2f}",
                    ),
                    tags=(tag,),
                )
        else:
            for s in signals:
                ret = s["return_pct"]
                tag = "win" if ret > 0 else "loss"
                self.tree.insert(
                    "", "end",
                    values=(
                        s["signal_date"], s["exit_date"],
                        s["stock_code"], s["stock_name"],
                        f"{s['conc_short']:+.2f}",
                        f"{s['conc_long']:+.2f}",
                        f"{s['entry_price']:.2f}",
                        f"{s['exit_price']:.2f}",
                        f"{ret:+.2f}",
                        _delta(s.get("chip_big_delta")),
                        _delta(s.get("chip_retail_delta")),
                    ),
                    tags=(tag,),
                )
