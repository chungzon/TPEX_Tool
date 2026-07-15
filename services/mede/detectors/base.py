"""偵測器基礎：DetectorResult / Detector 抽象 / RollingZ（線上 z-score 基準）。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field, asdict


@dataclass
class DetectorResult:
    detector_name: str
    direction: int                       # +1 偏多 / -1 偏空 / 0 中性
    score: float                         # 0..100
    confidence: float                    # 0..1
    is_triggered: bool
    reasons: list = field(default_factory=list)
    raw_metrics: dict = field(default_factory=dict)
    parameter_version: str = ""

    @classmethod
    def idle(cls, name: str, pv: str = "", **raw) -> "DetectorResult":
        return cls(name, 0, 0.0, 0.0, False, [], dict(raw), pv)

    def as_dict(self) -> dict:
        return asdict(self)


def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return lo if x < lo else hi if x > hi else x


class RollingZ:
    """最近 n 筆的線上均值/標準差 → z-score。O(1) 更新，不掃全歷史。"""

    __slots__ = ("n", "dq", "_s", "_s2")

    def __init__(self, n: int):
        self.n = max(2, n)
        self.dq: deque = deque()
        self._s = 0.0
        self._s2 = 0.0

    def push(self, x: float) -> None:
        self.dq.append(x)
        self._s += x
        self._s2 += x * x
        if len(self.dq) > self.n:
            o = self.dq.popleft()
            self._s -= o
            self._s2 -= o * o

    def stats(self) -> tuple[float, float]:
        m = len(self.dq)
        if m < 2:
            return 0.0, 0.0
        mean = self._s / m
        var = max(self._s2 / m - mean * mean, 0.0)
        return mean, var ** 0.5

    def z(self, x: float) -> float:
        mean, std = self.stats()
        if std < 1e-9:
            return 0.0
        return (x - mean) / std

    def ready(self, min_samples: int) -> bool:
        return len(self.dq) >= min_samples


class Detector(ABC):
    """所有偵測器介面。stateful：每筆快照呼叫 update()。可啟用/停用；不直接下單。"""

    name: str = "base"
    requires_bidask: bool = False

    def __init__(self, cfg):
        self.cfg = cfg
        self.enabled = True
        self.pv = getattr(cfg, "parameter_version", "")

    @abstractmethod
    def update(self, snap) -> DetectorResult:
        """吃一個 FeatureSnapshot，回傳 DetectorResult。"""

    # 便利：取主要參考時間窗特徵
    def _win(self, snap) -> dict:
        tw = snap.time_windows
        label = getattr(self.cfg, "burst_window_label", "1s")
        return tw.get(label) or (next(iter(tw.values())) if tw else {})

    def _idle(self, **raw) -> DetectorResult:
        return DetectorResult.idle(self.name, self.pv, **raw)
