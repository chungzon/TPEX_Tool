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
from services.mede.detectors.breakout import BreakoutDetector
from services.mede.detectors.sweep import SweepDetector
from services.mede.detectors.absorption import AbsorptionDetector
from services.mede.detectors.queue_collapse import QueueCollapseDetector
from services.mede.detectors.liquidity_vacuum import LiquidityVacuumDetector
from services.mede.detectors.replenishment import ReplenishmentDetector
from services.mede.detectors.failed_breakout import FailedBreakoutDetector
from services.mede.detectors.exhaustion import ExhaustionDetector
from services.mede.detectors.momentum_ignition import MomentumIgnitionDetector
# BED 空方結構偵測器（Phase 4）
from services.mede.detectors.rally_failure import RallyFailureDetector
from services.mede.detectors.vwap_break import VwapBreakDetector
from services.mede.detectors.vwap_rejection import VwapRejectionDetector
from services.mede.detectors.lower_high import LowerHighDetector
from services.mede.detectors.structure_break import StructureBreakDetector
from services.mede.detectors.directional_efficiency import DirectionalEfficiencyDetector

# MEDE 14 個 + BED 空方 6 個 = 20 個偵測器
DETECTOR_CLASSES = [
    TradeBurstDetector,
    VolumeBurstDetector,
    AggressiveFlowDetector,
    BookImbalanceShiftDetector,
    OFIShockDetector,
    BreakoutDetector,
    SweepDetector,
    AbsorptionDetector,
    QueueCollapseDetector,
    LiquidityVacuumDetector,
    ReplenishmentDetector,
    FailedBreakoutDetector,
    ExhaustionDetector,
    MomentumIgnitionDetector,
    # --- BED 空方結構 ---
    RallyFailureDetector,
    VwapBreakDetector,
    VwapRejectionDetector,
    LowerHighDetector,
    StructureBreakDetector,
    DirectionalEfficiencyDetector,
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
