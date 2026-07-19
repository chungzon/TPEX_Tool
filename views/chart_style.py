"""共用圖表樣式 — 精緻財經風格 + 超取樣渲染。

設計目標：在 100% 顯示縮放下也有「retina」般的滑順線條與文字。做法為以
`SCALE` 倍解析度離屏（Agg）渲染，再用 PIL LANCZOS 縮回邏輯尺寸貼到 Tk
Label。所有圖表共用同一套色票與座標軸樣式，維持視覺一致。

用法：
    fig, ax = new_fig(w_in, h_in)
    ...在 ax 上畫...
    style_axis(ax)
    embed(parent, fig, w_in, h_in)     # 回傳 Label（已 pack）

圖為靜態影像；資料更新時由呼叫端清除舊 widget 後重畫（既有 render 模式）。
"""

from __future__ import annotations

import tkinter as tk

import numpy as np

try:
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.patches import Polygon
    import matplotlib.colors as mcolors
    import matplotlib.ticker as mticker
    from matplotlib.patheffects import withStroke
    matplotlib.rcParams["font.sans-serif"] = [
        "Microsoft JhengHei", "Microsoft YaHei", "SimHei", "sans-serif",
    ]
    matplotlib.rcParams["axes.unicode_minus"] = False
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

from PIL import Image, ImageTk

# ---- 色票（深色財經）----
BG = "#16171a"        # 圖背景
TXT = "#c7c7cc"       # 文字/刻度
GRID = "#2a2b2f"      # 格線
RED = "#ff5a5f"       # 漲 / 買超（台股慣例）
GREEN = "#2ec5a8"     # 跌 / 賣超
FLAT = "#8e8e93"      # 平盤 / 基準
AMBER = "#ffb300"     # 投信
PURPLE = "#b57bd6"    # 自營

_BASE_DPI = 100
_SCALE = 2            # 超取樣倍率


def up_down_color(v) -> str:
    if v is None or v == 0:
        return FLAT
    return RED if v > 0 else GREEN


def new_fig(w_in: float, h_in: float):
    """建立超取樣 Figure（回 fig, ax）。w_in/h_in 為邏輯英吋。

    使用 constrained layout 自動容納刻度/軸標/圖例，避免千分位標籤被裁切。
    """
    fig = Figure(figsize=(w_in, h_in), dpi=_BASE_DPI * _SCALE, facecolor=BG,
                 layout="constrained")
    fig.get_layout_engine().set(w_pad=0.02, h_pad=0.02, wspace=0, hspace=0)
    ax = fig.add_subplot(111)
    ax.set_facecolor(BG)
    return fig, ax


def style_axis(ax, ylabel: str = "", thousands: bool = True,
               ymaxticks: int = 5):
    """去框、淡格線、無刻度線、千分位——統一座標軸外觀。"""
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.grid(True, axis="y", alpha=0.14, color=GRID, linewidth=0.8)
    ax.tick_params(colors=TXT, labelsize=15, length=0)   # 15 ≈ 7.5pt @2x
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=16, color=TXT)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(ymaxticks))
    if thousands:
        ax.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda v, _: f"{int(v):,}"))


def gradient_fill(ax, xs, ys, baseline: float, color: str,
                  top_alpha: float = 0.30):
    """線下到 baseline 的垂直漸層填色（頂濃底透）。"""
    xs = list(xs)
    ys = list(ys)
    lo = min(min(ys), baseline)
    hi = max(max(ys), baseline)
    grad = np.empty((256, 1, 4))
    rgba = mcolors.to_rgba(color)
    grad[:, 0, 0] = rgba[0]
    grad[:, 0, 1] = rgba[1]
    grad[:, 0, 2] = rgba[2]
    grad[:, 0, 3] = np.linspace(top_alpha, 0.0, 256)
    im = ax.imshow(grad, aspect="auto", origin="upper",
                   extent=[xs[0], xs[-1], lo, hi], zorder=1)
    verts = list(zip(xs, ys)) + [(xs[-1], baseline), (xs[0], baseline)]
    clip = Polygon(verts, closed=True, transform=ax.transData)
    im.set_clip_path(clip)


def end_marker(ax, x, y, color: str, label: str):
    """線末端光點 + 帶描邊的數值標籤（不被線壓住）。"""
    ax.scatter([x], [y], s=90, color=color, zorder=6,
               edgecolors=BG, linewidths=2.4)
    ax.annotate(label, (x, y), xytext=(-8, 14), textcoords="offset points",
                ha="right", fontsize=17, color=color, fontweight="bold",
                zorder=7,
                path_effects=[withStroke(linewidth=4, foreground=BG)])


def line(ax, xs, ys, color: str, width: float = 1.6, **kw):
    """統一線條樣式：圓角、抗鋸齒。"""
    return ax.plot(xs, ys, color=color, linewidth=width * _SCALE,
                   solid_capstyle="round", solid_joinstyle="round",
                   antialiased=True, **kw)


def embed(parent, fig, w_in: float, h_in: float):
    """超取樣渲染 fig → 縮回邏輯尺寸 → 貼到 parent 的 Label（已 pack）。"""
    canvas = FigureCanvasAgg(fig)
    canvas.draw()
    w_px, h_px = canvas.get_width_height()
    img = Image.frombuffer("RGBA", (w_px, h_px),
                           canvas.buffer_rgba(), "raw", "RGBA", 0, 1)
    target = (int(w_in * _BASE_DPI), int(h_in * _BASE_DPI))
    img = img.resize(target, Image.LANCZOS)
    photo = ImageTk.PhotoImage(img)
    lbl = tk.Label(parent, image=photo, bd=0, highlightthickness=0, bg=BG)
    lbl.image = photo          # 保留參考避免被 GC
    lbl.pack(fill="both", expand=True, padx=2, pady=2)
    return lbl
