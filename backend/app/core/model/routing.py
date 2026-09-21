"""马斯京根分段连续演算（河道汇流）。

C0 + C1 + C2 = 1，严格守恒；分段数 n 按「河段汇流时间 / 计算时段」自动确定，
每段 K = KE/n，并做稳定性钳制（K ≥ Δt/2，避免出现负系数）。
"""
from __future__ import annotations

import math

import numpy as np

# 河道弯曲系数与波速（估算河段汇流时间用；后续可按单元率定）
SINUOSITY = 1.3
WAVE_SPEED_MS = 1.5  # m/s，山区河道典型值


def muskingum_coeffs(k_h: float, xe: float, dt_h: float) -> tuple[float, float, float]:
    """单段马斯京根系数 (C0, C1, C2)，满足 C0+C1+C2=1。"""
    k_h = max(float(k_h), dt_h / 2.0)  # 稳定性：K ≥ Δt/2
    xe = min(0.45, max(-0.2, float(xe)))
    denom = k_h * (1.0 - xe) + 0.5 * dt_h
    c0 = (0.5 * dt_h - k_h * xe) / denom
    c1 = (0.5 * dt_h + k_h * xe) / denom
    c2 = (k_h * (1.0 - xe) - 0.5 * dt_h) / denom
    # 数值兜底（极端参数下可能出现微小负值，钳到 0 并重归一）
    c0, c1, c2 = max(0.0, c0), max(0.0, c1), max(0.0, c2)
    s = c0 + c1 + c2
    if s <= 0:
        return 0.0, 1.0, 0.0
    return c0 / s, c1 / s, c2 / s


def segment_count(reach_km: float, dt_h: float, speed_ms: float = WAVE_SPEED_MS) -> tuple[float, int]:
    """按河长估算总汇流时间 T(h) 与分段数 n（每段 K ≥ Δt/2 → n ≤ 2T/Δt）。"""
    t_h = reach_km * 1000.0 * SINUOSITY / max(speed_ms, 0.3) / 3600.0
    t_h = max(t_h, 0.0)
    n = max(1, int(math.ceil(2.0 * t_h / max(dt_h, 0.1))))
    n = min(n, 40)
    return t_h, n


def muskingum_route(inflow: np.ndarray, ke_h: float, xe: float, dt_h: float) -> np.ndarray:
    """分段连续演算。inflow 与返回值单位一致（m³/s 或 mm/step）。

    分段数：每段 K ≥ Δt/2（稳定性约束）→ n = ceil(KE/(Δt/2))，上限 40 段；
    初值取入流首值（warm start，避免人为零起点）；每段单独递推后串接。
    """
    q_in = np.asarray(inflow, dtype=float)
    n_seg = max(1, min(40, int(math.ceil(ke_h / max(dt_h / 2.0, 1e-6))))) if ke_h > 0 else 1
    k_seg = ke_h / n_seg
    c0, c1, c2 = muskingum_coeffs(k_seg, xe, dt_h)
    out = q_in
    for _ in range(n_seg):
        buf = np.empty_like(out)
        buf[0] = out[0]
        for t in range(1, len(out)):
            buf[t] = c0 * out[t] + c1 * out[t - 1] + c2 * buf[t - 1]
        out = buf
    return out
