"""MEDE 「Tick 發動偵測」分頁 — Phase 2：原始資料錄製控制與狀態監看。

後續階段（偵測器面板 / 回測頁）在此頁繼續擴充。
"""

from __future__ import annotations

import customtkinter as ctk
from tkinter import ttk

from viewmodels.mede_viewmodel import MedeViewModel


class MedeView(ctk.CTkFrame):
    def __init__(self, parent, viewmodel: MedeViewModel):
        super().__init__(parent, fg_color="transparent")
        self.vm = viewmodel
        self._q_tree: ttk.Treeview | None = None
        self._build_ui()
        self._bind_vm()

    def _build_ui(self):
        container = ctk.CTkScrollableFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True)

        # ---- 標題 ----
        hdr = ctk.CTkFrame(container, corner_radius=12)
        hdr.pack(fill="x", padx=30, pady=(12, 6))
        ctk.CTkLabel(hdr, text="🛰 Tick 發動偵測 · 原始資料錄製（Phase 2）",
                     font=ctk.CTkFont(size=17, weight="bold")).pack(
                         side="left", padx=18, pady=12)
        ctk.CTkLabel(hdr, text="即時錄製 Tick + 五檔 BidAsk（五檔事件事後無法還原，須從現在起累積）",
                     font=ctk.CTkFont(size=11), text_color="#888888").pack(
                         side="left", padx=(4, 0))

        # ---- 追蹤設定 ----
        card = ctk.CTkFrame(container, corner_radius=12)
        card.pack(fill="x", padx=30, pady=6)
        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=(14, 6))
        ctk.CTkLabel(row, text="股票代碼（空白或逗號分隔，最多 5 檔）：",
                     font=ctk.CTkFont(size=13)).pack(side="left")
        self.code_entry = ctk.CTkEntry(row, width=240, font=ctk.CTkFont(size=13),
                                       placeholder_text="例：1815 2330 2317")
        self.code_entry.pack(side="left", padx=(6, 12))
        saved = self.vm.saved_symbols
        if saved:
            self.code_entry.insert(0, " ".join(saved))

        self.start_btn = ctk.CTkButton(
            row, text="開始錄製", width=110, height=34, corner_radius=8,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#2e9e6b", hover_color="#247e55", command=self._on_start)
        self.start_btn.pack(side="left")
        self.stop_btn = ctk.CTkButton(
            row, text="停止", width=80, height=34, corner_radius=8,
            font=ctk.CTkFont(size=13), fg_color="#b3453b", hover_color="#8f372f",
            command=self._on_stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(8, 0))

        self.msg_label = ctk.CTkLabel(card, text="尚未開始（即時行情需先於『下單/大單追蹤』連線永豐正式環境）",
                                      font=ctk.CTkFont(size=12), text_color="#9aa4ad")
        self.msg_label.pack(anchor="w", padx=18, pady=(0, 12))

        # ---- 即時狀態 tiles ----
        st_card = ctk.CTkFrame(container, corner_radius=12)
        st_card.pack(fill="x", padx=30, pady=6)
        ctk.CTkLabel(st_card, text="即時錄製狀態",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(
                         anchor="w", padx=18, pady=(12, 2))
        grid = ctk.CTkFrame(st_card, fg_color="transparent")
        grid.pack(fill="x", padx=14, pady=(0, 12))
        self._tiles: dict[str, ctk.CTkLabel] = {}
        specs = [("state", "狀態"), ("codes", "標的"), ("date", "交易日"),
                 ("market", "盤中"), ("queue", "佇列"), ("dropped", "丟棄"),
                 ("tick_lag", "Tick延遲"), ("bidask_lag", "五檔延遲"),
                 ("writer", "Writer")]
        for i, (key, label) in enumerate(specs):
            cell = ctk.CTkFrame(grid, corner_radius=8)
            cell.grid(row=i // 5, column=i % 5, padx=5, pady=5, sticky="nsew")
            grid.grid_columnconfigure(i % 5, weight=1)
            ctk.CTkLabel(cell, text=label, font=ctk.CTkFont(size=11),
                         text_color="#888").pack(pady=(6, 0))
            v = ctk.CTkLabel(cell, text="—", font=ctk.CTkFont(size=14, weight="bold"))
            v.pack(pady=(0, 6))
            self._tiles[key] = v

        # ---- 每檔資料品質 ----
        q_card = ctk.CTkFrame(container, corner_radius=12)
        q_card.pack(fill="x", padx=30, pady=6)
        ctk.CTkLabel(q_card, text="每檔資料品質",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(
                         anchor="w", padx=18, pady=(12, 4))
        self._build_quality_tree(q_card)

        # ---- 偵測結果（Phase 5：對已錄製資料跑偵測管線）----
        self._build_detect_panel(container)

    def _build_quality_tree(self, parent):
        style = ttk.Style()
        style.theme_use("default")
        style.configure("Mede.Treeview", background="#1a222c",
                        fieldbackground="#1a222c", foreground="#e0e0e0",
                        rowheight=22, borderwidth=0)
        style.configure("Mede.Treeview.Heading", background="#263238",
                        foreground="#c0c0c0", font=("", 10, "bold"))
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=10, pady=(0, 12))
        cols = ("code", "ticks", "bidask", "unknown", "ooo", "gap", "status", "last")
        tree = ttk.Treeview(frame, columns=cols, show="headings", height=6,
                            style="Mede.Treeview")
        for c, txt, w, anc in [
            ("code", "代碼", 70, "center"), ("ticks", "Tick數", 80, "e"),
            ("bidask", "五檔數", 80, "e"), ("unknown", "未知方向", 80, "e"),
            ("ooo", "亂序", 60, "e"), ("gap", "最大間隔ms", 90, "e"),
            ("status", "品質", 80, "center"), ("last", "最後Tick", 100, "center"),
        ]:
            tree.heading(c, text=txt)
            tree.column(c, width=w, anchor=anc, stretch=(c == "last"))
        tree.tag_configure("ok", foreground="#26a69a")
        tree.tag_configure("degraded", foreground="#ffb74d")
        tree.tag_configure("invalid", foreground="#ef5350")
        tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._q_tree = tree

    def _build_detect_panel(self, container):
        card = ctk.CTkFrame(container, corner_radius=12)
        card.pack(fill="both", expand=True, padx=30, pady=(6, 16))
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=18, pady=(12, 2))
        ctk.CTkLabel(head, text="🎯 發動偵測結果（Phase 5）",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(side="left")
        ctk.CTkLabel(head, text="對已錄製的逐筆資料跑偵測管線，產生候選事件（僅供檢視，不下單）",
                     font=ctk.CTkFont(size=11), text_color="#888").pack(
                         side="left", padx=(8, 0))

        ctrl = ctk.CTkFrame(card, fg_color="transparent")
        ctrl.pack(fill="x", padx=18, pady=(6, 4))
        ctk.CTkLabel(ctrl, text="交易日：", font=ctk.CTkFont(size=13)).pack(side="left")
        self.date_menu = ctk.CTkOptionMenu(
            ctrl, width=140, values=["—"], command=self._on_pick_date)
        self.date_menu.pack(side="left", padx=(4, 12))
        ctk.CTkLabel(ctrl, text="代碼：", font=ctk.CTkFont(size=13)).pack(side="left")
        self.dcode_menu = ctk.CTkOptionMenu(ctrl, width=110, values=["—"])
        self.dcode_menu.pack(side="left", padx=(4, 12))
        self.detect_btn = ctk.CTkButton(
            ctrl, text="跑偵測", width=90, height=32, corner_radius=8,
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#3b6fb3", hover_color="#2f588f", command=self._on_detect)
        self.detect_btn.pack(side="left")
        self.detect_all_btn = ctk.CTkButton(
            ctrl, text="批次偵測（全部）", width=130, height=32, corner_radius=8,
            font=ctk.CTkFont(size=13), fg_color="#5a4b9e", hover_color="#463b7d",
            command=self._on_detect_all)
        self.detect_all_btn.pack(side="left", padx=(8, 0))
        self.refresh_btn = ctk.CTkButton(
            ctrl, text="⟳ 重整", width=74, height=32, corner_radius=8,
            font=ctk.CTkFont(size=12), fg_color="#455a64", hover_color="#37474f",
            command=lambda: self.vm.refresh_dates())
        self.refresh_btn.pack(side="left", padx=(8, 0))

        self.detect_msg = ctk.CTkLabel(card, text="讀取錄製資料中…",
                                       font=ctk.CTkFont(size=12), text_color="#9aa4ad")
        self.detect_msg.pack(anchor="w", padx=18, pady=(2, 6))
        self._build_event_tree(card)

    def _build_event_tree(self, parent):
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.pack(fill="both", expand=True, padx=10, pady=(0, 12))
        cols = ("code", "time", "type", "dir", "score", "conf", "price",
                "pattern", "reason")
        tree = ttk.Treeview(frame, columns=cols, show="headings", height=8,
                            style="Mede.Treeview")
        for c, txt, w, anc in [
            ("code", "代碼", 60, "center"), ("time", "時間", 100, "center"),
            ("type", "事件", 130, "w"),
            ("dir", "方向", 50, "center"), ("score", "分數", 60, "e"),
            ("conf", "信心", 60, "e"), ("price", "觸發價", 70, "e"),
            ("pattern", "型態", 150, "w"), ("reason", "主要理由", 240, "w"),
        ]:
            tree.heading(c, text=txt)
            tree.column(c, width=w, anchor=anc, stretch=(c == "reason"))
        tree.tag_configure("bull", foreground="#26a69a")
        tree.tag_configure("bear", foreground="#ef5350")
        tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self._ev_tree = tree

    # ---------------- events ----------------
    def _on_start(self):
        self.vm.start(self.code_entry.get())

    def _on_pick_date(self, date: str):
        if date and date != "—":
            self.vm.load_codes(date)

    def _on_detect(self):
        date = self.date_menu.get()
        code = self.dcode_menu.get()
        self.vm.run_detection(date, code)

    def _on_detect_all(self):
        self.vm.run_all_detection(self.date_menu.get())

    def _on_stop(self):
        self.vm.stop()

    def _bind_vm(self):
        self.vm.bind("is_recording", self._on_recording)
        self.vm.bind("status_msg", self._on_msg)
        self.vm.bind("status_data", self._on_status)
        self.vm.bind("detect_dates", self._on_detect_dates)
        self.vm.bind("detect_codes", self._on_detect_codes)
        self.vm.bind("detect_events", self._on_detect_events)
        self.vm.bind("detect_msg", self._on_detect_msg)
        self.vm.bind("detect_running", self._on_detect_running)
        self.vm.refresh_dates()

    def _on_recording(self, v):
        def _u():
            self.start_btn.configure(state="disabled" if v else "normal")
            self.stop_btn.configure(state="normal" if v else "disabled")
        self.after(0, _u)

    def _on_msg(self, v):
        clr = "#4ECDC4" if v.startswith("✓") else (
            "#FF6B6B" if "無法" in v else "#9aa4ad")
        self.after(0, lambda: self.msg_label.configure(text=v, text_color=clr))

    @staticmethod
    def _lag(ms):
        if ms is None:
            return "—"
        return f"{ms/1000:.1f}s" if ms >= 1000 else f"{ms:.0f}ms"

    def _on_status(self, d):
        if not d:
            return

        def _u():
            t = self._tiles
            t["state"].configure(
                text="錄製中" if d.get("is_recording") else "停止",
                text_color="#26a69a" if d.get("is_recording") else "#9aa4ad")
            t["codes"].configure(text="、".join(d.get("codes", [])) or "—")
            t["date"].configure(text=d.get("trade_date", "—"))
            t["market"].configure(text="是" if d.get("in_market_hours") else "否")
            t["queue"].configure(text=str(d.get("queue_size", "—")))
            dropped = d.get("dropped_count", 0)
            t["dropped"].configure(text=str(dropped),
                                   text_color="#ef5350" if dropped else "#e0e0e0")
            t["tick_lag"].configure(text=self._lag(d.get("tick_lag_ms")))
            t["bidask_lag"].configure(text=self._lag(d.get("bidask_lag_ms")))
            t["writer"].configure(
                text="OK" if d.get("writer_alive") else "停",
                text_color="#26a69a" if d.get("writer_alive") else "#ef5350")
            if self._q_tree:
                self._q_tree.delete(*self._q_tree.get_children())
                for code, q in (d.get("per_code") or {}).items():
                    stt = q.get("status", "")
                    self._q_tree.insert("", "end", values=(
                        code, f"{q.get('ticks', 0):,}", f"{q.get('bidask', 0):,}",
                        f"{q.get('unknown', 0):,}", q.get("out_of_order", 0),
                        f"{q.get('max_gap_ms', 0):.0f}", stt,
                        (q.get("last_tick_time", "") or "")[-12:],
                    ), tags=(stt,))
        self.after(0, _u)

    # ---------------- 偵測結果 callbacks ----------------
    def _on_detect_dates(self, dates):
        def _u():
            vals = list(dates) if dates else ["—"]
            self.date_menu.configure(values=vals)
            self.date_menu.set(vals[0])
        self.after(0, _u)

    def _on_detect_codes(self, codes):
        def _u():
            vals = list(codes) if codes else ["—"]
            self.dcode_menu.configure(values=vals)
            self.dcode_menu.set(vals[0])
        self.after(0, _u)

    def _on_detect_events(self, events):
        def _u():
            self._ev_tree.delete(*self._ev_tree.get_children())
            for e in (events or []):
                d = e.get("dir", 0)
                arrow = "▲多" if d > 0 else ("▼空" if d < 0 else "—")
                tag = "bull" if d > 0 else ("bear" if d < 0 else "")
                self._ev_tree.insert("", "end", values=(
                    e.get("code", ""), e.get("time", ""), e.get("type", ""), arrow,
                    f"{e.get('score', 0):.0f}", f"{e.get('conf', 0):.2f}",
                    f"{e.get('price', 0):g}", e.get("patterns", ""),
                    e.get("reason", ""),
                ), tags=(tag,))
        self.after(0, _u)

    def _on_detect_msg(self, v):
        clr = "#4ECDC4" if v.startswith("✓") else (
            "#FF6B6B" if ("失敗" in v or "請先" in v) else "#9aa4ad")
        self.after(0, lambda: self.detect_msg.configure(text=v, text_color=clr))

    def _on_detect_running(self, running):
        def _u():
            self.detect_btn.configure(
                state="disabled" if running else "normal",
                text="偵測中…" if running else "跑偵測")
            self.detect_all_btn.configure(state="disabled" if running else "normal")
        self.after(0, _u)
