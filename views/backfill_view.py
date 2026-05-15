"""補資料 — 下載指定日期的上市/上櫃每日行情與三大法人，補齊漏跑的日子。"""

from __future__ import annotations

from datetime import datetime, timedelta

import customtkinter as ctk

from viewmodels.backfill_viewmodel import BackfillViewModel


class BackfillView(ctk.CTkFrame):
    """補資料 tab page。"""

    def __init__(self, parent: ctk.CTkFrame, viewmodel: BackfillViewModel):
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
            container, text="補資料",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(pady=(24, 8))
        ctk.CTkLabel(
            container,
            text="排程漏跑某天時，補抓指定日期的每日行情與三大法人資料",
            font=ctk.CTkFont(size=13), text_color="gray",
        ).pack(pady=(0, 4))
        ctk.CTkLabel(
            container,
            text="※ 分點明細（券商買賣超）無法補抓 —— "
                 "TWSE/TPEX 僅提供最近一個交易日",
            font=ctk.CTkFont(size=12), text_color="#e8a13a",
        ).pack(pady=(0, 20))

        # --- Input card ---
        card = ctk.CTkFrame(container, corner_radius=12)
        card.pack(padx=40, pady=8, fill="x")

        # Date row
        date_row = ctk.CTkFrame(card, fg_color="transparent")
        date_row.pack(fill="x", padx=20, pady=(18, 6))
        ctk.CTkLabel(date_row, text="補資料日期：",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.date_entry = ctk.CTkEntry(
            date_row, width=140, font=ctk.CTkFont(size=14),
            placeholder_text="yyyy-mm-dd")
        self.date_entry.pack(side="left", padx=(4, 8))
        # 預設為前一個工作日
        yesterday = datetime.now() - timedelta(days=1)
        self.date_entry.insert(0, yesterday.strftime("%Y-%m-%d"))
        ctk.CTkLabel(
            date_row, text="（單一交易日）",
            font=ctk.CTkFont(size=12), text_color="gray",
        ).pack(side="left")

        # Market checkboxes
        mkt_row = ctk.CTkFrame(card, fg_color="transparent")
        mkt_row.pack(fill="x", padx=20, pady=(6, 4))
        ctk.CTkLabel(mkt_row, text="市場：",
                      font=ctk.CTkFont(size=14)).pack(side="left", padx=(0, 8))
        self.otc_var = ctk.BooleanVar(value=True)
        self.twse_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(mkt_row, text="上櫃 (TPEX)", variable=self.otc_var,
                         font=ctk.CTkFont(size=13)).pack(side="left", padx=4)
        ctk.CTkCheckBox(mkt_row, text="上市 (TWSE)", variable=self.twse_var,
                         font=ctk.CTkFont(size=13)).pack(side="left", padx=4)

        # Data-kind checkboxes
        kind_row = ctk.CTkFrame(card, fg_color="transparent")
        kind_row.pack(fill="x", padx=20, pady=(4, 4))
        ctk.CTkLabel(kind_row, text="資料：",
                      font=ctk.CTkFont(size=14)).pack(side="left", padx=(0, 8))
        self.market_var = ctk.BooleanVar(value=True)
        self.insti_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(kind_row, text="每日行情（開高低收／量）",
                         variable=self.market_var,
                         font=ctk.CTkFont(size=13)).pack(side="left", padx=4)
        ctk.CTkCheckBox(kind_row, text="三大法人",
                         variable=self.insti_var,
                         font=ctk.CTkFont(size=13)).pack(side="left", padx=4)

        # Error label
        self.error_label = ctk.CTkLabel(
            card, text="", font=ctk.CTkFont(size=12), text_color="#FF6B6B")
        self.error_label.pack(padx=20, pady=(4, 0))

        # Run button
        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(pady=(8, 20))
        self.run_btn = ctk.CTkButton(
            btn_row, text="開始補資料", width=160, height=38,
            corner_radius=8, font=ctk.CTkFont(size=14, weight="bold"),
            command=self._on_run)
        self.run_btn.pack()

        # --- Progress card ---
        prog_card = ctk.CTkFrame(container, corner_radius=12)
        prog_card.pack(padx=40, pady=8, fill="x")

        prog_top = ctk.CTkFrame(prog_card, fg_color="transparent")
        prog_top.pack(fill="x", padx=20, pady=(16, 4))
        self.status_label = ctk.CTkLabel(
            prog_top, text="就緒", font=ctk.CTkFont(size=12),
            text_color="gray")
        self.status_label.pack(side="left")
        self.progress_label = ctk.CTkLabel(
            prog_top, text="", font=ctk.CTkFont(size=12, weight="bold"))
        self.progress_label.pack(side="right")

        self.progress_bar = ctk.CTkProgressBar(prog_card, width=400)
        self.progress_bar.pack(padx=20, pady=(0, 16))
        self.progress_bar.set(0)

        # --- Log card ---
        log_card = ctk.CTkFrame(container, corner_radius=12)
        log_card.pack(padx=40, pady=8, fill="both", expand=True)
        ctk.CTkLabel(
            log_card, text="補資料記錄",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(12, 4))
        self.log_textbox = ctk.CTkTextbox(
            log_card, height=240,
            font=ctk.CTkFont(size=12, family="Consolas"),
            state="disabled")
        self.log_textbox.pack(fill="both", expand=True, padx=16, pady=(0, 12))

    # ------------------------------------------------------------ Events

    def _on_run(self):
        self.vm.start_backfill(
            self.date_entry.get(),
            do_otc=self.otc_var.get(),
            do_twse=self.twse_var.get(),
            do_market=self.market_var.get(),
            do_insti=self.insti_var.get(),
        )

    # ---------------------------------------------------------- Bindings

    def _bind_vm(self):
        self.vm.bind("status_text", self._on_status)
        self.vm.bind("progress", self._on_progress)
        self.vm.bind("progress_text", self._on_progress_text)
        self.vm.bind("is_running", self._on_running)
        self.vm.bind("log_text", self._on_log)
        self.vm.bind("error_text", self._on_error)

    def _on_status(self, v: str):
        self.after(0, lambda: self.status_label.configure(text=v))

    def _on_progress(self, v: float):
        self.after(0, lambda: self.progress_bar.set(v))

    def _on_progress_text(self, v: str):
        self.after(0, lambda: self.progress_label.configure(text=v))

    def _on_running(self, v: bool):
        def _update():
            if v:
                self.run_btn.configure(state="disabled", text="補資料中...")
            else:
                self.run_btn.configure(state="normal", text="開始補資料")
        self.after(0, _update)

    def _on_log(self, v: str):
        def _update():
            self.log_textbox.configure(state="normal")
            self.log_textbox.delete("1.0", "end")
            self.log_textbox.insert("1.0", v or "")
            self.log_textbox.see("end")
            self.log_textbox.configure(state="disabled")
        self.after(0, _update)

    def _on_error(self, v: str):
        self.after(0, lambda: self.error_label.configure(text=v))
