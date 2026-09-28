"""向后兼容别名 —— 新安江模型实现已迁至 :mod:`app.core.model.models.xaj`。

保留本模块是为了不打断既有 import（``from .xaj import simulate_unit``）。
新代码请用注册表：``from .models import get_model`` / ``spec = get_model("hbv")``。
"""
from __future__ import annotations

from .models.xaj import (  # noqa: F401
    C_DEEP,
    DM,
    FIXED,
    IMP,
    KSS,
    LM,
    PARAMS,
    SPEC,
    TRUTH,
    UM,
    WM_TOTAL,
    UnitState,
    evapotranspiration,
    route_linear,
    runoff_generation,
    simulate_unit,
    split_water_sources,
)

__all__ = [
    "C_DEEP",
    "DM",
    "FIXED",
    "IMP",
    "KSS",
    "LM",
    "PARAMS",
    "SPEC",
    "TRUTH",
    "UM",
    "WM_TOTAL",
    "UnitState",
    "evapotranspiration",
    "route_linear",
    "runoff_generation",
    "simulate_unit",
    "split_water_sources",
]
