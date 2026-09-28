"""多模型注册基础设施 —— 概念性集总模型的统一契约。

每个模型（新安江 / HBV / Tank / GR4J）只实现一个函数：

    simulate_unit(p, e0, params, state=None) -> {
        "q":       np.ndarray,   # 单元出流，mm/step
        "state":   <状态对象>,    # 需提供 storage() -> 全部蓄量 (mm)
        "balance": {...}         # 需含 closure（水量平衡闭合误差，机器精度）
    }

其余一切（子流域链式装配、马斯京根河道演算、SCE-UA 率定、参数共享、联合率定、
GLUE、情景预报、CSV 导出）都与具体模型无关，换核不需要改动。

**水量平衡恒等式由各模型自行声明**（``ModelSpec.closure_expr``）：

- 新安江 / HBV / Tank：``ΣP = ΣE + Σq + ΔS``
- GR4J：``ΣP + ΣF = ΣE + Σq + ΔS``（F 为地下水交换净量，正值表示补给流域）

换核后第一件要验的就是这条恒等式——很多文献里线性水库写成递推滤波式，
会漏掉「虚拟蓄量」导致不闭合，必须用蓄量型 ``S ← k·S + in, out = (1-k)·S_prev``。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

# ---------------------------------------------------------------- 通用演算部件
MM_PER_STEP_TO_M3S = 1000.0  # Q[m³/s] = mm × km² × 1000 / Δt_s


def route_storage_reservoir(store: float, inflow: float, k: float) -> tuple[float, float]:
    """蓄量型线性水库一步演算（**必须**用这个形式，否则水量平衡不闭合）。

    返回 ``(新蓄量, 出流)``。入流 + 前蓄量 = 新蓄量 + 出流，逐步严格守恒。
    """
    kk = min(0.999, max(0.0, k))
    out = (1.0 - kk) * store
    return kk * store + inflow, out


class CumulativeUH:
    """保守型单位线：以累积曲线 S(x) 定义，在途蓄量显式记账。

    逐步释放比例 = ``S(x + dt) - S(x)``；``S`` 在 ``tmax`` 处饱和到 1。
    入流在**释放之后**入队，因此步末蓄量把新入流整份计入（等效「尚未释放」），
    从而 ``Σ入流 − Σ出流 = Δ蓄量`` 严格成立（见 ``scripts/_test_p10_models.py``）。

    S 曲线由各模型给出：HBV 用三角分布（MAXBAS），GR4J 用两条标准 UH 曲线。
    """

    __slots__ = ("_s", "_dt", "_tmax", "_pending")

    def __init__(self, s_curve: Callable[[float], float], dt: float, tmax: float):
        self._s = s_curve
        self._dt = float(dt)
        self._tmax = float(tmax)
        self._pending: list[list[float]] = []   # [[已历时(时段)，水量]]

    def step(self, inflow: float) -> float:
        """推进一个时段：先释放队列（用入队时的历时），再把本步入流入队。"""
        out = 0.0
        keep: list[list[float]] = []
        for it in self._pending:
            t0 = it[0]
            t1 = min(t0 + self._dt, self._tmax)
            out += it[1] * (self._s(t1) - self._s(t0))
            it[0] = t1
            if it[0] < self._tmax - 1e-12:
                keep.append(it)
        self._pending = keep
        if inflow:
            self._pending.append([0.0, float(inflow)])
        return out

    def storage(self) -> float:
        """步末在途蓄量：每份入流按「尚未释放的比例」加权。"""
        total = 0.0
        for t, v in self._pending:
            s = self._s(t)
            if s < 1.0:
                total += v * (1.0 - s)
        return total


def triangular_s_curve(x: float, tmax: float) -> float:
    """对称三角分布的累积曲线（HBV MAXBAS 用）：峰在 tmax/2。"""
    if x <= 0.0:
        return 0.0
    if x >= tmax:
        return 1.0
    r = x / tmax
    return 2.0 * r * r if r <= 0.5 else 1.0 - 2.0 * (1.0 - r) ** 2


# ---------------------------------------------------------------- 模型规范
@dataclass(frozen=True)
class ModelSpec:
    """一个概念性集总模型的全部元数据 + 演算入口。"""

    key: str
    name: str
    origin: str
    structure: str
    notes: str
    closure_expr: str
    params: dict                       # {key: {label, default, min, max, unit?, calibrate?}}
    fixed: dict                        # {key: {label, value, unit?}} 仅展示，不参与率定
    truth: dict                        # OSSE 演示真值参数（落在率定区间内）
    simulate: Callable
    refs: tuple = field(default_factory=tuple)

    @property
    def n_calib(self) -> int:
        """可率定参数个数（不含通用的河道演算参数 XE）。"""
        return sum(1 for v in self.params.values() if v.get("calibrate") is not False)

    def info(self) -> dict:
        """给前端的精简元数据。"""
        return {
            "key": self.key,
            "name": self.name,
            "origin": self.origin,
            "structure": self.structure,
            "notes": self.notes,
            "closure_expr": self.closure_expr,
            "n_calib": self.n_calib,
            "params": [{"key": k, **v} for k, v in self.params.items()],
            "fixed": [{"key": k, **v} for k, v in self.fixed.items()],
            "refs": list(self.refs),
        }


def balance_of(
    sum_p: float,
    sum_e: float,
    sum_q: float,
    storage0: float,
    storage1: float,
    *,
    sum_exchange: float = 0.0,
    sum_r: float | None = None,
) -> dict:
    """装配标准的 balance 结构。``closure = ΣP + ΣF − (ΣE + Σq + ΔS)``。"""
    closure = sum_p + sum_exchange - (sum_e + sum_q + (storage1 - storage0))
    out = {
        "sum_p": round(sum_p, 3),
        "sum_e": round(sum_e, 3),
        "sum_q": round(sum_q, 3),
        "storage_change": round(storage1 - storage0, 3),
        "closure": round(closure, 9),
        "closure_rate": round(closure / sum_p, 12) if sum_p > 0 else 0.0,
    }
    if sum_r is not None:
        out["sum_r"] = round(sum_r, 3)
    if abs(sum_exchange) > 1e-12:
        out["sum_exchange"] = round(sum_exchange, 3)
    return out


def clamp(v: float, lo: float, hi: float) -> float:
    return min(hi, max(lo, float(v)))


__all__ = [
    "MM_PER_STEP_TO_M3S",
    "CumulativeUH",
    "ModelSpec",
    "balance_of",
    "clamp",
    "route_storage_reservoir",
    "triangular_s_curve",
    "np",
]
