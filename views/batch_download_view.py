import threading
import customtkinter as ctk

from viewmodels.batch_download_viewmodel import BatchDownloadViewModel
from services.tpex_api_service import fetch_top_volume_stocks


class BatchDownloadView(ctk.CTkFrame):
    """批次下載分點資料並存入資料庫 tab page."""

    def __init__(self, parent: ctk.CTkFrame, viewmodel: BatchDownloadViewModel):
        super().__init__(parent, fg_color="transparent")
        self.vm = viewmodel
        self._build_ui()
        self._bind_vm()

    def _build_ui(self):
        container = ctk.CTkScrollableFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True)

        # --- Title ---
        ctk.CTkLabel(
            container, text="批次下載至資料庫",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(pady=(24, 8))

        ctk.CTkLabel(
            container,
            text="輸入多檔股票代碼，自動逐一下載分點資料並寫入 MSSQL 資料庫",
            font=ctk.CTkFont(size=13), text_color="gray",
        ).pack(pady=(0, 24))

        # --- Input card ---
        card = ctk.CTkFrame(container, corner_radius=12)
        card.pack(padx=40, pady=8, fill="x")

        ctk.CTkLabel(
            card, text="股票代碼清單",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(anchor="w", padx=20, pady=(16, 4))

        ctk.CTkLabel(
            card, text="以逗號、空格或換行分隔，例: 6180, 2330, 3008",
            font=ctk.CTkFont(size=11), text_color="gray",
        ).pack(anchor="w", padx=20, pady=(0, 8))

        # Quick-fill row
        fill_row = ctk.CTkFrame(card, fg_color="transparent")
        fill_row.pack(fill="x", padx=20, pady=(0, 6))

        self.top_n_entry = ctk.CTkEntry(fill_row, width=60, placeholder_text="200")
        self.top_n_entry.pack(side="left")

        self.load_top_btn = ctk.CTkButton(
            fill_row, text="載入交易量前 N 名", width=180, height=30,
            corner_radius=6, font=ctk.CTkFont(size=12),
            command=self._on_load_top,
        )
        self.load_top_btn.pack(side="left", padx=(8, 0))

        self.load_status_label = ctk.CTkLabel(
            fill_row, text="", font=ctk.CTkFont(size=11), text_color="gray",
        )
        self.load_status_label.pack(side="left", padx=(8, 0))

        self.codes_textbox = ctk.CTkTextbox(card, height=100, font=ctk.CTkFont(size=13))
        self.codes_textbox.pack(fill="x", padx=20, pady=(0, 8))

        # Options row
        opt_row = ctk.CTkFrame(card, fg_color="transparent")
        opt_row.pack(fill="x", padx=20, pady=4)

        self.skip_existing_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            opt_row, text="跳過已存在的資料（同股票+同日期）",
            variable=self.skip_existing_var,
            font=ctk.CTkFont(size=12),
        ).pack(side="left")

        # Error label
        self.error_label = ctk.CTkLabel(
            card, text="", font=ctk.CTkFont(size=12), text_color="#FF6B6B",
        )
        self.error_label.pack(padx=20, pady=(4, 0))

        # Buttons row
        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(pady=(8, 20))

        self.start_btn = ctk.CTkButton(
            btn_row, text="開始批次下載", width=160, height=38,
            corner_radius=8, font=ctk.CTkFont(size=14, weight="bold"),
            command=self._on_start,
        )
        self.start_btn.pack(side="left", padx=4)

        self.cancel_btn = ctk.CTkButton(
            btn_row, text="取消", width=80, height=38,
            corner_radius=8, font=ctk.CTkFont(size=13),
            fg_color="#666", hover_color="#888",
            command=self._on_cancel, state="disabled",
        )
        self.cancel_btn.pack(side="left", padx=4)

        # --- Progress area ---
        prog_card = ctk.CTkFrame(container, corner_radius=12)
        prog_card.pack(padx=40, pady=8, fill="x")

        prog_top = ctk.CTkFrame(prog_card, fg_color="transparent")
        prog_top.pack(fill="x", padx=20, pady=(16, 4))

        self.status_label = ctk.CTkLabel(
            prog_top, text="就緒", font=ctk.CTkFont(size=12), text_color="gray",
        )
        self.status_label.pack(side="left")

        self.progress_label = ctk.CTkLabel(
            prog_top, text="", font=ctk.CTkFont(size=12, weight="bold"),
        )
        self.progress_label.pack(side="right")

        self.progress_bar = ctk.CTkProgressBar(prog_card, width=400)
        self.progress_bar.pack(padx=20, pady=(0, 8))
        self.progress_bar.set(0)

        # Trade date display
        date_row = ctk.CTkFrame(prog_card, fg_color="transparent")
        date_row.pack(fill="x", padx=20, pady=(0, 16))

        ctk.CTkLabel(
            date_row, text="交易日期：",
            font=ctk.CTkFont(size=12), text_color="gray",
        ).pack(side="left")

        self.trade_date_label = ctk.CTkLabel(
            date_row, text="（等待下載）",
            font=ctk.CTkFont(size=12, weight="bold"), text_color="#4ECDC4",
        )
        self.trade_date_label.pack(side="left")

        # --- Log area ---
        log_card = ctk.CTkFrame(container, corner_radius=12)
        log_card.pack(padx=40, pady=8, fill="both", expand=True)

        ctk.CTkLabel(
            log_card, text="下載記錄",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(anchor="w", padx=16, pady=(12, 4))

        self.log_textbox = ctk.CTkTextbox(
            log_card, height=250, font=ctk.CTkFont(size=12, family="Consolas"),
            state="disabled",
        )
        self.log_textbox.pack(fill="both", expand=True, padx=16, pady=(0, 12))

    # ---------------------------------------------------------------- Events

    def _on_load_top(self):
        """Fetch top N stocks by volume from TPEX API and fill the textbox."""
        raw = self.top_n_entry.get().strip()
        top_n = int(raw) if raw.isdigit() else 200

        self.load_top_btn.configure(state="disabled", text="載入中...")
        self.load_status_label.configure(text="正在查詢 TPEX...")

        def _fetch():
            try:
                stocks = fetch_top_volume_stocks(top_n)
                codes = ", ".join(s.stock_code for s in stocks)
                summary = f"已載入 {len(stocks)} 檔（依成交量排序）"

                def _fill():
                    self.codes_textbox.delete("1.0", "end")
                    self.codes_textbox.insert("1.0", codes)
                    self.load_status_label.configure(text=summary)
                    self.load_top_btn.configure(state="normal", text="載入交易量前 N 名")

                self.after(0, _fill)
            except Exception as e:
                def _err():
                    self.load_status_label.configure(text=f"錯誤：{e}")
                    self.load_top_btn.configure(state="normal", text="載入交易量前 N 名")
                self.after(0, _err)

        threading.Thread(target=_fetch, daemon=True).start()

    def _on_start(self):
        text = self.codes_textbox.get("1.0", "end").strip()
        self.vm.start_batch(text, skip_existing=self.skip_existing_var.get())

    def _on_cancel(self):
        self.vm.cancel()

    # ---------------------------------------------------------------- Bindings

    def _bind_vm(self):
        self.vm.bind("status_text", self._on_status)
        self.vm.bind("progress", self._on_progress)
        self.vm.bind("progress_text", self._on_progress_text)
        self.vm.bind("is_downloading", self._on_downloading)
        self.vm.bind("log_text", self._on_log)
        self.vm.bind("error_text", self._on_error)
        self.vm.bind("trade_date_text", self._on_trade_date)

    def _on_status(self, v: str):
        self.after(0, lambda: self.status_label.configure(text=v))

    def _on_progress(self, v: float):
        self.after(0, lambda: self.progress_bar.set(v))

    def _on_progress_text(self, v: str):
        self.after(0, lambda: self.progress_label.configure(text=v))

    def _on_downloading(self, v: bool):
        def _update():
            if v:
                self.start_btn.configure(state="disabled", text="下載中...")
                self.cancel_btn.configure(state="normal")
            else:
                self.start_btn.configure(state="normal", text="開始批次下載")
                self.cancel_btn.configure(state="disabled")
        self.after(0, _update)

    def _on_log(self, v: str):
        def _update():
            self.log_textbox.configure(state="normal")
            self.log_textbox.delete("1.0", "end")
            self.log_textbox.insert("1.0", v or "")
            self.log_textbox.see("end")
            self.log_textbox.configure(state="disabled")
        self.after(0, _update)

    def _on_trade_date(self, v: str):
        def _update():
            if v:
                self.trade_date_label.configure(text=v)
            else:
                self.trade_date_label.configure(text="（等待下載）")
        self.after(0, _update)

    def _on_error(self, v: str):
        self.after(0, lambda: self.error_label.configure(text=v))
