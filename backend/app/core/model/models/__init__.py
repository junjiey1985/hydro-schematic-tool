"""概念性集总模型注册表。

新增一个模型只需：在本包内写一个模块，暴露一个 :class:`~.base.ModelSpec`
（含 params / fixed / truth / simulate），然后加进下面的 ``_SPECS``。
参数体系、率定、GLUE、预报、导出会**自动**支持它。
"""
from __future__ import annotations

from .base import CumulativeUH, ModelSpec, balance_of
from .gr4j import SPEC as GR4J_SPEC
from .hbv import SPEC as HBV_SPEC
from .tank import SPEC as TANK_SPEC
from .xaj import SPEC as XAJ_SPEC

# 注册顺序即前端展示顺序：先本项目原生的新安江，再按参数从少到多
_SPECS: tuple[ModelSpec, ...] = (XAJ_SPEC, GR4J_SPEC, TANK_SPEC, HBV_SPEC)

MODELS: dict[str, ModelSpec] = {s.key: s for s in _SPECS}
DEFAULT_MODEL = XAJ_SPEC.key


def get_model(key: str | None = None) -> ModelSpec:
    """按 key 取模型规范；未知或空值回退到默认模型（新安江）。"""
    if key:
        spec = MODELS.get(str(key).strip().lower())
        if spec is not None:
            return spec
    return MODELS[DEFAULT_MODEL]


def is_valid(key: str | None) -> bool:
    return bool(key) and str(key).strip().lower() in MODELS


def list_models() -> list[ModelSpec]:
    return list(_SPECS)


def list_model_info() -> list[dict]:
    """给前端的模型列表（含参数与固定参数明细）。"""
    out = []
    for s in _SPECS:
        info = s.info()
        info["is_default"] = s.key == DEFAULT_MODEL
        out.append(info)
    return out


__all__ = [
    "CumulativeUH",
    "DEFAULT_MODEL",
    "MODELS",
    "ModelSpec",
    "balance_of",
    "get_model",
    "is_valid",
    "list_model_info",
    "list_models",
]
