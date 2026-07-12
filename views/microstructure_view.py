"""大單追蹤 — 市場微觀結構即時偵測介面。"""

from __future__ import annotations

from datetime import datetime

import customtkinter as ctk
from tkinter import ttk, filedialog

from viewmodels.microstructure_viewmodel import MicrostructureViewModel


def _fmt(n, nd=0) -> str:
    try:
        return f"{float(n):,.{nd}f}"
    except (ValueError, TypeError):
        return str(n)


class MicrostructureView(ctk.CTkFrame):
    """大單追蹤 tab page。"""

    def __init__(self, parent: ctk.CTkFrame, viewmodel: MicrostructureViewModel):
        super().__init__(parent, fg_color="transparent")
        self.vm = viewmodel
        self._tiles: dict[str, ctk.CTkLabel] = {}
        self._points_tree: ttk.Treeview | None = None
        self._ob_tree: ttk.Treeview | None = None
        self._large_tree: ttk.Treeview | None = None
        self._bt_tree: ttk.Treeview | None = None
        self._build_ui()
        self._bind_vm()
        # 進頁時同步共用連線狀態
        self.vm.refresh_conn_state()

    # ================================================================ Build UI

    def _build_ui(self):
        container = ctk.CTkScrollableFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True)

        ctk.CTkLabel(
            container, text="大單追蹤",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(pady=(20, 2))
        ctk.CTkLabel(
            container,
            text="市場微觀結構即時偵測：委託單失衡(OBI) · 成交量毒性(VPIN) · 大單/冰山/起漲點",
            font=ctk.CTkFont(size=13), text_color="#4ECDC4",
        ).pack(pady=(0, 14))

        # -------- Connection + tracking card --------
        top = ctk.CTkFrame(container, corner_radius=12)
        top.pack(padx=30, pady=6, fill="x")

        conn_row = ctk.CTkFrame(top, fg_color="transparent")
        conn_row.pack(fill="x", padx=20, pady=(16, 6))
        ctk.CTkLabel(conn_row, text="行情連線",
                      font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")

        self.env_var = ctk.StringVar(value="正式環境")
        ctk.CTkSegmentedButton(
            conn_row, values=["正式環境", "測試環境"], variable=self.env_var,
            font=ctk.CTkFont(size=12), width=160,
        ).pack(side="left", padx=(14, 0))

        self.connect_btn = ctk.CTkButton(
            conn_row, text="連線", width=90, height=32, corner_radius=8,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#1f6aa5", hover_color="#185a8c",
            command=self._on_connect)
        self.connect_btn.pack(side="left", padx=(14, 0))

        self.conn_label = ctk.CTkLabel(
            conn_row, text="", font=ctk.CTkFont(size=12), text_color="#c0c0c0")
        self.conn_label.pack(side="left", padx=(12, 0))

        ctk.CTkLabel(
            top,
            text="※ 即時逐筆行情僅正式環境提供；請先於『下單/系統設定』分頁儲存 API 金鑰。",
            font=ctk.CTkFont(size=11), text_color="#888888",
        ).pack(anchor="w", padx=20, pady=(0, 6))

        track_row = ctk.CTkFrame(top, fg_color="transparent")
        track_row.pack(fill="x", padx=20, pady=(4, 16))
        ctk.CTkLabel(track_row, text="股票代碼：",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.code_entry = ctk.CTkEntry(track_row, width=110,
                                       font=ctk.CTkFont(size=14))
        self.code_entry.pack(side="left", padx=(4, 10))
        self.code_entry.bind("<Return>", lambda e: self._on_start())

        self.start_btn = ctk.CTkButton(
            track_row, text="開始追蹤", width=100, height=34, corner_radius=8,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#26a69a", hover_color="#00897b",
            command=self._on_start)
        self.start_btn.pack(side="left")

        self.stop_btn = ctk.CTkButton(
            track_row, text="停止追蹤", width=100, height=34, corner_radius=8,
            font=ctk.CTkFont(size=13), fg_color="#ef5350", hover_color="#c62828",
            command=self._on_stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))

        self.auto_param_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            track_row, text="依股價自動調參", variable=self.auto_param_var,
            font=ctk.CTkFont(size=12)).pack(side="left", padx=(12, 0))

        self.track_status = ctk.CTkLabel(
            track_row, text="", font=ctk.CTkFont(size=13), text_color="#4ECDC4")
        self.track_status.pack(side="left", padx=(14, 0))

        # -------- Parameter card --------
        self._build_param_card(container)

        # -------- Metric tiles --------
        tiles_wrap = ctk.CTkFrame(container, fg_color="transparent")
        tiles_wrap.pack(padx=30, pady=(10, 4), fill="x")
        specs = [
            ("price", "現價 / 均價", "#e0e0e0"),
            ("obi1", "OBI 第一檔", "#4ECDC4"),
            ("obi5", "OBI 前五檔", "#4ECDC4"),
            ("vpin", "VPIN 毒性", "#FFD166"),
            ("push", "買方推進率", "#FFD166"),
            ("io", "內外盤量比", "#e0e0e0"),
        ]
        for i, (key, title, color) in enumerate(specs):
            tiles_wrap.grid_columnconfigure(i, weight=1)
            tile = ctk.CTkFrame(tiles_wrap, corner_radius=10, fg_color="#1e2833")
            tile.grid(row=0, column=i, padx=5, pady=4, sticky="nsew")
            ctk.CTkLabel(tile, text=title, font=ctk.CTkFont(size=12),
                          text_color="#9aa4ad").pack(pady=(10, 0))
            val = ctk.CTkLabel(tile, text="—",
                                font=ctk.CTkFont(size=20, weight="bold"),
                                text_color=color)
            val.pack(pady=(2, 10))
            self._tiles[key] = val

        # 蓄勢 / 站上均價 狀態燈
        self.flag_label = ctk.CTkLabel(
            container, text="", font=ctk.CTkFont(size=13, weight="bold"))
        self.flag_label.pack(pady=(2, 6))

        # -------- 買賣點 (trade points) --------
        pts_card = ctk.CTkFrame(container, corner_radius=12)
        pts_card.pack(padx=30, pady=6, fill="x")
        pts_hdr = ctk.CTkFrame(pts_card, fg_color="transparent")
        pts_hdr.pack(fill="x", padx=16, pady=(12, 4))
        ctk.CTkLabel(pts_hdr, text="買 / 賣 點",
                      font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        ctk.CTkLabel(pts_hdr,
                      text="由微觀結構訊號彙整（起漲/起跌 · 動能點火 · 冰山），僅供參考非投資建議",
                      font=ctk.CTkFont(size=11), text_color="#888888").pack(
                          side="left", padx=(10, 0))
        ctk.CTkButton(pts_hdr, text="匯出 CSV", width=90, height=28, corner_radius=6,
                       font=ctk.CTkFont(size=12), fg_color="#1f6aa5", hover_color="#185a8c",
                       command=self._on_export_csv).pack(side="right")
        self.points_frame = ctk.CTkFrame(pts_card, fg_color="transparent")
        self.points_frame.pack(fill="both", expand=True, padx=10, pady=(0, 12))
        self.export_label = ctk.CTkLabel(
            pts_card, text="", font=ctk.CTkFont(size=11), text_color="#4ECDC4")
        self.export_label.pack(anchor="w", padx=16, pady=(0, 8))

        # -------- Order book + large orders (side by side) --------
        mid = ctk.CTkFrame(container, fg_color="transparent")
        mid.pack(padx=30, pady=6, fill="x")
        mid.grid_columnconfigure(0, weight=1)
        mid.grid_columnconfigure(1, weight=1)

        ob_card = ctk.CTkFrame(mid, corner_radius=12)
        ob_card.grid(row=0, column=0, padx=(0, 6), sticky="nsew")
        ctk.CTkLabel(ob_card, text="五檔委託",
                      font=ctk.CTkFont(size=14, weight="bold")).pack(
                          anchor="w", padx=16, pady=(12, 4))
        self.ob_frame = ctk.CTkFrame(ob_card, fg_color="transparent")
        self.ob_frame.pack(fill="both", expand=True, padx=10, pady=(0, 12))

        lg_card = ctk.CTkFrame(mid, corner_radius=12)
        lg_card.grid(row=0, column=1, padx=(6, 0), sticky="nsew")
        ctk.CTkLabel(lg_card, text="近期大單",
                      font=ctk.CTkFont(size=14, weight="bold")).pack(
                          anchor="w", padx=16, pady=(12, 4))
        self.large_frame = ctk.CTkFrame(lg_card, fg_color="transparent")
        self.large_frame.pack(fill="both", expand=True, padx=10, pady=(0, 12))

        # -------- Signal log --------
        log_card = ctk.CTkFrame(container, corner_radius=12)
        log_card.pack(padx=30, pady=(6, 16), fill="x")
        log_hdr = ctk.CTkFrame(log_card, fg_color="transparent")
        log_hdr.pack(fill="x", padx=16, pady=(12, 4))
        ctk.CTkLabel(log_hdr, text="訊號紀錄",
                      font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        ctk.CTkButton(log_hdr, text="清除", width=60, height=26, corner_radius=6,
                       font=ctk.CTkFont(size=12), fg_color="#555", hover_color="#777",
                       command=self.vm.clear_alerts).pack(side="right")

        self.log_textbox = ctk.CTkTextbox(
            log_card, height=200,
            font=ctk.CTkFont(size=12, family="Consolas"), state="disabled")
        self.log_textbox.pack(fill="x", padx=14, pady=(0, 14))

        # -------- Backtest card --------
        self._build_backtest_card(container)

        self._build_points_tree()
        self._build_ob_tree()
        self._build_large_tree()

    # ---- Parameter card ----

    # (key, 顯示標籤, 型別, 說明群組)
    PARAM_SPEC = [
        ("obi_threshold", "OBI 門檻", float, "OBI"),
        ("obi_sustain_ticks", "OBI 連續筆數", int, "OBI"),
        ("obi_window", "OBI 窗口", int, "OBI"),
        ("bucket_size", "VPIN 桶量(張)", float, "VPIN"),
        ("vpin_buckets", "VPIN 桶數", int, "VPIN"),
        ("buy_push_ratio", "買方推進門檻", float, "VPIN"),
        ("large_order_mult", "大單倍數", float, "大單"),
        ("large_order_floor", "大單最低張", float, "大單"),
        ("trade_window", "均量窗口", int, "大單"),
        ("min_trades_for_avg", "起算筆數", int, "大單"),
        ("attack_consecutive", "起漲連續數", int, "大單"),
        ("iceberg_mult", "冰山倍數", float, "冰山"),
        ("iceberg_min_visible", "冰山最低顯示張", float, "冰山"),
    ]

    def _build_param_card(self, container):
        card = ctk.CTkFrame(container, corner_radius=12)
        card.pack(padx=30, pady=6, fill="x")

        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(14, 6))
        ctk.CTkLabel(hdr, text="偵測參數",
                      font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        ctk.CTkButton(hdr, text="套用參數", width=90, height=30, corner_radius=8,
                       font=ctk.CTkFont(size=13, weight="bold"),
                       fg_color="#1f6aa5", hover_color="#185a8c",
                       command=self._on_apply_params).pack(side="right")
        ctk.CTkButton(hdr, text="回復預設", width=80, height=30, corner_radius=8,
                       font=ctk.CTkFont(size=12), fg_color="#555", hover_color="#777",
                       command=self._on_reset_params).pack(side="right", padx=(0, 8))
        ctk.CTkButton(hdr, text="依股價自動", width=100, height=30, corner_radius=8,
                       font=ctk.CTkFont(size=12, weight="bold"),
                       fg_color="#26a69a", hover_color="#00897b",
                       command=self._on_auto_params).pack(side="right", padx=(0, 8))

        grid = ctk.CTkFrame(card, fg_color="transparent")
        grid.pack(fill="x", padx=20, pady=(0, 6))
        for c in range(3):
            grid.grid_columnconfigure(c, weight=1)

        cur = self.vm.default_params()
        self._param_entries: dict[str, tuple] = {}
        PER_ROW = 3
        for idx, (key, label, typ, _grp) in enumerate(self.PARAM_SPEC):
            r, c = divmod(idx, PER_ROW)
            cell = ctk.CTkFrame(grid, fg_color="transparent")
            cell.grid(row=r, column=c, padx=6, pady=4, sticky="ew")
            ctk.CTkLabel(cell, text=label + "：", width=110, anchor="e",
                          font=ctk.CTkFont(size=12)).pack(side="left")
            ent = ctk.CTkEntry(cell, width=70, font=ctk.CTkFont(size=12))
            ent.pack(side="left")
            val = cur.get(key)
            ent.insert(0, self._fmt_param(val, typ))
            self._param_entries[key] = (ent, typ)

        self.params_status_label = ctk.CTkLabel(
            card, text="留空則沿用預設；套用後即時生效並歸零統計。",
            font=ctk.CTkFont(size=11), text_color="#888888")
        self.params_status_label.pack(anchor="w", padx=20, pady=(0, 12))

    @staticmethod
    def _fmt_param(val, typ) -> str:
        if typ is int:
            return str(int(val))
        s = f"{float(val):g}"
        return s

    def _on_apply_params(self):
        params = {}
        for key, (ent, typ) in self._param_entries.items():
            raw = ent.get().strip()
            if raw == "":
                continue
            try:
                params[key] = typ(raw)
            except ValueError:
                self.params_status_label.configure(
                    text=f"「{key}」數值格式錯誤：{raw}", text_color="#FF6B6B")
                return
        self.vm.apply_params(params)

    def _on_reset_params(self):
        self.vm.reset_params()
        self._fill_param_entries(self.vm.default_params())

    def _on_auto_params(self):
        code = self.code_entry.get().strip()
        self.vm.auto_params(code)

    def _fill_param_entries(self, cur: dict):
        """把一組參數 dict 回填到輸入框（供回復預設 / 自動調參後同步顯示）。"""
        if not cur:
            return
        for key, (ent, typ) in self._param_entries.items():
            if key not in cur:
                continue
            ent.delete(0, "end")
            ent.insert(0, self._fmt_param(cur.get(key), typ))

    # ---- Backtest card ----

    BT_NUM_SPEC = [
        ("entry_min_conditions", "進場門檻K", int),
        ("min_consec_buckets", "連續桶數", int),
        ("confluence_window", "訊號窗口", int),
        ("take_profit_pct", "停利%", float),
        ("stop_loss_pct", "停損%", float),
        ("trailing_pct", "移動停損%", float),
        ("slippage_ticks", "滑價(tick)", float),
        ("fee_discount", "手續費折", float),
        ("debug_every", "診斷間隔", int),
        ("daily_ma_period", "日線MA", int),
        ("bar_seconds", "分線秒數", int),
        ("intraday_ma_period", "分線MA", int),
    ]

    def _build_backtest_card(self, container):
        card = ctk.CTkFrame(container, corner_radius=12)
        card.pack(padx=30, pady=(6, 16), fill="x")

        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=20, pady=(14, 2))
        ctk.CTkLabel(hdr, text="策略回測（永豐歷史逐筆）",
                      font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")
        ctk.CTkLabel(hdr, text="套用上方偵測參數，事件驅動回測 · 已計滑價與手續費稅",
                      font=ctk.CTkFont(size=11), text_color="#888888").pack(
                          side="left", padx=(10, 0))

        cur0 = self.vm.default_backtest_params()
        self._strategy_map = {"合流計分(多空)": "confluence",
                              "起漲/起跌點(順勢)": "attack_point"}
        self._strategy_rmap = {v: k for k, v in self._strategy_map.items()}
        ctk.CTkLabel(hdr, text="策略：", font=ctk.CTkFont(size=12)).pack(
            side="left", padx=(16, 2))
        self.bt_strategy_var = ctk.StringVar(
            value=self._strategy_rmap.get(cur0.get("strategy", "confluence"),
                                          "合流計分(多空)"))
        ctk.CTkOptionMenu(
            hdr, values=list(self._strategy_map.keys()), variable=self.bt_strategy_var,
            width=150, font=ctk.CTkFont(size=12)).pack(side="left")

        # 代碼 + 日期 + 做多/做空
        row1 = ctk.CTkFrame(card, fg_color="transparent")
        row1.pack(fill="x", padx=20, pady=(6, 2))
        ctk.CTkLabel(row1, text="代碼：", font=ctk.CTkFont(size=13)).pack(side="left")
        self.bt_code_entry = ctk.CTkEntry(row1, width=90, font=ctk.CTkFont(size=13))
        self.bt_code_entry.pack(side="left", padx=(2, 12))
        ctk.CTkLabel(row1, text="日期：", font=ctk.CTkFont(size=13)).pack(side="left")
        self.bt_date_entry = ctk.CTkEntry(row1, width=110, font=ctk.CTkFont(size=13),
                                          placeholder_text="YYYY-MM-DD")
        self.bt_date_entry.pack(side="left", padx=(2, 12))
        self.bt_date_entry.insert(0, datetime.now().strftime("%Y-%m-%d"))

        cur = self.vm.default_backtest_params()
        self.bt_long_var = ctk.BooleanVar(value=bool(cur.get("allow_long", True)))
        self.bt_short_var = ctk.BooleanVar(value=bool(cur.get("allow_short", True)))
        ctk.CTkCheckBox(row1, text="做多", variable=self.bt_long_var,
                         font=ctk.CTkFont(size=13)).pack(side="left", padx=(0, 8))
        ctk.CTkCheckBox(row1, text="做空", variable=self.bt_short_var,
                         font=ctk.CTkFont(size=13)).pack(side="left", padx=(0, 16))

        self.bt_req_attack_var = ctk.BooleanVar(value=bool(cur.get("require_attack", False)))
        self.bt_req_breakout_var = ctk.BooleanVar(value=bool(cur.get("require_breakout", False)))
        ctk.CTkCheckBox(row1, text="需大單", variable=self.bt_req_attack_var,
                         font=ctk.CTkFont(size=13)).pack(side="left", padx=(0, 8))
        ctk.CTkCheckBox(row1, text="需突破", variable=self.bt_req_breakout_var,
                         font=ctk.CTkFont(size=13)).pack(side="left", padx=(0, 16))

        self.bt_invert_var = ctk.BooleanVar(value=bool(cur.get("invert_signals", False)))
        ctk.CTkCheckBox(row1, text="反向(fade)", variable=self.bt_invert_var,
                         font=ctk.CTkFont(size=13, weight="bold"),
                         fg_color="#d98324", hover_color="#b86a1a").pack(side="left")

        # 濾網列：雙層濾網（上層決定方向，微觀訊號只在閘門開啟時扣板機）
        rowf = ctk.CTkFrame(card, fg_color="transparent")
        rowf.pack(fill="x", padx=20, pady=(2, 2))
        ctk.CTkLabel(rowf, text="趨勢濾網：",
                      font=ctk.CTkFont(size=13, weight="bold")).pack(side="left")
        self.bt_daily_filter_var = ctk.BooleanVar(
            value=bool(cur.get("daily_trend_filter", False)))
        ctk.CTkCheckBox(rowf, text="日線趨勢(只做順勢)", variable=self.bt_daily_filter_var,
                         font=ctk.CTkFont(size=13)).pack(side="left", padx=(0, 16))
        ctk.CTkLabel(rowf, text="分線閘門：",
                      font=ctk.CTkFont(size=13)).pack(side="left")
        self._intraday_map = {"關閉": "off", "分線MA順勢": "ma", "布林擠壓突破": "squeeze"}
        self._intraday_rmap = {v: k for k, v in self._intraday_map.items()}
        self.bt_intraday_var = ctk.StringVar(
            value=self._intraday_rmap.get(cur.get("intraday_filter", "off"), "關閉"))
        ctk.CTkOptionMenu(
            rowf, values=list(self._intraday_map.keys()), variable=self.bt_intraday_var,
            width=140, font=ctk.CTkFont(size=12)).pack(side="left", padx=(0, 8))

        # 數值參數（多欄自動換行，避免欄位過多被裁切）
        row2 = ctk.CTkFrame(card, fg_color="transparent")
        row2.pack(fill="x", padx=20, pady=(4, 2))
        self._bt_entries: dict[str, tuple] = {}
        PER_ROW = 5
        for idx, (key, label, typ) in enumerate(self.BT_NUM_SPEC):
            r, c = divmod(idx, PER_ROW)
            cell = ctk.CTkFrame(row2, fg_color="transparent")
            cell.grid(row=r, column=c, padx=(0, 8), pady=3, sticky="w")
            ctk.CTkLabel(cell, text=label + "：",
                          font=ctk.CTkFont(size=12)).pack(side="left")
            ent = ctk.CTkEntry(cell, width=56, font=ctk.CTkFont(size=12))
            ent.pack(side="left")
            ent.insert(0, self._fmt_param(cur.get(key), typ))
            self._bt_entries[key] = (ent, typ)

        # 執行 + 狀態
        row3 = ctk.CTkFrame(card, fg_color="transparent")
        row3.pack(fill="x", padx=20, pady=(6, 4))
        self.bt_run_btn = ctk.CTkButton(
            row3, text="執行回測", width=110, height=34, corner_radius=8,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#7e57c2", hover_color="#5e35b1",
            command=self._on_run_backtest)
        self.bt_run_btn.pack(side="left")
        self.bt_save_btn = ctk.CTkButton(
            row3, text="儲存設定", width=90, height=34, corner_radius=8,
            font=ctk.CTkFont(size=12), fg_color="#555", hover_color="#777",
            command=self._on_save_bt_params)
        self.bt_save_btn.pack(side="left", padx=(8, 0))
        self.bt_export_btn = ctk.CTkButton(
            row3, text="匯出交易 CSV", width=110, height=34, corner_radius=8,
            font=ctk.CTkFont(size=12), fg_color="#1f6aa5", hover_color="#185a8c",
            command=self._on_export_backtest)
        self.bt_export_btn.pack(side="left", padx=(8, 0))
        self.bt_status_label = ctk.CTkLabel(
            row3, text="", font=ctk.CTkFont(size=12), text_color="#4ECDC4")
        self.bt_status_label.pack(side="left", padx=(14, 0))

        # 報告
        self.bt_report_label = ctk.CTkLabel(
            card, text="", font=ctk.CTkFont(size=12, family="Consolas"),
            justify="left", text_color="#e0e0e0")
        self.bt_report_label.pack(anchor="w", padx=22, pady=(4, 6))

        # 交易明細表
        self.bt_trades_frame = ctk.CTkFrame(card, fg_color="transparent")
        self.bt_trades_frame.pack(fill="both", expand=True, padx=12, pady=(0, 14))
        self._build_bt_tree()

    def _build_bt_tree(self):
        self._tree_style()
        cols = ("dir", "et", "ep", "xt", "xp", "ret", "reason", "hold")
        tree = ttk.Treeview(self.bt_trades_frame, columns=cols, show="headings",
                            height=8, style="Micro.Treeview")
        for c, txt, w, anc in [
            ("dir", "方向", 45, "center"), ("et", "進場時間", 70, "center"),
            ("ep", "進場價", 60, "e"), ("xt", "出場時間", 70, "center"),
            ("xp", "出場價", 60, "e"), ("ret", "報酬%", 60, "e"),
            ("reason", "出場原因", 75, "center"), ("hold", "持有", 45, "e"),
        ]:
            tree.heading(c, text=txt)
            tree.column(c, width=w, anchor=anc, stretch=(c == "reason"))
        tree.tag_configure("win", foreground="#ef5350")
        tree.tag_configure("loss", foreground="#26a69a")
        sb = ttk.Scrollbar(self.bt_trades_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._bt_tree = tree

    def _collect_bt_params(self) -> dict | None:
        """從 UI 收集回測參數；數值格式錯誤時回 None 並顯示訊息。"""
        params = {
            "strategy": self._strategy_map.get(self.bt_strategy_var.get(), "confluence"),
            "allow_long": self.bt_long_var.get(),
            "allow_short": self.bt_short_var.get(),
            "require_attack": self.bt_req_attack_var.get(),
            "require_breakout": self.bt_req_breakout_var.get(),
            "invert_signals": self.bt_invert_var.get(),
            "daily_trend_filter": self.bt_daily_filter_var.get(),
            "intraday_filter": self._intraday_map.get(self.bt_intraday_var.get(), "off"),
        }
        for key, (ent, typ) in self._bt_entries.items():
            raw = ent.get().strip()
            if raw == "":
                continue
            try:
                params[key] = typ(raw)
            except ValueError:
                self.bt_status_label.configure(
                    text=f"「{key}」格式錯誤", text_color="#FF6B6B")
                return None
        return params

    def _on_run_backtest(self):
        params = self._collect_bt_params()
        if params is None:
            return
        self.vm.run_backtest(
            self.bt_code_entry.get().strip() or self.code_entry.get().strip(),
            self.bt_date_entry.get().strip(), params)

    def _on_save_bt_params(self):
        params = self._collect_bt_params()
        if params is None:
            return
        if self.vm.save_backtest_params(params) is not None:
            self.bt_status_label.configure(
                text="✓ 已儲存回測設定", text_color="#4ECDC4")

    def _on_export_backtest(self):
        code = self.bt_code_entry.get().strip() or "stock"
        default_name = f"回測交易_{code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path = filedialog.asksaveasfilename(
            title="匯出回測交易", defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("CSV 檔", "*.csv"), ("所有檔案", "*.*")])
        if path:
            self.vm.export_backtest_csv(path)

    def _tree_style(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Micro.Treeview", background="#1a222c",
                        fieldbackground="#1a222c", foreground="#e0e0e0",
                        rowheight=22, borderwidth=0)
        style.configure("Micro.Treeview.Heading",
                        background="#263238", foreground="#c0c0c0",
                        font=("", 10, "bold"))

    def _build_points_tree(self):
        self._tree_style()
        cols = ("time", "type", "price", "strength", "reason")
        tree = ttk.Treeview(self.points_frame, columns=cols, show="headings",
                            height=6, style="Micro.Treeview")
        for c, txt, w, anc in [("time", "時間", 75, "center"), ("type", "訊號", 60, "center"),
                               ("price", "價格", 65, "e"), ("strength", "強度", 50, "center"),
                               ("reason", "依據", 200, "w")]:
            tree.heading(c, text=txt)
            tree.column(c, width=w, anchor=anc, stretch=(c == "reason"))
        tree.tag_configure("buy", foreground="#ef5350")   # 買點：紅
        tree.tag_configure("sell", foreground="#26a69a")  # 賣點：綠
        tree.tag_configure("buy_dim", foreground="#7a4a48")   # 買點·被濾：暗紅
        tree.tag_configure("sell_dim", foreground="#3f6b64")  # 賣點·被濾：暗綠
        sb = ttk.Scrollbar(self.points_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._points_tree = tree

    def _build_ob_tree(self):
        self._tree_style()
        cols = ("bvol", "bprice", "aprice", "avol")
        tree = ttk.Treeview(self.ob_frame, columns=cols, show="headings",
                            height=5, style="Micro.Treeview")
        for c, txt, anc in [("bvol", "委買量", "e"), ("bprice", "買價", "e"),
                            ("aprice", "賣價", "e"), ("avol", "委賣量", "e")]:
            tree.heading(c, text=txt)
            tree.column(c, width=70, anchor=anc, stretch=True)
        tree.tag_configure("bid", foreground="#ef5350")
        tree.tag_configure("ask", foreground="#26a69a")
        tree.pack(fill="both", expand=True)
        self._ob_tree = tree

    def _build_large_tree(self):
        cols = ("time", "side", "price", "vol", "mult")
        tree = ttk.Treeview(self.large_frame, columns=cols, show="headings",
                            height=6, style="Micro.Treeview")
        for c, txt, w, anc in [("time", "時間", 70, "center"), ("side", "內外", 45, "center"),
                               ("price", "價格", 60, "e"), ("vol", "張數", 55, "e"),
                               ("mult", "倍均量", 55, "e")]:
            tree.heading(c, text=txt)
            tree.column(c, width=w, anchor=anc, stretch=True)
        tree.tag_configure("outer", foreground="#ef5350")
        tree.tag_configure("inner", foreground="#26a69a")
        tree.pack(fill="both", expand=True)
        self._large_tree = tree

    # ================================================================ Events

    def _on_connect(self):
        simulation = self.env_var.get() == "測試環境"
        self.vm.connect(simulation=simulation)

    def _on_start(self):
        self.vm.start_tracking(
            self.code_entry.get().strip(), auto=self.auto_param_var.get())

    def _on_stop(self):
        self.vm.stop_tracking()

    def _on_export_csv(self):
        code = self.vm.tracked_code or "stock"
        default_name = f"買賣點_{code}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path = filedialog.asksaveasfilename(
            title="匯出買賣點", defaultextension=".csv",
            initialfile=default_name,
            filetypes=[("CSV 檔", "*.csv"), ("所有檔案", "*.*")])
        if path:
            self.vm.export_csv(path)

    # ================================================================ Bindings

    def _bind_vm(self):
        self.vm.bind("conn_status", self._on_conn_status)
        self.vm.bind("is_connected", self._on_connected)
        self.vm.bind("is_connecting", self._on_connecting)
        self.vm.bind("is_tracking", self._on_tracking)
        self.vm.bind("tracked_code", self._on_tracked_code)
        self.vm.bind("state_data", self._on_state)
        self.vm.bind("alert_log", self._on_alert_log)
        self.vm.bind("error", self._on_error)
        self.vm.bind("params_status", self._on_params_status)
        self.vm.bind("computed_params", self._on_computed_params)
        self.vm.bind("export_status", self._on_export_status)
        self.vm.bind("is_backtesting", self._on_backtesting)
        self.vm.bind("backtest_status", self._on_backtest_status)
        self.vm.bind("backtest_result", self._on_backtest_result)

    def _on_backtesting(self, v):
        def _u():
            if v:
                self.bt_run_btn.configure(state="disabled", text="回測中...")
            else:
                self.bt_run_btn.configure(state="normal", text="執行回測")
        self.after(0, _u)

    def _on_backtest_status(self, v):
        clr = "#FF6B6B" if ("失敗" in v or "錯誤" in v or "查無" in v or "請" in v) else "#4ECDC4"
        self.after(0, lambda: self.bt_status_label.configure(text=v, text_color=clr))

    def _on_backtest_result(self, data):
        if not data:
            return

        def _u():
            self.bt_report_label.configure(text=data.get("report", ""))
            if not self._bt_tree:
                return
            self._bt_tree.delete(*self._bt_tree.get_children())
            for t in data.get("trades", []):
                ret = t.get("ret_pct", 0)
                tag = "win" if ret > 0 else "loss"
                self._bt_tree.insert("", "end", values=(
                    "做多" if t.get("direction") == "long" else "做空",
                    t.get("entry_time", ""), f"{t.get('entry_price', 0):.2f}",
                    t.get("exit_time", ""), f"{t.get('exit_price', 0):.2f}",
                    f"{ret:+.3f}", t.get("exit_reason", ""), t.get("hold_ticks", 0),
                ), tags=(tag,))
        self.after(0, _u)

    def _on_params_status(self, v):
        if not v:
            return
        clr = "#FF6B6B" if (v.startswith("參數") or "失敗" in v or "查無" in v
                            or "請先" in v) else "#4ECDC4"
        self.after(0, lambda: self.params_status_label.configure(text=v, text_color=clr))

    def _on_computed_params(self, data):
        if not data:
            return
        self.after(0, lambda: self._fill_param_entries(data))

    def _on_export_status(self, v):
        if not v:
            return
        clr = "#FF6B6B" if "失敗" in v or "沒有" in v else "#4ECDC4"
        self.after(0, lambda: self.export_label.configure(text=v, text_color=clr))

    def _on_conn_status(self, v):
        self.after(0, lambda: self.conn_label.configure(text=v))

    def _on_connected(self, v):
        def _u():
            if v:
                self.connect_btn.configure(text="已連線", state="disabled")
            else:
                self.connect_btn.configure(text="連線", state="normal")
        self.after(0, _u)

    def _on_connecting(self, v):
        def _u():
            if v:
                self.connect_btn.configure(text="連線中...", state="disabled")
            elif not self.vm.is_connected:
                self.connect_btn.configure(text="連線", state="normal")
        self.after(0, _u)

    def _on_tracking(self, v):
        def _u():
            if v:
                self.start_btn.configure(state="disabled")
                self.stop_btn.configure(state="normal")
            else:
                self.start_btn.configure(state="normal")
                self.stop_btn.configure(state="disabled")
        self.after(0, _u)

    def _on_tracked_code(self, v):
        self.after(0, lambda: self.track_status.configure(
            text=f"追蹤中：{v}" if v else "", text_color="#4ECDC4"))

    def _on_error(self, v):
        if v:
            self.after(0, lambda: self.track_status.configure(
                text=v, text_color="#FF6B6B"))

    def _on_state(self, data):
        if not data:
            return

        def _u():
            self._update_tiles(data)
            self._update_points(data)
            self._update_ob(data)
            self._update_large(data)
        self.after(0, _u)

    def _update_tiles(self, d: dict):
        price = d.get("last_price", 0)
        avg = d.get("avg_price", 0)
        self._tiles["price"].configure(text=f"{_fmt(price, 2)}\n均 {_fmt(avg, 2)}")

        obi1, obi5 = d.get("obi1", 0), d.get("obi5", 0)
        self._tiles["obi1"].configure(
            text=f"{obi1:+.2f}", text_color=self._obi_color(obi1))
        self._tiles["obi5"].configure(
            text=f"{obi5:+.2f}", text_color=self._obi_color(obi5))

        vpin = d.get("vpin", 0)
        self._tiles["vpin"].configure(text=f"{vpin:.2f}")

        push = d.get("buy_push_ratio", 0.5)
        pc = "#ef5350" if push >= 0.6 else ("#26a69a" if push <= 0.4 else "#FFD166")
        self._tiles["push"].configure(text=f"{push*100:.0f}%", text_color=pc)

        io = d.get("inner_outer_ratio", 0)
        self._tiles["io"].configure(text=f"{io:.2f}" if io else "—")

        # 狀態燈
        flags = []
        setup_side = d.get("setup_side", "")
        if d.get("setup_active") and setup_side == "buy":
            flags.append("🔴 買盤蓄勢中")
        elif d.get("setup_active") and setup_side == "sell":
            flags.append("🟢 賣壓蓄勢中")
        if d.get("above_avg"):
            flags.append("✅ 站上均價")
        else:
            flags.append("⬇ 均價下方")
        # 趨勢濾網狀態（有啟用才顯示）
        if d.get("filter_active"):
            dl, ds = d.get("filter_daily_long", True), d.get("filter_daily_short", True)
            bias = ("日線偏多" if dl and not ds else
                    "日線偏空" if ds and not dl else "日線中性")
            istate = d.get("filter_intraday_state", "")
            fp = d.get("filtered_points", 0)
            seg = f"🛡 {bias}"
            if istate:
                seg += f"｜分線:{istate}"
            if fp:
                seg += f"｜已濾 {fp} 點"
            flags.append(seg)
        self.flag_label.configure(
            text="　".join(flags),
            text_color="#FFD166" if d.get("setup_active") else "#9aa4ad")

    @staticmethod
    def _obi_color(v: float) -> str:
        if v >= 0.6:
            return "#ef5350"
        if v <= -0.6:
            return "#26a69a"
        return "#e0e0e0"

    def _update_points(self, d: dict):
        if not self._points_tree:
            return
        self._points_tree.delete(*self._points_tree.get_children())
        kind_txt = {"attack": "起漲/起跌", "momentum": "動能點火", "iceberg": "冰山"}
        for p in reversed(d.get("trade_points", [])):
            side = p.get("side", "")
            filtered = p.get("filtered", False)
            if filtered:
                type_txt = ("⚪ 買點" if side == "buy" else "⚪ 賣點")
                tag = f"{side}_dim"
                fr = p.get("filter_reason", "") or "逆勢"
                reason_txt = (f"[濾網擋下·{fr}] "
                              f"{kind_txt.get(p.get('kind',''), p.get('kind',''))}"
                              f"｜{p.get('reason','')}")
            else:
                type_txt = ("🔴 買點" if side == "buy" else "🟢 賣點")
                tag = side
                reason_txt = (f"{kind_txt.get(p.get('kind',''), p.get('kind',''))}"
                              f"｜{p.get('reason','')}")
            self._points_tree.insert("", "end", values=(
                p.get("time", ""),
                type_txt,
                _fmt(p.get("price", 0), 2),
                p.get("strength", ""),
                reason_txt,
            ), tags=(tag,))

    def _update_ob(self, d: dict):
        if not self._ob_tree:
            return
        self._ob_tree.delete(*self._ob_tree.get_children())
        bp, bv = d.get("bid_price", []), d.get("bid_volume", [])
        ap, av = d.get("ask_price", []), d.get("ask_volume", [])
        for i in range(5):
            self._ob_tree.insert("", "end", values=(
                _fmt(bv[i]) if i < len(bv) else "",
                _fmt(bp[i], 2) if i < len(bp) else "",
                _fmt(ap[i], 2) if i < len(ap) else "",
                _fmt(av[i]) if i < len(av) else "",
            ))

    def _update_large(self, d: dict):
        if not self._large_tree:
            return
        self._large_tree.delete(*self._large_tree.get_children())
        for r in reversed(d.get("recent_large", [])):
            side = r.get("side", 0)
            side_txt = "外盤" if side > 0 else ("內盤" if side < 0 else "平盤")
            tag = "outer" if side > 0 else "inner"
            t = (r.get("time", "") or "")[-8:]
            self._large_tree.insert("", "end", values=(
                t, side_txt, _fmt(r.get("price", 0), 2),
                _fmt(r.get("volume", 0)), f"{r.get('mult', 0):.1f}×",
            ), tags=(tag,))

    def _on_alert_log(self, v):
        def _u():
            self.log_textbox.configure(state="normal")
            self.log_textbox.delete("1.0", "end")
            self.log_textbox.insert("1.0", v or "")
            self.log_textbox.see("end")
            self.log_textbox.configure(state="disabled")
        self.after(0, _u)
