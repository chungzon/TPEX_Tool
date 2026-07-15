"""MomentumIgnitionDetector — 成交與價格動能同步進入加速（多類證據共振）。

不得單一條件成立：需 ≥ momentum_min_categories 類不同證據同向共振，且無否決。
向上：Trade/Volume Burst、主動買流、正 OFI、賣一崩塌、價速與加速度轉正、
      點差未異常、無賣方吸收(此由「價確實在動」保證)。向下反向。
"""

from __future__ import annotations

from services.mede.detectors.base import Detector, DetectorResult, RollingZ, clamp


class MomentumIgnitionDetector(Detector):
    name = "momentum_ignition"

    def __init__(self, cfg):
        super().__init__(cfg)
        self._zr = RollingZ(cfg.baseline_window)
        self._zv = RollingZ(cfg.baseline_window)

    def update(self, snap) -> DetectorResult:
        w = self._win(snap)
        rate = w.get("buy_cnt", 0) + w.get("sell_cnt", 0)
        vol = w.get("buy_vol", 0.0) + w.get("sell_vol", 0.0)
        imb = w.get("trade_imbalance", 0.0)
        zr = self._zr.z(rate)
        self._zr.push(rate)
        zv = self._zv.z(vol)
        self._zv.push(vol)
        if not self._zr.ready(self.cfg.minimum_warmup_ticks):
            return self._idle()

        vel, acc = snap.price_velocity, snap.price_accel
        thr = self.cfg.flow_imbalance_threshold
        if imb >= thr and vel > 0:
            d = 1
        elif imb <= -thr and vel < 0:
            d = -1
        else:
            return self._idle(imbalance=round(imb, 3))

        cats = ["aggressive_flow"]
        if zr >= self.cfg.trade_burst_zscore:
            cats.append("trade_burst")
        if zv >= self.cfg.volume_burst_zscore:
            cats.append("volume_burst")
        if snap.bidask_available and ((snap.ofi_l1 > 0) if d > 0 else (snap.ofi_l1 < 0)):
            cats.append("ofi")
        qc = ((snap.ask_moved == 1 or snap.ask_collapse_ratio >= self.cfg.queue_collapse_ratio)
              if d > 0 else
              (snap.bid_moved == -1 or snap.bid_collapse_ratio >= self.cfg.queue_collapse_ratio))
        if snap.bidask_available and qc:
            cats.append("queue_collapse")
        if (acc > 0) if d > 0 else (acc < 0):
            cats.append("accel")

        veto = []
        if snap.bidask_available and snap.spread_ticks >= 5:
            veto.append("spread異常寬")

        triggered = len(cats) >= self.cfg.momentum_min_categories and not veto
        direction = d if triggered else 0
        score = clamp(len(cats) * 16 + min(abs(imb), 1.0) * 12)
        conf = clamp(len(cats) / 6.0 * 0.7 + min(abs(imb), 1.0) * 0.3, 0.0, 1.0)
        reasons = ([f"{'上漲' if d > 0 else '下跌'}動能點火：{len(cats)} 類共振"
                    f"（{'/'.join(cats)}）"] if triggered else [])
        return DetectorResult(self.name, direction, score, conf, triggered, reasons,
                              {"categories": cats, "zr": round(zr, 1),
                               "zv": round(zv, 1), "veto": veto}, self.pv)
