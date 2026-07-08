"""依股價 / 量能動態決定微觀結構偵測參數。

學理依據（市場微觀結構 Market Microstructure）
------------------------------------------------
市場微觀結構演算法的門檻**不能一體適用**，必須依標的的「股價區間、流動性、
市場深度」縮放，否則同一組參數在高價深盤股與低價淺碟股上會嚴重失真。

* **OBI 門檻 ∝ 市場深度**（Cont, *The Price Impact of Order Book Events*）：
  委託單失衡對股價的衝擊與市場深度成反比。高價／大型股委託簿極深，需 OBI
  連續維持在 0.75 以上才視為發動；低價／淺碟股 0.60 就可能噴發，門檻需放寬
  以提升敏感度。
* **VPIN 量桶 ∝ 日均量**（Easley et al., *Flow Toxicity and Liquidity in a
  High-Frequency World*）：VPIN 的核心是「成交量同步化」，量桶必須設為日均量
  （ADV）的固定比例（此處 1%），高價低量股桶子自動變小、低價高量股變大，
  跨股價區間才具統計可比性。
* **大單門檻 ∝ 成交量厚尾分布**：個股成交量呈不對稱厚尾，大單不能用固定「張數」，
  應以歷史每筆量的高百分位數（95%）或滾動均量倍數動態界定。

本模組為 **pure-compute**：吃 (股價, 日均量張數, 選配的歷史每筆量) → 回傳一個
已縮放好的 :class:`MicroConfig`。不做任何 I/O，方便測試與重用。
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from services.microstructure_service import MicroConfig


def _percentile(values: list[float], pct: float) -> float:
    """第 ``pct`` 百分位數（線性內插），不依賴 numpy。空序列回 0。"""
    xs = sorted(v for v in values if v > 0)
    if not xs:
        return 0.0
    if len(xs) == 1:
        return xs[0]
    rank = (pct / 100.0) * (len(xs) - 1)
    lo = int(rank)
    frac = rank - lo
    if lo + 1 >= len(xs):
        return xs[-1]
    return xs[lo] + (xs[lo + 1] - xs[lo]) * frac


def _round_step(x: float, step: float) -> int:
    """四捨五入到最接近的 ``step`` 倍數（至少一個 step），取整。"""
    return int(max(step, round(x / step) * step))


@dataclass
class ParamBasis:
    """推導參數所依據的原始輸入（供 UI 顯示「為什麼是這組值」）。"""
    price: float
    adv_lots: float          # 日均成交量（張）
    tick_count: int = 0      # 用來算百分位的歷史每筆量筆數（0=未使用）
    band: str = ""           # 股價分帶名稱


class AdaptiveParameterManager:
    """依股價 / 日均量 / 歷史每筆量，動態產生 :class:`MicroConfig`。"""

    def __init__(self, price: float, adv_lots: float,
                 tick_sizes: list[float] | None = None):
        self.price = max(0.0, float(price or 0.0))
        self.adv_lots = max(0.0, float(adv_lots or 0.0))
        self.tick_sizes = [float(v) for v in (tick_sizes or []) if v and v > 0]

    # ---- 股價分帶：決定與「市場深度」相關的門檻 ----
    def _price_band(self) -> dict:
        p = self.price
        if p >= 500:
            return dict(name="高價深盤(≥500)", obi=0.75, sustain=6,
                        push=0.78, attack=3)
        if p >= 100:
            return dict(name="中高價(100–500)", obi=0.68, sustain=5,
                        push=0.75, attack=2)
        if p >= 50:
            return dict(name="中價(50–100)", obi=0.64, sustain=5,
                        push=0.74, attack=2)
        return dict(name="低價淺碟(<50)", obi=0.60, sustain=4,
                    push=0.72, attack=2)

    # ---- VPIN 量桶：日均量的 1%（Easley et al.）----
    def _bucket_size(self) -> float:
        if self.adv_lots <= 0:
            return MicroConfig.bucket_size  # 無量能資料 → 沿用預設
        return float(min(5000, _round_step(self.adv_lots * 0.01, 50)))

    # ---- 大單最低門檻：優先用歷史每筆量 95 百分位（厚尾法）----
    def _large_order_floor(self) -> float:
        if len(self.tick_sizes) >= 30:
            p95 = _percentile(self.tick_sizes, 95)
            if p95 > 0:
                return float(max(5, round(p95)))
        if self.adv_lots > 0:
            # 退而求其次：日均量的 0.05% 作為地板，濾除開盤均量過小的誤判
            return float(max(5, _round_step(self.adv_lots * 0.0005, 5)))
        return MicroConfig.large_order_floor

    def _iceberg_min_visible(self) -> float:
        if self.adv_lots > 0:
            return float(max(5, _round_step(self.adv_lots * 0.0004, 5)))
        return MicroConfig.iceberg_min_visible

    def build(self) -> tuple[MicroConfig, ParamBasis]:
        """回傳 (縮放後的 MicroConfig, 推導依據)。以 MicroConfig() 預設為底，只覆寫
        與股價 / 量能相關的欄位，其餘（窗口長度、倍數等）沿用穩健預設值。"""
        band = self._price_band()
        cfg = replace(
            MicroConfig(),
            obi_threshold=band["obi"],
            obi_sustain_ticks=band["sustain"],
            buy_push_ratio=band["push"],
            attack_consecutive=band["attack"],
            bucket_size=self._bucket_size(),
            large_order_floor=self._large_order_floor(),
            iceberg_min_visible=self._iceberg_min_visible(),
        )
        basis = ParamBasis(
            price=self.price, adv_lots=self.adv_lots,
            tick_count=len(self.tick_sizes), band=band["name"])
        return cfg, basis

    def describe(self, basis: ParamBasis) -> str:
        """一行說明，供 UI 顯示這組參數是怎麼推出來的。"""
        src = (f"每筆量95百分位×{basis.tick_count}筆"
               if basis.tick_count >= 30 else "日均量比例法")
        return (f"股價 {basis.price:g}（{basis.band}）· 日均量 "
                f"{basis.adv_lots:,.0f} 張 · 大單門檻採{src}")
