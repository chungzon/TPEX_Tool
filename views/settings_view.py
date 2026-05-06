"""系統設定 tab — scheduler configuration with live progress."""

from __future__ import annotations

import customtkinter as ctk

from viewmodels.settings_viewmodel import SettingsViewModel


class SettingsView(ctk.CTkFrame):
    """系統設定 tab page."""

    def __init__(self, parent: ctk.CTkFrame, viewmodel: SettingsViewModel):
        super().__init__(parent, fg_color="transparent")
        self.vm = viewmodel
        self._refresh_id: str | None = None
        self._build_ui()
        self._bind_vm()
        self._start_refresh_loop()

    def _build_ui(self):
        container = ctk.CTkScrollableFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True)

        # --- Title ---
        ctk.CTkLabel(
            container, text="系統設定",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(pady=(24, 8))
        ctk.CTkLabel(
            container, text="排程自動下載與應用程式設定",
            font=ctk.CTkFont(size=13), text_color="gray",
        ).pack(pady=(0, 24))

        # ============================================================
        # Shioaji API Usage card
        # ============================================================
        usage_card = ctk.CTkFrame(container, corner_radius=12)
        usage_card.pack(padx=40, pady=8, fill="x")

        ctk.CTkLabel(
            usage_card, text="永豐金 API 流量資訊",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=24, pady=(20, 8))

        self.usage_frame = ctk.CTkFrame(usage_card, fg_color="transparent")
        self.usage_frame.pack(fill="x", padx=24, pady=(0, 16))

        self.usage_not_login = ctk.CTkLabel(
            self.usage_frame, text="（請先在「下單」分頁登入永豐金帳號）",
            font=ctk.CTkFont(size=13), text_color="gray")
        self.usage_not_login.pack(anchor="w")

        # ============================================================
        # Stock list card
        # ============================================================
        list_card = ctk.CTkFrame(container, corner_radius=12)
        list_card.pack(padx=40, pady=8, fill="x")

        ctk.CTkLabel(
            list_card, text="下載股票清單",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=24, pady=(20, 4))

        ctk.CTkLabel(
            list_card,
            text="從 TPEX 取得交易量前 N 名，存入設定檔，之後排程和手動下載都用這份清單",
            font=ctk.CTkFont(size=11), text_color="gray",
        ).pack(anchor="w", padx=24, pady=(0, 10))

        # Top N row
        topn_row = ctk.CTkFrame(list_card, fg_color="transparent")
        topn_row.pack(fill="x", padx=24, pady=4)
        ctk.CTkLabel(topn_row, text="取前",
                      font=ctk.CTkFont(size=13)).pack(side="left")
        self.topn_entry = ctk.CTkEntry(
            topn_row, width=70, font=ctk.CTkFont(size=13))
        self.topn_entry.pack(side="left", padx=(8, 4))
        self.topn_entry.insert(0, str(self.vm.scheduler_top_n))
        ctk.CTkLabel(topn_row, text="名",
                      font=ctk.CTkFont(size=13)).pack(side="left")

        ctk.CTkButton(
            topn_row, text="套用", width=50, height=28,
            corner_radius=6, font=ctk.CTkFont(size=12),
            command=self._on_save_topn,
        ).pack(side="left", padx=(12, 0))

        # Current list info + refresh button
        info_row = ctk.CTkFrame(list_card, fg_color="transparent")
        info_row.pack(fill="x", padx=24, pady=(8, 4))

        ctk.CTkLabel(info_row, text="目前清單：",
                      font=ctk.CTkFont(size=12),
                      text_color="gray").pack(side="left")
        self.list_info_label = ctk.CTkLabel(
            info_row, text=self.vm.stock_list_info,
            font=ctk.CTkFont(size=12, weight="bold"))
        self.list_info_label.pack(side="left")

        btn_row = ctk.CTkFrame(list_card, fg_color="transparent")
        btn_row.pack(fill="x", padx=24, pady=(4, 16))

        self.refresh_list_btn = ctk.CTkButton(
            btn_row, text="更新清單（從 TPEX 取得今日前 N 名）",
            width=320, height=36, corner_radius=8,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#1f6aa5", hover_color="#185a8c",
            command=self._on_refresh_list,
        )
        self.refresh_list_btn.pack(side="left")

        # ============================================================
        # TDCC holder distribution card
        # ============================================================
        tdcc_card = ctk.CTkFrame(container, corner_radius=12)
        tdcc_card.pack(padx=40, pady=8, fill="x")

        ctk.CTkLabel(
            tdcc_card, text="集保戶股權分散表（TDCC）",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=24, pady=(20, 4))

        ctk.CTkLabel(
            tdcc_card,
            text="下載所有清單股票的大戶/散戶持股比例，資料來源：台灣集保結算所（每週五更新）",
            font=ctk.CTkFont(size=11), text_color="gray",
        ).pack(anchor="w", padx=24, pady=(0, 10))

        tdcc_btn_row = ctk.CTkFrame(tdcc_card, fg_color="transparent")
        tdcc_btn_row.pack(fill="x", padx=24, pady=(0, 4))

        self.tdcc_btn = ctk.CTkButton(
            tdcc_btn_row, text="下載集保資料（全部股票）",
            width=280, height=36, corner_radius=8,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#7b61ff", hover_color="#6344e0",
            command=self._on_download_tdcc,
        )
        self.tdcc_btn.pack(side="left")

        tdcc_status_row = ctk.CTkFrame(tdcc_card, fg_color="transparent")
        tdcc_status_row.pack(fill="x", padx=24, pady=(4, 16))

        ctk.CTkLabel(tdcc_status_row, text="狀態：",
                      font=ctk.CTkFont(size=12),
                      text_color="gray").pack(side="left")
        self.tdcc_status_label = ctk.CTkLabel(
            tdcc_status_row, text="—",
            font=ctk.CTkFont(size=12), text_color="#b0b0b0")
        self.tdcc_status_label.pack(side="left")

        # ============================================================
        # Institutional (三大法人) card
        # ============================================================
        insti_card = ctk.CTkFrame(container, corner_radius=12)
        insti_card.pack(padx=40, pady=8, fill="x")

        ctk.CTkLabel(
            insti_card, text="三大法人買賣超（含自營商避險）",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=24, pady=(20, 4))

        ctk.CTkLabel(
            insti_card,
            text="下載外資、投信、自營商（自行買賣＋避險）每日買賣超，資料來源：櫃買中心",
            font=ctk.CTkFont(size=11), text_color="gray",
        ).pack(anchor="w", padx=24, pady=(0, 10))

        insti_btn_row = ctk.CTkFrame(insti_card, fg_color="transparent")
        insti_btn_row.pack(fill="x", padx=24, pady=(0, 4))

        self.insti_btn = ctk.CTkButton(
            insti_btn_row, text="下載三大法人資料（近 5 個交易日）",
            width=310, height=36, corner_radius=8,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#26a69a", hover_color="#1e8c82",
            command=self._on_download_insti,
        )
        self.insti_btn.pack(side="left")

        insti_st_row = ctk.CTkFrame(insti_card, fg_color="transparent")
        insti_st_row.pack(fill="x", padx=24, pady=(4, 16))
        ctk.CTkLabel(insti_st_row, text="狀態：",
                      font=ctk.CTkFont(size=12),
                      text_color="gray").pack(side="left")
        self.insti_status_label = ctk.CTkLabel(
            insti_st_row, text="—",
            font=ctk.CTkFont(size=12), text_color="#b0b0b0")
        self.insti_status_label.pack(side="left")

        # ============================================================
        # Scheduler card
        # ============================================================
        card = ctk.CTkFrame(container, corner_radius=12)
        card.pack(padx=40, pady=8, fill="x")

        ctk.CTkLabel(
            card, text="每日自動下載排程",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=24, pady=(20, 12))

        # Enable switch
        switch_row = ctk.CTkFrame(card, fg_color="transparent")
        switch_row.pack(fill="x", padx=24, pady=6)
        ctk.CTkLabel(switch_row, text="啟用排程",
                      font=ctk.CTkFont(size=13)).pack(side="left")
        self.sched_switch = ctk.CTkSwitch(
            switch_row, text="", command=self._on_toggle,
            onvalue=True, offvalue=False)
        self.sched_switch.pack(side="left", padx=(12, 0))
        if self.vm.scheduler_enabled:
            self.sched_switch.select()

        # Time
        time_row = ctk.CTkFrame(card, fg_color="transparent")
        time_row.pack(fill="x", padx=24, pady=6)
        ctk.CTkLabel(time_row, text="執行時間",
                      font=ctk.CTkFont(size=13)).pack(side="left")
        self.time_entry = ctk.CTkEntry(
            time_row, width=80, font=ctk.CTkFont(size=13),
            placeholder_text="18:00")
        self.time_entry.pack(side="left", padx=(12, 8))
        self.time_entry.insert(0, self.vm.scheduler_time)
        ctk.CTkButton(
            time_row, text="套用", width=60, height=28,
            corner_radius=6, font=ctk.CTkFont(size=12),
            command=self._on_save_time).pack(side="left")

        # Run now button
        run_row = ctk.CTkFrame(card, fg_color="transparent")
        run_row.pack(fill="x", padx=24, pady=(10, 4))
        self.run_now_btn = ctk.CTkButton(
            run_row, text="立即下載（使用目前清單）",
            width=260, height=38, corner_radius=8,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#e88a1a", hover_color="#c97616",
            command=self._on_run_now,
        )
        self.run_now_btn.pack(pady=4)

        ctk.CTkFrame(card, height=1, fg_color="#3a3a3a").pack(
            fill="x", padx=24, pady=(12, 12))

        # --- Status area ---
        ctk.CTkLabel(card, text="排程狀態",
                      font=ctk.CTkFont(size=14, weight="bold"),
                      ).pack(anchor="w", padx=24, pady=(0, 4))

        status_grid = ctk.CTkFrame(card, fg_color="transparent")
        status_grid.pack(fill="x", padx=24, pady=(0, 4))

        for label_text, attr in [
            ("下次執行：", "next_run_label"),
            ("上次結果：", "last_result_label"),
            ("目前狀態：", "status_label"),
        ]:
            row = ctk.CTkFrame(status_grid, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(row, text=label_text, font=ctk.CTkFont(size=12),
                          text_color="gray").pack(side="left")
            lbl = ctk.CTkLabel(row, text="—", font=ctk.CTkFont(size=12))
            lbl.pack(side="left")
            setattr(self, attr, lbl)

        self.status_label.configure(text_color="#4ECDC4")

        ctk.CTkFrame(card, height=12, fg_color="transparent").pack()

        # ============================================================
        # Live progress card (visible during download)
        # ============================================================
        self.progress_card = ctk.CTkFrame(container, corner_radius=12)

        ctk.CTkLabel(
            self.progress_card, text="下載進度",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=20, pady=(16, 4))

        prog_row = ctk.CTkFrame(self.progress_card, fg_color="transparent")
        prog_row.pack(fill="x", padx=20, pady=(0, 4))

        self.dl_status_label = ctk.CTkLabel(
            prog_row, text="", font=ctk.CTkFont(size=12), text_color="gray")
        self.dl_status_label.pack(side="left")

        self.progress_label = ctk.CTkLabel(
            prog_row, text="", font=ctk.CTkFont(size=12, weight="bold"))
        self.progress_label.pack(side="right")

        self.progress_bar = ctk.CTkProgressBar(self.progress_card, width=400)
        self.progress_bar.pack(padx=20, pady=(0, 8))
        self.progress_bar.set(0)

        ctk.CTkLabel(
            self.progress_card, text="下載記錄",
            font=ctk.CTkFont(size=13, weight="bold"),
        ).pack(anchor="w", padx=20, pady=(4, 2))

        self.log_textbox = ctk.CTkTextbox(
            self.progress_card, height=200,
            font=ctk.CTkFont(size=11, family="Consolas"),
            state="disabled")
        self.log_textbox.pack(fill="both", expand=True, padx=20, pady=(0, 16))

    # ---- Events ----

    def _on_toggle(self):
        self.vm.toggle_scheduler(bool(self.sched_switch.get()))

    def _on_save_time(self):
        self.vm.save_time(self.time_entry.get())

    def _on_save_topn(self):
        self.vm.save_top_n(self.topn_entry.get())

    def _on_refresh_list(self):
        self.vm.refresh_stock_list()

    def _on_download_tdcc(self):
        self.vm.download_tdcc()

    def _on_download_insti(self):
        self.vm.download_insti()

    def _on_run_now(self):
        self.vm.run_now()

    # ---- Bindings ----

    def _bind_vm(self):
        self.vm.bind("status_text", self._on_status)
        self.vm.bind("next_run_text", self._on_next_run)
        self.vm.bind("last_result_text", self._on_last_result)
        self.vm.bind("stock_list_info", self._on_list_info)
        self.vm.bind("stock_list_loading", self._on_list_loading)
        self.vm.bind("shioaji_usage", self._on_usage)
        self.vm.bind("tdcc_status", self._on_tdcc_status)
        self.vm.bind("tdcc_loading", self._on_tdcc_loading)
        self.vm.bind("insti_status", self._on_insti_status)
        self.vm.bind("insti_loading", self._on_insti_loading)
        self.vm.bind("progress", self._on_progress)
        self.vm.bind("progress_text", self._on_progress_text)
        self.vm.bind("log_text", self._on_log)
        self.vm.bind("is_downloading", self._on_downloading)

    def _on_status(self, v: str):
        self.after(0, lambda: self.status_label.configure(text=v))
        self.after(0, lambda: self.dl_status_label.configure(text=v))

    def _on_next_run(self, v: str):
        self.after(0, lambda: self.next_run_label.configure(text=v))

    def _on_last_result(self, v: str):
        self.after(0, lambda: self.last_result_label.configure(text=v))

    def _on_list_info(self, v: str):
        self.after(0, lambda: self.list_info_label.configure(text=v))

    def _on_list_loading(self, v: bool):
        def _u():
            if v:
                self.refresh_list_btn.configure(
                    state="disabled", text="取得中...")
            else:
                self.refresh_list_btn.configure(
                    state="normal",
                    text="更新清單（從 TPEX 取得今日前 N 名）")
        self.after(0, _u)

    def _on_usage(self, data):
        def _u():
            for w in self.usage_frame.winfo_children():
                w.destroy()

            if not data:
                ctk.CTkLabel(
                    self.usage_frame,
                    text="（請先在「下單」分頁登入永豐金帳號）",
                    font=ctk.CTkFont(size=13), text_color="gray",
                ).pack(anchor="w")
                return

            conns = data.get("connections", 0)
            used = data.get("bytes_used", 0)
            limit = data.get("limit_bytes", 0)
            remain = data.get("remaining_bytes", 0)

            used_mb = used / 1024 / 1024
            limit_mb = limit / 1024 / 1024
            remain_mb = remain / 1024 / 1024
            pct = (used / limit * 100) if limit > 0 else 0

            # KPI row
            kpi_row = ctk.CTkFrame(self.usage_frame, fg_color="transparent")
            kpi_row.pack(fill="x", pady=(0, 4))

            for label, value, color in [
                ("連線數", str(conns), "#c0c0c0"),
                ("已使用", f"{used_mb:,.1f} MB", "#ff9800" if pct > 70 else "#4ECDC4"),
                ("剩餘", f"{remain_mb:,.1f} MB", "#ef5350" if pct > 90 else "#26a69a"),
                ("上限", f"{limit_mb:,.1f} MB", "#c0c0c0"),
                ("使用率", f"{pct:.1f}%", "#ef5350" if pct > 90 else ("#ff9800" if pct > 70 else "#4ECDC4")),
            ]:
                f = ctk.CTkFrame(kpi_row, fg_color="#1e1e1e", corner_radius=8)
                f.pack(side="left", padx=4, pady=2)
                ctk.CTkLabel(f, text=label, font=ctk.CTkFont(size=11),
                              text_color="gray").pack(padx=12, pady=(5, 0))
                ctk.CTkLabel(f, text=value,
                              font=ctk.CTkFont(size=14, weight="bold"),
                              text_color=color).pack(padx=12, pady=(0, 5))

            # Progress bar
            bar_frame = ctk.CTkFrame(self.usage_frame, fg_color="transparent")
            bar_frame.pack(fill="x", pady=(2, 0))
            bar = ctk.CTkProgressBar(bar_frame, width=400)
            bar.pack(side="left", padx=(0, 8))
            bar.set(min(pct / 100, 1.0))
            ctk.CTkLabel(
                bar_frame, text=f"{pct:.1f}%",
                font=ctk.CTkFont(size=12, weight="bold"),
                text_color="#ff9800" if pct > 70 else "#4ECDC4",
            ).pack(side="left")

        self.after(0, _u)

    def _on_tdcc_status(self, v: str):
        self.after(0, lambda: self.tdcc_status_label.configure(text=v or "—"))

    def _on_tdcc_loading(self, v: bool):
        def _u():
            if v:
                self.tdcc_btn.configure(state="disabled", text="下載中...")
            else:
                self.tdcc_btn.configure(
                    state="normal", text="下載集保資料（全部股票）")
        self.after(0, _u)

    def _on_insti_status(self, v: str):
        self.after(0, lambda: self.insti_status_label.configure(text=v or "—"))

    def _on_insti_loading(self, v: bool):
        def _u():
            if v:
                self.insti_btn.configure(state="disabled", text="下載中...")
            else:
                self.insti_btn.configure(
                    state="normal",
                    text="下載三大法人資料（近 5 個交易日）")
        self.after(0, _u)

    def _on_progress(self, v: float):
        self.after(0, lambda: self.progress_bar.set(v))

    def _on_progress_text(self, v: str):
        self.after(0, lambda: self.progress_label.configure(text=v))

    def _on_log(self, v: str):
        def _update():
            self.log_textbox.configure(state="normal")
            self.log_textbox.delete("1.0", "end")
            self.log_textbox.insert("1.0", v or "")
            self.log_textbox.see("end")
            self.log_textbox.configure(state="disabled")
        self.after(0, _update)

    def _on_downloading(self, v: bool):
        def _update():
            if v:
                self.run_now_btn.configure(state="disabled", text="下載中...")
                self.progress_card.pack(padx=40, pady=8, fill="both",
                                         expand=True)
            else:
                self.run_now_btn.configure(
                    state="normal", text="立即下載（使用目前清單）")
        self.after(0, _update)

    # ---- Periodic refresh ----

    def _start_refresh_loop(self):
        self.vm.refresh()
        self._refresh_id = self.after(3000, self._start_refresh_loop)

    def destroy(self):
        if self._refresh_id is not None:
            self.after_cancel(self._refresh_id)
        super().destroy()
