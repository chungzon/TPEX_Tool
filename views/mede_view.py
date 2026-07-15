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
        q_card.pack(fill="both", expand=True, padx=30, pady=(6, 16))
        ctk.CTkLabel(q_card, text="每檔資料品質",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(
                         anchor="w", padx=18, pady=(12, 4))
        self._build_quality_tree(q_card)

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

    # ---------------- events ----------------
    def _on_start(self):
        self.vm.start(self.code_entry.get())

    def _on_stop(self):
        self.vm.stop()

    def _bind_vm(self):
        self.vm.bind("is_recording", self._on_recording)
        self.vm.bind("status_msg", self._on_msg)
        self.vm.bind("status_data", self._on_status)

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
