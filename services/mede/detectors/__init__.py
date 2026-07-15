"""MEDE 偵測器 — 每個 Detector 獨立、吃 FeatureSnapshot、輸出 DetectorResult。

第一版：可解釋規則型偵測器，不直接下單、不做 Fusion（Fusion 於 Phase 5）。
"""

from __future__ import annotations

from services.mede.detectors.base import Detector, DetectorResult, RollingZ
from services.mede.detectors.trade_burst import TradeBurstDetector
from services.mede.detectors.volume_burst import VolumeBurstDetector
from services.mede.detectors.aggressive_flow import AggressiveFlowDetector
from services.mede.detectors.book_imbalance import BookImbalanceShiftDetector
from services.mede.detectors.ofi_shock import OFIShockDetector

# 已實作偵測器（依 Phase 4 進度擴充；其餘 detector 於後續 turn 加入）
DETECTOR_CLASSES = [
    TradeBurstDetector,
    VolumeBurstDetector,
    AggressiveFlowDetector,
    BookImbalanceShiftDetector,
    OFIShockDetector,
]


def build_detectors(cfg) -> list[Detector]:
    """依設定建立啟用的偵測器（detector_enabled 未指定者預設啟用）。"""
    enabled = cfg.detector_enabled or {}
    out = []
    for klass in DETECTOR_CLASSES:
        if enabled.get(klass.name, True):
            out.append(klass(cfg))
    return out


__all__ = ["Detector", "DetectorResult", "RollingZ", "build_detectors",
           "DETECTOR_CLASSES"]
