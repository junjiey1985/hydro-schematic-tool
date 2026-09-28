"""参数体系：**模型可插拔**的参数规范 / 默认值 / 率定区间 / 参数集清洗。

每单元一套产汇流参数（个数随模型而定） + 河道演算参数 XE。
参数集与模型选择一同持久化于 ``data/projects/<pid>/calibration/default.json``::

    {"model": "hbv", "saved_at": "...", "params": {"SB01": {...}}}

``XE``（马斯京根流量比重因子）与 ``KE``（汇流时间，按出口间距自动估算）属于
**河道演算层**，与具体产汇流模型无关，因此四种模型共用 —— 只要单元有上游来流，
XE 就是一个可率定参数。

模型参数的取值区间、默认值、真值全部来自 :mod:`app.core.model.models` 的注册表，
本模块只负责组装与钳制。
"""
from __future__ import annotations

import math

from .models import DEFAULT_MODEL, get_model

# 河道演算参数（所有模型共用）：只要单元有直接上游来流，XE 就参与率定
ROUTING_SPEC = {
    "XE": {"label": "马斯京根 XE", "default": 0.2, "min": 0.0, "max": 0.4, "calibrate": "channel"},
}

DEFAULT_KE_H = 12.0  # KE 缺省（h）；simulate 会按出口间距自动覆盖


# ---------------------------------------------------------------- 模型相关查询
def param_spec(model: str | None = None) -> dict:
    """模型的参数规范（可率定 + 展示用），末尾附通用的河道演算参数 XE。"""
    spec = dict(get_model(model).params)
    spec.update(ROUTING_SPEC)
    return spec


def param_keys(model: str | None = None) -> list[str]:
    return list(param_spec(model).keys())


def model_param_keys(model: str | None = None) -> list[str]:
    """仅产汇流参数（不含 XE）。"""
    return list(get_model(model).params.keys())


def fixed_spec(model: str | None = None) -> dict:
    return dict(get_model(model).fixed)


def truth_params(model: str | None = None) -> dict:
    """演示数据（OSSE）的真值参数，已保证落在率定区间内。"""
    m = get_model(model)
    return {k: float(v) for k, v in m.truth.items()}


def model_name(model: str | None = None) -> str:
    return get_model(model).name


def closure_expr(model: str | None = None) -> str:
    return get_model(model).closure_expr


# ---------------------------------------------------------------- 参数集装配
def default_params(reach_km: float = 0.0, dt_h: float = 24.0, model: str | None = None) -> dict:
    """默认参数集。reach_km>0 时按河长估算 KE（汇流时间 = L·弯曲系数/波速）。"""
    spec = param_spec(model)
    out = {k: float(v["default"]) for k, v in spec.items()}
    if reach_km > 0:
        from . import routing

        t_h, _n = routing.segment_count(reach_km, dt_h)
        # 分段连续演算的总 KE 即汇流时间；受稳定性约束的单段钳制在系数内处理
        out["KE"] = round(min(max(t_h, dt_h / 2.0), 72.0), 2)
    else:
        out["KE"] = DEFAULT_KE_H
    return out


def sanitize_params(raw: dict | None, model: str | None = None) -> dict:
    """清洗用户传入的参数：缺省补齐、类型与区间钳制（按当前模型的区间）。"""
    src = raw or {}
    out = {}
    for key, spec in param_spec(model).items():
        try:
            v = float(src.get(key, spec["default"]))
        except (TypeError, ValueError):
            v = float(spec["default"])
        if not math.isfinite(v):
            v = float(spec["default"])
        out[key] = min(float(spec["max"]), max(float(spec["min"]), v))
    out["KE"] = float(src.get("KE") or DEFAULT_KE_H)
    if not math.isfinite(out["KE"]) or out["KE"] <= 0:
        out["KE"] = DEFAULT_KE_H
    return out


# ---------------------------------------------------------------- 向后兼容常量
# 以下常量等价于「默认模型（新安江）」的规范，供演示数据生成等老调用点使用。
# 新代码请直接用上面的函数并显式传入模型 key。
PARAM_SPEC = param_spec(DEFAULT_MODEL)
PARAM_KEYS = list(PARAM_SPEC.keys())
FIXED_SPEC = fixed_spec(DEFAULT_MODEL)
DEMO_TRUTH_PARAMS = truth_params(DEFAULT_MODEL)
