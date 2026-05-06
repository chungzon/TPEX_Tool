"""分點訊號 — Branch Alpha Model signal dashboard."""

from __future__ import annotations

import customtkinter as ctk
from tkinter import ttk
from datetime import datetime

from viewmodels.signal_viewmodel import SignalViewModel


def _fmt(n: int) -> str:
    return f"{n:,}"

def _lots(shares: int) -> str:
    return _fmt(shares // 1000)


_STYLE_INIT = False

def _ensure_style():
    global _STYLE_INIT
    if _STYLE_INIT:
        return
    _STYLE_INIT = True
    s = ttk.Style()
    for name in ("Signal.Treeview", "Alpha.Treeview"):
        s.configure(name,
                    background="#252526", foreground="#d4d4d4",
                    fieldbackground="#252526", borderwidth=0,
                    rowheight=28, font=("Microsoft JhengHei", 11))
        s.map(name,
              background=[("selected", "#264f78")],
              foreground=[("selected", "#ffffff")])
        s.configure(f"{name}.Heading",
                    background="#2d2d2d", foreground="#cccccc",
                    borderwidth=0, relief="flat",
                    font=("Microsoft JhengHei", 11, "bold"))
        s.map(f"{name}.Heading",
              background=[("active", "#3e3e3e")])


class SignalView(ctk.CTkFrame):
    """分點訊號 tab page."""

    def __init__(self, parent: ctk.CTkFrame, viewmodel: SignalViewModel):
        super().__init__(parent, fg_color="transparent")
        self.vm = viewmodel
        self._build_ui()
        self._bind_vm()

    def _build_ui(self):
        container = ctk.CTkScrollableFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True)

        # Title
        ctk.CTkLabel(
            container, text="分點訊號",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(pady=(24, 8))
        ctk.CTkLabel(
            container,
            text="異常偵測 × 分點Alpha × 群聚分析 → 綜合訊號評分",
            font=ctk.CTkFont(size=14), text_color="gray",
        ).pack(pady=(0, 20))

        # Parameters
        param_card = ctk.CTkFrame(container, corner_radius=12)
        param_card.pack(padx=40, pady=8, fill="x")

        param_row = ctk.CTkFrame(param_card, fg_color="transparent")
        param_row.pack(fill="x", padx=24, pady=16)

        ctk.CTkLabel(param_row, text="分析日期：",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.date_entry = ctk.CTkEntry(
            param_row, width=130, font=ctk.CTkFont(size=14),
            placeholder_text="yyyy-mm-dd")
        self.date_entry.pack(side="left", padx=(4, 16))
        self.date_entry.insert(0, datetime.now().strftime("%Y-%m-%d"))

        ctk.CTkLabel(param_row, text="回溯天數：",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.lookback_entry = ctk.CTkEntry(
            param_row, width=60, font=ctk.CTkFont(size=14))
        self.lookback_entry.pack(side="left", padx=(4, 16))
        self.lookback_entry.insert(0, "60")

        self.run_btn = ctk.CTkButton(
            param_row, text="開始分析", width=120, height=36,
            corner_radius=8,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color="#ab47bc", hover_color="#8e24aa",
            command=self._on_run,
        )
        self.run_btn.pack(side="left")

        self.status_label = ctk.CTkLabel(
            param_row, text="", font=ctk.CTkFont(size=13),
            text_color="#4ECDC4")
        self.status_label.pack(side="left", padx=(12, 0))

        self.error_label = ctk.CTkLabel(
            param_row, text="", font=ctk.CTkFont(size=13),
            text_color="#FF6B6B")
        self.error_label.pack(side="left", padx=(8, 0))

        # Legend
        legend_card = ctk.CTkFrame(container, corner_radius=12)
        legend_card.pack(padx=40, pady=4, fill="x")

        legend_row = ctk.CTkFrame(legend_card, fg_color="transparent")
        legend_row.pack(fill="x", padx=24, pady=10)

        hints = [
            ("訊號分數", "#ffeb3b", "綜合評分 0~1"),
            ("Z-score", "#42a5f5", "買超異常程度"),
            ("佔比%", "#c0c0c0", "分點買進/成交量"),
            ("連續", "#ff9800", "連續同方向天數"),
            ("群聚", "#ba68c8", "同日多分點同買"),
            ("Alpha", "#26a69a", "歷史預測力"),
            ("D+1", "#ef5350", "隔日報酬%"),
        ]
        for label, clr, desc in hints:
            ctk.CTkLabel(
                legend_row, text=f"● {label}",
                font=ctk.CTkFont(size=11, weight="bold"),
                text_color=clr,
            ).pack(side="left", padx=(0, 4))
            ctk.CTkLabel(
                legend_row, text=desc,
                font=ctk.CTkFont(size=11), text_color="#666",
            ).pack(side="left", padx=(0, 12))

        # Signal results
        ctk.CTkLabel(
            container, text="今日異常分點訊號",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=44, pady=(12, 4))

        self.signal_frame = ctk.CTkFrame(container, corner_radius=12)
        self.signal_frame.pack(padx=40, pady=4, fill="both", expand=True)

        # Branch Alpha ranking
        ctk.CTkLabel(
            container, text="分點 Alpha 排名（歷史預測力）",
            font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=44, pady=(12, 4))

        self.alpha_frame = ctk.CTkFrame(container, corner_radius=12)
        self.alpha_frame.pack(padx=40, pady=4, fill="both", expand=True)

    # Events

    def _on_run(self):
        date = self.date_entry.get().strip()
        try:
            lb = int(self.lookback_entry.get().strip())
        except ValueError:
            lb = 60
        self.vm.run_analysis(date, lb)

    # Bindings

    def _bind_vm(self):
        self.vm.bind("signals", self._on_signals)
        self.vm.bind("branch_alphas", self._on_alphas)
        self.vm.bind("loading", self._on_loading)
        self.vm.bind("error_text", self._on_error)
        self.vm.bind("status_text", self._on_status)

    def _on_loading(self, v):
        def _u():
            if v:
                self.run_btn.configure(state="disabled", text="分析中...")
            else:
                self.run_btn.configure(state="normal", text="開始分析")
        self.after(0, _u)

    def _on_error(self, v):
        self.after(0, lambda: self.error_label.configure(text=v))

    def _on_status(self, v):
        self.after(0, lambda: self.status_label.configure(text=v))

    def _on_signals(self, data):
        def _u():
            for w in self.signal_frame.winfo_children():
                w.destroy()
            if not data:
                ctk.CTkLabel(
                    self.signal_frame, text="（無訊號）",
                    font=ctk.CTkFont(size=13), text_color="gray",
                ).pack(pady=12)
                return

            _ensure_style()

            columns = ("rank", "code", "name", "action", "net",
                       "score", "z", "share", "consec", "cluster",
                       "alpha", "d1", "d3")
            tree = ttk.Treeview(
                self.signal_frame, columns=columns, show="headings",
                style="Signal.Treeview", height=min(len(data), 25))

            hdrs = {
                "rank": "#", "code": "代碼", "name": "名稱",
                "action": "分點", "net": "淨量(張)",
                "score": "訊號分數", "z": "Z-score",
                "share": "佔比%", "consec": "連續",
                "cluster": "群聚", "alpha": "Alpha",
                "d1": "D+1%", "d3": "D+3%",
            }
            widths = {
                "rank": 30, "code": 50, "name": 65,
                "action": 90, "net": 65,
                "score": 60, "z": 55, "share": 50,
                "consec": 40, "cluster": 40,
                "alpha": 50, "d1": 50, "d3": 50,
            }
            anchors = {
                "rank": "center", "code": "center", "name": "w",
                "action": "w", "net": "e",
                "score": "e", "z": "e", "share": "e",
                "consec": "e", "cluster": "e",
                "alpha": "e", "d1": "e", "d3": "e",
            }

            for c in columns:
                tree.heading(c, text=hdrs[c])
                tree.column(c, width=widths[c], anchor=anchors[c], stretch=True)

            tree.tag_configure("strong", foreground="#ffeb3b")
            tree.tag_configure("good", foreground="#ff9800")
            tree.tag_configure("normal", foreground="#d4d4d4")
            tree.tag_configure("sell", foreground="#26a69a")

            for i, s in enumerate(data[:50], 1):
                if s.signal_score >= 0.5:
                    tag = "strong"
                elif s.signal_score >= 0.3:
                    tag = "good"
                elif s.net_volume < 0:
                    tag = "sell"
                else:
                    tag = "normal"

                d1_str = f"{s.d1_return:+.1f}" if s.d1_return is not None else "—"
                d3_str = f"{s.d3_return:+.1f}" if s.d3_return is not None else "—"

                tree.insert("", "end", values=(
                    i, s.stock_code, s.stock_name,
                    s.broker_name,
                    _lots(s.net_volume),
                    f"{s.signal_score:.3f}",
                    f"{s.net_buy_z:+.1f}",
                    f"{s.volume_share:.1f}",
                    s.consecutive_days,
                    int(s.cluster_score),
                    f"{s.branch_alpha:.2f}",
                    d1_str, d3_str,
                ), tags=(tag,))

            sb = ttk.Scrollbar(
                self.signal_frame, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=sb.set)
            tree.pack(side="left", fill="both", expand=True,
                      padx=(8, 0), pady=8)
            sb.pack(side="right", fill="y", padx=(0, 8), pady=8)

        self.after(0, _u)

    def _on_alphas(self, data):
        def _u():
            for w in self.alpha_frame.winfo_children():
                w.destroy()
            if not data:
                ctk.CTkLabel(
                    self.alpha_frame, text="（無資料）",
                    font=ctk.CTkFont(size=13), text_color="gray",
                ).pack(pady=12)
                return

            _ensure_style()

            columns = ("rank", "name", "signals", "wins",
                       "winrate", "d1_avg", "d3_avg", "d5_avg",
                       "max_high", "alpha")
            tree = ttk.Treeview(
                self.alpha_frame, columns=columns, show="headings",
                style="Alpha.Treeview", height=min(len(data), 20))

            hdrs = {
                "rank": "#", "name": "分點", "signals": "訊號數",
                "wins": "勝次", "winrate": "勝率",
                "d1_avg": "D+1均%", "d3_avg": "D+3均%",
                "d5_avg": "D+5均%", "max_high": "D+1最高%",
                "alpha": "Alpha",
            }
            widths = {
                "rank": 30, "name": 100, "signals": 55,
                "wins": 45, "winrate": 50,
                "d1_avg": 60, "d3_avg": 60, "d5_avg": 60,
                "max_high": 65, "alpha": 55,
            }

            for c in columns:
                tree.heading(c, text=hdrs[c])
                tree.column(c, width=widths[c],
                            anchor="e" if c != "name" else "w",
                            stretch=True)

            tree.tag_configure("top", foreground="#ffeb3b")
            tree.tag_configure("good", foreground="#26a69a")
            tree.tag_configure("weak", foreground="#888888")

            for i, a in enumerate(data[:30], 1):
                if a.alpha_score >= 0.4:
                    tag = "top"
                elif a.alpha_score >= 0.25:
                    tag = "good"
                else:
                    tag = "weak"

                tree.insert("", "end", values=(
                    i, a.broker_name, a.buy_signals,
                    a.d1_win_count,
                    f"{a.win_rate:.0%}",
                    f"{a.d1_avg_return:+.2f}",
                    f"{a.d3_avg_return:+.2f}",
                    f"{a.d5_avg_return:+.2f}",
                    f"{a.d1_avg_max_high:+.2f}",
                    f"{a.alpha_score:.3f}",
                ), tags=(tag,))

            sb = ttk.Scrollbar(
                self.alpha_frame, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=sb.set)
            tree.pack(side="left", fill="both", expand=True,
                      padx=(8, 0), pady=8)
            sb.pack(side="right", fill="y", padx=(0, 8), pady=8)

        self.after(0, _u)
