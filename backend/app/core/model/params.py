"""参数体系：默认值 / 率定区间 / 参数集读写。

每单元一套产汇流参数（可率定 8 个）+ 河道演算参数（KE 自动估算、XE 可调）。
参数集持久化于 data/projects/<pid>/calibration/default.json（P3 率定会写入）。
"""
from __future__ import annotations

import math

# 可率定参数（每单元）——label 供前端表头，min/max 供 P3 率定区间与前端校验
PARAM_SPEC = {
    "K": {"label": "蒸散发折算系数", "default": 0.85, "min": 0.5, "max": 1.2, "calibrate": True},
    "B": {"label": "蓄水容量曲线指数", "default": 0.3, "min": 0.1, "max": 0.7, "calibrate": True},
    "SM": {"label": "自由水蓄水容量", "default": 30.0, "min": 5.0, "max": 120.0, "unit": "mm", "calibrate": True},
    "EX": {"label": "自由水容量分布指数", "default": 1.5, "min": 1.0, "max": 1.8, "calibrate": True},
    "KGF": {"label": "地下水出流比 KG/(KG+KI)", "default": 0.5, "min": 0.25, "max": 0.75, "calibrate": True},
    "CG": {"label": "地下水库消退系数", "default": 0.985, "min": 0.93, "max": 0.998, "calibrate": True},
    "CI": {"label": "壤中流消退系数", "default": 0.7, "min": 0.4, "max": 0.9, "calibrate": True},
    "CS": {"label": "河网汇流消退系数", "default": 0.3, "min": 0.05, "max": 0.6, "calibrate": True},
    "XE": {"label": "马斯京根 XE", "default": 0.2, "min": 0.0, "max": 0.4, "calibrate": "channel"},
}

# 固定参数（不率定，仅展示）
FIXED_SPEC = {
    "WM": {"label": "张力水蓄水容量", "value": 140.0, "unit": "mm"},
    "C": {"label": "深层蒸散发系数", "value": 0.15},
    "IMP": {"label": "不透水面积比", "value": 0.01},
    "KSS": {"label": "自由水总出流系数 KG+KI", "value": 0.7},
}

DEFAULT_KE_H = 12.0  # KE 缺省（h）；simulate 会按出口间距自动覆盖

PARAM_KEYS = list(PARAM_SPEC.keys())

# 演示数据的「真值参数」：用同一套模型结构 + 该组参数生成合成"实测"流量，
# 供率定回收验证（观测系统模拟实验，OSSE）。所有取值均落在 PARAM_SPEC 的率定区间内。
DEMO_TRUTH_PARAMS = {
    "K": 0.92,     # 蒸散发折算系数 → 年均蒸散发 ≈ 0.92×670 ≈ 615 mm，对应径流系数 ≈ 0.35
    "B": 0.32,
    "SM": 38.0,
    "EX": 1.40,
    "KGF": 0.42,
    "CG": 0.986,
    "CI": 0.72,
    "CS": 0.28,
    "XE": 0.22,
}


def default_params(reach_km: float = 0.0, dt_h: float = 24.0) -> dict:
    """默认参数集。reach_km>0 时按河长估算 KE（汇流时间 = L·弯曲系数/波速）。"""
    out = {k: float(v["default"]) for k, v in PARAM_SPEC.items()}
    if reach_km > 0:
        from . import routing

        t_h, _n = routing.segment_count(reach_km, dt_h)
        # 分段连续演算的总 KE 即汇流时间；受稳定性约束的单段钳制在系数内处理
        out["KE"] = round(min(max(t_h, dt_h / 2.0), 72.0), 2)
    else:
        out["KE"] = DEFAULT_KE_H
    return out


def sanitize_params(raw: dict | None) -> dict:
    """清洗用户传入的参数：缺省补齐、类型与区间钳制。"""
    src = raw or {}
    out = {}
    for key, spec in PARAM_SPEC.items():
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
