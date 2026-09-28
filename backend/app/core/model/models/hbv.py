"""HBV 模型（降雨版）—— 土壤湿度带 + 响应函数 + 单位线汇流。

出处：瑞典 SMHI，Bergström & Forsman (1973)、Bergström (1976/1992)；
本实现遵循 Seibert 的 HBV-light 版本（最广泛使用与验证的简化版），
去掉积雪/融雪模块（本项目流域为无长期积雪的湿润山区，与 XAJ 口径一致）。

结构 ::

    面雨量 P / 蒸发能力 E0
      ├─ 土壤湿度带（土壤含水 SM，容量 FC）：
      │     蒸发限制   E_act = CET·E0·min(1, SM/(FC·LP))
      │     产流/补给   recharge = P·(SM/FC)^BETA     ← BETA 越大越非线性
      │     更新       SM ← SM + P − recharge − E_act
      └─ 响应函数（两层蓄水）：
            SUZ ← SUZ + recharge
                 ├─ 快出流  Q0 = K0·max(0, SUZ − UZL)      ← 地表/近地表
                 ├─ 下　渗  perc = min(PERC, SUZ) → SLZ
                 ├─ 慢出流  Q1 = K1·SUZ
                 └─ SLZ 基流 Q2 = K2·SLZ
            Q = Q0 + Q1 + Q2
      └─ MAXBAS 三角单位线汇流 → 单元出流

水量平衡恒等式：``ΣP = ΣE + Σq + ΔS``（SM + SUZ + SLZ + 单位线在途蓄量）。
单位线用累积曲线差分实现并显式记账在途蓄量，因此恒等式严格闭合。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .base import CumulativeUH, ModelSpec, balance_of, clamp, triangular_s_curve


@dataclass
class UnitState:
    """HBV 状态量。单位线在途蓄量也计入总蓄量，保证水量平衡可闭合。"""

    SM: float = 100.0    # 土壤含水量 (mm)
    SUZ: float = 10.0    # 上层响应箱蓄量 (mm)
    SLZ: float = 50.0    # 下层响应箱蓄量 (mm)
    uh: CumulativeUH | None = field(default=None, repr=False)

    def storage(self) -> float:
        return self.SM + self.SUZ + self.SLZ + (self.uh.storage() if self.uh else 0.0)


def simulate_unit(
    p: np.ndarray,
    e0: np.ndarray,
    params: dict,
    state: UnitState | None = None,
    dt_days: float = 1.0,
) -> dict:
    """单单元逐时段演算（可率定参数见 PARAMS）。"""
    fc = max(1.0, float(params.get("FC", 250.0)))
    lp = clamp(float(params.get("LP", 0.9)), 0.01, 1.0)
    beta = clamp(float(params.get("BETA", 2.0)), 0.1, 12.0)
    cet = clamp(float(params.get("CET", 1.0)), 0.1, 3.0)
    k0 = clamp(float(params.get("K0", 0.3)), 0.0, 0.99)
    k1 = clamp(float(params.get("K1", 0.1)), 0.0, 0.99)
    k2 = clamp(float(params.get("K2", 0.02)), 0.0, 0.99)
    uzl = max(0.0, float(params.get("UZL", 20.0)))
    perc_max = max(0.0, float(params.get("PERC", 2.0)))
    maxbas = max(0.0, float(params.get("MAXBAS", 3.0)))

    st = state or UnitState()
    st.SM = clamp(st.SM, 0.0, fc)
    if st.uh is None:
        dt = max(1e-6, float(dt_days))
        st.uh = CumulativeUH(
            lambda x, _t=max(maxbas, dt): triangular_s_curve(x, _t),
            dt,
            max(maxbas, dt),
        )

    n = len(p)
    q = np.zeros(n)
    sum_p = sum_e = sum_r = sum_q = 0.0
    storage0 = st.storage()

    for t in range(n):
        pt = max(0.0, float(p[t]))
        e0t = max(0.0, float(e0[t]))

        # ---- 土壤湿度带：蒸发（用步初 SM）+ 产流补给
        e_act = cet * e0t * min(1.0, st.SM / (fc * lp))
        e_act = min(e_act, st.SM + pt)            # 不能超过可用水量
        if pt > 0.0:
            recharge = pt * min(1.0, st.SM / fc) ** beta
        else:
            recharge = 0.0
        st.SM = max(0.0, min(fc, st.SM + pt - recharge - e_act))

        # ---- 响应函数：两个蓄水箱
        st.SUZ += recharge
        q0 = k0 * max(0.0, st.SUZ - uzl)
        st.SUZ -= q0
        perc = min(perc_max, st.SUZ)
        st.SUZ -= perc
        st.SLZ += perc
        q1 = k1 * st.SUZ
        st.SUZ -= q1
        q2 = k2 * st.SLZ
        st.SLZ -= q2

        q_gen = q0 + q1 + q2
        q[t] = st.uh.step(q_gen)

        sum_p += pt
        sum_e += e_act
        sum_r += recharge
        sum_q += float(q[t])

    return {
        "q": q,
        "state": st,
        "balance": balance_of(sum_p, sum_e, sum_q, storage0, st.storage(), sum_r=sum_r),
    }


PARAMS = {
    "FC": {"label": "土壤含水容量", "default": 250.0, "min": 50.0, "max": 900.0, "unit": "mm", "calibrate": True},
    "LP": {"label": "蒸发限制阈值 SM/FC", "default": 0.9, "min": 0.3, "max": 1.0, "calibrate": True},
    "BETA": {"label": "土壤蓄水曲线指数", "default": 2.0, "min": 0.5, "max": 6.0, "calibrate": True},
    "CET": {"label": "蒸发折算系数", "default": 1.0, "min": 0.4, "max": 1.6, "calibrate": True},
    "K0": {"label": "上层快出流系数", "default": 0.3, "min": 0.02, "max": 0.8, "calibrate": True},
    "K1": {"label": "上层慢出流系数", "default": 0.1, "min": 0.005, "max": 0.5, "calibrate": True},
    "K2": {"label": "基流出流系数", "default": 0.02, "min": 0.001, "max": 0.15, "calibrate": True},
    "UZL": {"label": "上层出流阈值", "default": 20.0, "min": 0.0, "max": 100.0, "unit": "mm", "calibrate": True},
    "PERC": {"label": "下层下渗量", "default": 2.0, "min": 0.0, "max": 12.0, "unit": "mm/时段", "calibrate": True},
    "MAXBAS": {"label": "单位线汇流时间", "default": 3.0, "min": 0.5, "max": 8.0, "unit": "d", "calibrate": True},
}

FIXED = {
    "SNOW": {"label": "积雪/融雪模块", "value": "未启用（降雨版）"},
    "UH": {"label": "汇流单位线", "value": "三角分布（MAXBAS）"},
}

TRUTH = {
    "FC": 260.0,
    "LP": 0.85,
    "BETA": 1.8,
    "CET": 0.95,
    "K0": 0.26,
    "K1": 0.09,
    "K2": 0.022,
    "UZL": 18.0,
    "PERC": 2.2,
    "MAXBAS": 2.6,
    "XE": 0.22,
}

SPEC = ModelSpec(
    key="hbv",
    name="HBV（降雨版）",
    origin="瑞典 SMHI，Bergström 1976；本实现按 Seibert 的 HBV-light",
    structure="土壤湿度带（FC/LP/BETA）→ 两层响应函数（K0/K1/K2/UZL/PERC）→ MAXBAS 单位线汇流",
    notes=(
        "未启用积雪/融雪模块（降雨版），与本流域无长期积雪的条件一致。"
        "单位线为三角分布，汇流时间 MAXBAS 以「天」计，随模拟时段自动离散。"
    ),
    closure_expr="ΣP = ΣE + Σq + ΔS（土壤含水量 + 两层响应箱 + 单位线在途蓄量）",
    params=PARAMS,
    fixed=FIXED,
    truth=TRUTH,
    simulate=simulate_unit,
    refs=(
        "Bergström S. Development and application of a conceptual runoff model for Scandinavian catchments. SMHI Report RHO 7, 1976.",
        "Seibert J. HBV light version 2 user's manual. Uppsala University, 2005.",
        "Lindström G., Johansson B., Persson M., Gardelin M., Bergström S. Development and test of the distributed HBV-96 hydrological model. J. Hydrol. 201, 272-288, 1997.",
    ),
)
