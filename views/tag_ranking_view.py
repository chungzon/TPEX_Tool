"""主力分點排行 — top stocks by broker-type buy volume share."""

from __future__ import annotations

import customtkinter as ctk
from tkinter import ttk
from datetime import datetime

from viewmodels.tag_ranking_viewmodel import TagRankingViewModel
from services.broker_tags import (
    TAG_DAY, TAG_NEXT, TAG_SHORT, TAG_COLORS, TAG_LABELS,
)


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
    for name in ("TagRank.Treeview",):
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


class TagRankingView(ctk.CTkFrame):
    """主力分點排行 tab page."""

    def __init__(self, parent: ctk.CTkFrame, viewmodel: TagRankingViewModel):
        super().__init__(parent, fg_color="transparent")
        self.vm = viewmodel
        self._trees: dict[str, ttk.Treeview] = {}
        self._build_ui()
        self._bind_vm()

    def _build_ui(self):
        container = ctk.CTkScrollableFrame(self, fg_color="transparent")
        container.pack(fill="both", expand=True)

        # Title
        ctk.CTkLabel(
            container, text="主力分點排行",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(pady=(24, 8))
        ctk.CTkLabel(
            container,
            text="依日期查詢當沖 / 隔日沖 / 短線券商買入佔比最高的股票",
            font=ctk.CTkFont(size=14), text_color="gray",
        ).pack(pady=(0, 20))

        # Date input row
        date_card = ctk.CTkFrame(container, corner_radius=12)
        date_card.pack(padx=40, pady=8, fill="x")
        date_row = ctk.CTkFrame(date_card, fg_color="transparent")
        date_row.pack(fill="x", padx=20, pady=16)

        ctk.CTkLabel(date_row, text="交易日期：",
                      font=ctk.CTkFont(size=14)).pack(side="left")
        self.date_entry = ctk.CTkEntry(
            date_row, width=130, font=ctk.CTkFont(size=14),
            placeholder_text="yyyy-mm-dd",
        )
        self.date_entry.pack(side="left", padx=(4, 8))
        # Default to today
        self.date_entry.insert(0, datetime.now().strftime("%Y-%m-%d"))

        self.query_btn = ctk.CTkButton(
            date_row, text="查詢", width=80, height=34,
            corner_radius=8, font=ctk.CTkFont(size=14, weight="bold"),
            command=self._on_query,
        )
        self.query_btn.pack(side="left")

        self.error_label = ctk.CTkLabel(
            date_row, text="", font=ctk.CTkFont(size=13),
            text_color="#FF6B6B",
        )
        self.error_label.pack(side="left", padx=(12, 0))

        # Three ranking tables side by side
        tables_frame = ctk.CTkFrame(container, fg_color="transparent")
        tables_frame.pack(padx=20, pady=8, fill="both", expand=True)
        tables_frame.columnconfigure(0, weight=1)
        tables_frame.columnconfigure(1, weight=1)
        tables_frame.columnconfigure(2, weight=1)

        for col, (tag, title) in enumerate([
            (TAG_DAY, "當沖主力"),
            (TAG_NEXT, "隔日沖主力"),
            (TAG_SHORT, "短線主力"),
        ]):
            card = ctk.CTkFrame(tables_frame, corner_radius=10)
            card.grid(row=0, column=col, sticky="nsew", padx=4, pady=4)

            # Header with colored accent
            hdr = ctk.CTkFrame(card, fg_color="transparent")
            hdr.pack(fill="x", padx=12, pady=(10, 4))
            ctk.CTkLabel(
                hdr, text="●", font=ctk.CTkFont(size=14),
                text_color=TAG_COLORS[tag],
            ).pack(side="left")
            ctk.CTkLabel(
                hdr, text=f" {title} 買入佔比 Top 20",
                font=ctk.CTkFont(size=13, weight="bold"),
            ).pack(side="left")

            # Treeview
            tree_frame = ctk.CTkFrame(card, fg_color="transparent")
            tree_frame.pack(fill="both", expand=True, padx=4, pady=(0, 8))

            _ensure_style()
            columns = ("rank", "code", "name", "price", "ratio")
            tree = ttk.Treeview(
                tree_frame, columns=columns, show="headings",
                style="TagRank.Treeview", height=20,
            )
            headings = {
                "rank": "#", "code": "代碼", "name": "名稱",
                "price": "收盤", "ratio": "佔比%",
            }
            widths = {"rank": 30, "code": 55, "name": 75, "price": 60, "ratio": 58}
            anchors = {
                "rank": "center", "code": "center", "name": "w",
                "price": "e", "ratio": "e",
            }
            for c in columns:
                tree.heading(c, text=headings[c])
                tree.column(c, width=widths[c], anchor=anchors[c], stretch=True)

            tree.tag_configure("hot", foreground=TAG_COLORS[tag])

            sb = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=sb.set)
            tree.pack(side="left", fill="both", expand=True, padx=(4, 0), pady=4)
            sb.pack(side="right", fill="y", padx=(0, 4), pady=4)

            self._trees[tag] = tree

    # Events

    def _on_query(self):
        d = self.date_entry.get().strip()
        self.vm.load_rankings(d)

    # Bindings

    def _bind_vm(self):
        self.vm.bind("rankings", self._on_rankings)
        self.vm.bind("error_text", self._on_error)
        self.vm.bind("loading", self._on_loading)

    def _on_error(self, v):
        self.after(0, lambda: self.error_label.configure(text=v))

    def _on_loading(self, v):
        def _u():
            if v:
                self.query_btn.configure(state="disabled", text="查詢中...")
            else:
                self.query_btn.configure(state="normal", text="查詢")
        self.after(0, _u)

    def _on_rankings(self, data):
        def _u():
            if data is None:
                for tree in self._trees.values():
                    tree.delete(*tree.get_children())
                return
            for tag, tree in self._trees.items():
                tree.delete(*tree.get_children())
                rows = data.get(tag, [])
                for i, r in enumerate(rows, 1):
                    price = r.get("close_price", "")
                    try:
                        price = f"{float(str(price).replace(',', '')):,.2f}"
                    except (ValueError, TypeError):
                        pass
                    tag_style = "hot" if r["ratio"] >= 10 else ""
                    tree.insert(
                        "", "end",
                        values=(
                            i,
                            r["stock_code"],
                            r["stock_name"],
                            price,
                            f"{r['ratio']:.1f}",
                        ),
                        tags=(tag_style,) if tag_style else (),
                    )
        self.after(0, _u)
