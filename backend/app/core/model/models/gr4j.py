"""GR4J 模型（4 参数）—— Génie Rural à 4 paramètres Journalier。

出处：法国 INRAE（原 Cemagref），Perrin, Michel & Andréassian (2003)。
是全球水文界最广泛使用的**极简**概念性集总模型：4 个自由参数即可稳健复现
日尺度径流过程，长期作为概念模型与数据驱动模型的对比基准。

结构 ::

    面雨量 P / 蒸发能力 E0
      ├─ 产流水库 S（容量 X1）：
      │     净雨 Pn = max(0, P−E)     净蒸发 En = max(0, E−P)
      │     入渗  Ps   = X1(1−(S/X1)²)·tanh(Pn/X1) / (1 + (S/X1)·tanh(Pn/X1))
      │     产流  Perc = S·(1 − (1 + (4S/9X1)⁴)^(−1/4))
      │     蒸发  Es   = S(2−S/X1)·tanh(En/X1) / (1 + (1−S/X1)·tanh(En/X1))
      │     净雨  Pr   = Perc + (Pn − Ps)
      ├─ 两条单位线：90% → UH1（时基 X4）、10% → UH2（时基 2·X4）
      └─ 汇流水库 R（容量 X3）：
            地下水交换 F = X2·(R/X3)^3.5
            出流  Qr = R·(1 − (1 + (R/X3)⁴)^(−1/4))    → 单元出流

水量平衡恒等式：``ΣP + ΣF = ΣE + Σq + ΔS``。
GR4J 的 X2 表示与深层地下水的交换：X2 > 0 补给流域、X2 < 0 带走水量，
因此恒等式必须把交换项 F 计入，否则不闭合（这是 GR4J 与前三者的唯一区别）。
双单位线用累积曲线差分实现并显式记账在途蓄量，``Δt`` 变化时质量仍然严格守恒。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .base import CumulativeUH, ModelSpec, balance_of, clamp


def _sh1(x: float, x4: float) -> float:
    """UH1 累积曲线（时基 X4）：S = 1 − (1 − x/X4)^(5/2)。"""
    if x <= 0.0:
        return 0.0
    if x >= x4:
        return 1.0
    return 1.0 - (1.0 - x / x4) ** 2.5


def _sh2(x: float, x4: float) -> float:
    """UH2 累积曲线（时基 2·X4）：前段 ½(x/X4)^(5/2)，后段 1 − ½(2 − x/X4)^(5/2)。"""
    if x <= 0.0:
        return 0.0
    if x >= 2.0 * x4:
        return 1.0
    if x <= x4:
        return 0.5 * (x / x4) ** 2.5
    return 1.0 - 0.5 * (2.0 - x / x4) ** 2.5


@dataclass
class UnitState:
    S: float = 100.0     # 产流水库蓄量 (mm)
    R: float = 30.0      # 汇流水库蓄量 (mm)
    uh1: CumulativeUH | None = field(default=None, repr=False)
    uh2: CumulativeUH | None = field(default=None, repr=False)

    def storage(self) -> float:
        uh = (self.uh1.storage() if self.uh1 else 0.0) + (self.uh2.storage() if self.uh2 else 0.0)
        return self.S + self.R + uh


def simulate_unit(
    p: np.ndarray,
    e0: np.ndarray,
    params: dict,
    state: UnitState | None = None,
    dt_days: float = 1.0,
) -> dict:
    """GR4J 逐时段演算（可率定参数 X1 / X2 / X3 / X4）。"""
    x1 = max(10.0, float(params.get("X1", 350.0)))
    x2 = float(params.get("X2", 0.0))
    x3 = max(1.0, float(params.get("X3", 90.0)))
    x4 = max(0.1, float(params.get("X4", 1.7)))

    st = state or UnitState()
    st.S = clamp(st.S, 0.0, x1)
    dt = max(1e-6, float(dt_days))
    # 注意：_sh1/_sh2 的第二个参数是 **X4 本身**（曲线内部各自乘 1 倍 / 2 倍得到时基），
    # 而 CumulativeUH 的 tmax 必须不小于曲线的饱和点，否则未释放完的在途水会被丢弃、
    # 水量平衡不闭合（这里踩过：把 2·X4 当 X4 传入导致 UH2 曲线在 tmax 处只有 0.5）。
    if st.uh1 is None:
        st.uh1 = CumulativeUH(lambda x: _sh1(x, x4), dt, max(x4, dt))
    if st.uh2 is None:
        st.uh2 = CumulativeUH(lambda x: _sh2(x, x4), dt, max(2.0 * x4, dt))

    n = len(p)
    q = np.zeros(n)
    sum_p = sum_e = sum_r = sum_q = sum_exch = 0.0
    storage0 = st.storage()

    for t in range(n):
        pt = max(0.0, float(p[t]))
        e0t = max(0.0, float(e0[t]))

        # ---------- 1) 产流水库
        pn = max(0.0, pt - e0t)
        en = max(0.0, e0t - pt)
        if pn > 0.0:
            r_s = st.S / x1
            ps = x1 * (1.0 - r_s * r_s) * math.tanh(pn / x1) / (1.0 + r_s * math.tanh(pn / x1))
            st.S += ps
        else:
            ps = 0.0
        perc = st.S * (1.0 - (1.0 + (4.0 * st.S / (9.0 * x1)) ** 4) ** -0.25)
        st.S -= perc
        if en > 0.0:
            r_s = st.S / x1
            es = st.S * (2.0 - r_s) * math.tanh(en / x1) / (1.0 + (1.0 - r_s) * math.tanh(en / x1))
            st.S -= es
            e_act = es + pt
        else:
            es = 0.0
            e_act = e0t
        e_act = max(0.0, e_act)
        pr = perc + (pn - ps)

        # ---------- 2) 双单位线（90% / 10%）
        routed = st.uh1.step(0.9 * pr) + st.uh2.step(0.1 * pr)

        # ---------- 3) 汇流水库 + 地下水交换
        ratio = min(st.R / x3, 10.0)
        f = clamp(x2 * ratio ** 3.5, -100.0, 100.0)
        a = routed + f
        r_new = st.R + a
        extra = 0.0
        if r_new < 0.0:            # 交换能力超过可交换水量时截断（多余部分记为额外交换）
            extra = -r_new
            r_new = 0.0
        st.R = r_new
        qr = st.R * (1.0 - (1.0 + (st.R / x3) ** 4) ** -0.25)
        st.R -= qr

        q[t] = qr
        sum_p += pt
        sum_e += e_act
        sum_r += pr
        sum_q += float(qr)
        sum_exch += f + extra

    return {
        "q": q,
        "state": st,
        "balance": balance_of(
            sum_p, sum_e, sum_q, storage0, st.storage(),
            sum_exchange=sum_exch, sum_r=sum_r,
        ),
    }


PARAMS = {
    "X1": {"label": "产流水库容量", "default": 350.0, "min": 100.0, "max": 1200.0, "unit": "mm", "calibrate": True},
    "X2": {"label": "地下水交换系数", "default": 0.0, "min": -5.0, "max": 3.0, "unit": "mm/时段", "calibrate": True},
    "X3": {"label": "汇流水库容量", "default": 90.0, "min": 20.0, "max": 300.0, "unit": "mm", "calibrate": True},
    "X4": {"label": "单位线汇流时间", "default": 1.7, "min": 1.1, "max": 2.9, "unit": "d", "calibrate": True},
}

FIXED = {
    "UH_SPLIT": {"label": "单位线分流比", "value": "UH1 90% / UH2 10%"},
    "UH_FORM": {"label": "单位线形态", "value": "S(t) = 1−(1−t/X4)^(5/2)"},
    "PARAMS": {"label": "参数个数", "value": "4（水文界最简概念模型之一）"},
}

TRUTH = {
    "X1": 380.0,
    "X2": 1.2,
    "X3": 85.0,
    "X4": 1.8,
    "XE": 0.22,
}

SPEC = ModelSpec(
    key="gr4j",
    name="GR4J",
    origin="法国 INRAE，Perrin / Michel / Andréassian，2003",
    structure="产流水库 + 两条单位线（90%/10%）+ 汇流水库 + 地下水交换，仅 4 个自由参数",
    notes=(
        "4 参数极简模型，全球概念模型基准。X2 表示与深层地下水的交换："
        "正值补给流域、负值带走水量，因此水量平衡恒等式含交换项 ΣF。"
        "单位线以累积曲线差分实现，模拟时段（天/小时）变化时质量仍严格守恒。"
    ),
    closure_expr="ΣP + ΣF = ΣE + Σq + ΔS（产流水库 + 汇流水库 + 双单位线在途蓄量；F 为地下水交换净量）",
    params=PARAMS,
    fixed=FIXED,
    truth=TRUTH,
    simulate=simulate_unit,
    refs=(
        "Perrin C., Michel C., Andréassian V. Improvement of a parsimonious model for streamflow simulation. J. Hydrol. 279, 275-289, 2003.",
        "Edijatno, Nascimento N.O., Yang X., Makhlouf Z., Michel C. GR3J: a daily watershed model with three free parameters. Hydrol. Sci. J. 44(2), 263-277, 1999.",
    ),
)
