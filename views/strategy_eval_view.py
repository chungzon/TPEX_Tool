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
            "進場條件：短期、長期集中度皆 > 0，且短期集中度上穿長期集中度"
            "（黃金交叉）",
            "進場價：訊號日收盤　・　出場：訊號日後第 N 個交易日收盤",
            "集中度 = (買超前 K 家張數 − 賣超前 K 家張數) ÷ 區間成交量",
            "預設：短期 5 日、長期 15 日、持有 4 日、主力 15 家 "
            "（皆可下方自訂）",
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
        tree_f = ctk.CTkFrame(tbl_card, fg_color="transparent")
        tree_f.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        columns = ("date", "exit", "code", "name",
                    "conc_s", "conc_l", "entry", "exit_p", "ret")
        self.tree = ttk.Treeview(
            tree_f, columns=columns, show="headings",
            style="StratEval.Treeview", height=14)
        for c, txt, w, anc in [
            ("date",   "訊號日",   90, "center"),
            ("exit",   "出場日",   90, "center"),
            ("code",   "代碼",     56, "center"),
            ("name",   "名稱",     90, "w"),
            ("conc_s", "短期%",    65, "e"),
            ("conc_l", "長期%",    65, "e"),
            ("entry",  "進場價",   72, "e"),
            ("exit_p", "出場價",   72, "e"),
            ("ret",    "報酬%",    72, "e"),
        ]:
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=w, anchor=anc, stretch=True)
        self.tree.tag_configure("win", foreground="#ef5350")  # 紅=漲
        self.tree.tag_configure("loss", foreground="#26a69a")  # 綠=跌

        sb = ttk.Scrollbar(tree_f, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(4, 0),
                        pady=2)
        sb.pack(side="right", fill="y", padx=(0, 4), pady=2)

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
                self.cancel_btn.configure(state="normal")
            else:
                self.run_btn.configure(state="normal", text="開始回測")
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
        self.after(0, lambda: self.error_label.configure(text=v))

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
        self.tree.delete(*self.tree.get_children())
        if not signals:
            self.signal_count_label.configure(text="")
            return
        self.signal_count_label.configure(text=f"（共 {len(signals)} 筆）")
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
                ),
                tags=(tag,),
            )
