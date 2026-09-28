"""Tank 水箱模型（三层串联）—— 菅原正巳 (Sugawara) 系列。

出处：日本 菅原正巳 (1961, 1972)；国家防灾科学技术中心（原防灾中心）业务版本。
模型把流域抽象为若干**串联水箱**：每层底部有下渗孔（向下一层供水）、
侧面有出流孔（超出孔高的水量按系数流出），降水量注入顶层，蒸散发从上部两层扣除。

结构 ::

    面雨量 P → ┌────────── 第 1 层 ──────────┐
               │  S1 += P                     │
               │  蒸发  E1 = min(S1, E0)      │  → 出流 o1 = a1·max(0, S1−h1)
               │  下渗  i1 = b1·max(0, S1−h1i)│
               └──────────────┬───────────────┘
                              ↓ i1
               ┌────────── 第 2 层 ──────────┐
               │  S2 += i1                    │
               │  蒸发  E2 = min(S2, evp2·ΔE) │  → 出流 o2 = a2·max(0, S2−h2)
               │  下渗  i2 = b2·max(0, S2−h2i)│
               └──────────────┬───────────────┘
                              ↓ i2
               ┌────────── 第 3 层（基流）────┐
               │  S3 += i2                    │  → 出流 o3 = a3·max(0, S3−h3)
               └──────────────────────────────┘

    单元出流 Q = o1 + o2 + o3（三层并联，无单位线——层内蓄量本身即汇流过程）

水量平衡恒等式：``ΣP = ΣE + Σq + ΔS``（三层蓄量之和），逐步严格闭合。
第 3 层不设下渗（否则下渗量会成为不闭合的"损失项"，需另立恒等式）。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .base import ModelSpec, balance_of, clamp


@dataclass
class UnitState:
    S1: float = 20.0   # 第 1 层蓄量 (mm)
    S2: float = 15.0   # 第 2 层蓄量 (mm)
    S3: float = 45.0   # 第 3 层蓄量 (mm)

    def storage(self) -> float:
        return self.S1 + self.S2 + self.S3


def simulate_unit(
    p: np.ndarray,
    e0: np.ndarray,
    params: dict,
    state: UnitState | None = None,
    dt_days: float = 1.0,
    t: "np.ndarray | None" = None,  # 气温序列（可选；本模型不用，仅为统一契约保留）
) -> dict:
    """三层水箱逐时段演算（可率定参数见 PARAMS）。dt_days 不影响本模型（系数按时段定义）。"""
    h1 = max(0.0, float(params.get("H1", 12.0)))
    a1 = clamp(float(params.get("A1", 0.15)), 0.0, 0.99)
    h1i = max(0.0, float(params.get("H1I", 6.0)))
    b1 = clamp(float(params.get("B1", 0.06)), 0.0, 0.99)
    h2 = max(0.0, float(params.get("H2", 10.0)))
    a2 = clamp(float(params.get("A2", 0.08)), 0.0, 0.99)
    h2i = max(0.0, float(params.get("H2I", 4.0)))
    b2 = clamp(float(params.get("B2", 0.04)), 0.0, 0.99)
    h3 = max(0.0, float(params.get("H3", 35.0)))
    a3 = clamp(float(params.get("A3", 0.012)), 0.0, 0.99)
    evp2 = clamp(float(params.get("EVP2", 0.35)), 0.0, 1.0)

    st = state or UnitState()
    n = len(p)
    q = np.zeros(n)
    sum_p = sum_e = sum_q = 0.0
    storage0 = st.storage()

    for t in range(n):
        pt = max(0.0, float(p[t]))
        e0t = max(0.0, float(e0[t]))

        # ---- 第 1 层：降水 → 蒸发 → 出流 → 下渗
        st.S1 += pt
        e1 = min(st.S1, e0t)
        st.S1 -= e1
        e2 = min(st.S2, evp2 * max(0.0, e0t - e1))
        st.S2 -= e2
        o1 = a1 * max(0.0, st.S1 - h1)
        st.S1 -= o1
        i1 = b1 * max(0.0, st.S1 - h1i)
        st.S1 -= i1

        # ---- 第 2 层
        st.S2 += i1
        o2 = a2 * max(0.0, st.S2 - h2)
        st.S2 -= o2
        i2 = b2 * max(0.0, st.S2 - h2i)
        st.S2 -= i2

        # ---- 第 3 层（基流，无下渗）
        st.S3 += i2
        o3 = a3 * max(0.0, st.S3 - h3)
        st.S3 -= o3

        q[t] = o1 + o2 + o3
        sum_p += pt
        sum_e += e1 + e2
        sum_q += float(q[t])

    return {
        "q": q,
        "state": st,
        "balance": balance_of(sum_p, sum_e, sum_q, storage0, st.storage()),
    }


PARAMS = {
    "H1": {"label": "第1层出流孔高", "default": 12.0, "min": 0.0, "max": 60.0, "unit": "mm", "calibrate": True},
    "A1": {"label": "第1层出流系数", "default": 0.15, "min": 0.01, "max": 0.6, "calibrate": True},
    "H1I": {"label": "第1层下渗孔高", "default": 6.0, "min": 0.0, "max": 60.0, "unit": "mm", "calibrate": True},
    "B1": {"label": "第1层下渗系数", "default": 0.06, "min": 0.005, "max": 0.5, "calibrate": True},
    "H2": {"label": "第2层出流孔高", "default": 10.0, "min": 0.0, "max": 60.0, "unit": "mm", "calibrate": True},
    "A2": {"label": "第2层出流系数", "default": 0.08, "min": 0.005, "max": 0.5, "calibrate": True},
    "H2I": {"label": "第2层下渗孔高", "default": 4.0, "min": 0.0, "max": 60.0, "unit": "mm", "calibrate": True},
    "B2": {"label": "第2层下渗系数", "default": 0.04, "min": 0.002, "max": 0.4, "calibrate": True},
    "H3": {"label": "第3层出流孔高", "default": 35.0, "min": 0.0, "max": 120.0, "unit": "mm", "calibrate": True},
    "A3": {"label": "第3层基流系数", "default": 0.012, "min": 0.001, "max": 0.15, "calibrate": True},
    "EVP2": {"label": "第2层蒸发折减", "default": 0.35, "min": 0.0, "max": 1.0, "calibrate": True},
}

FIXED = {
    "LAYERS": {"label": "串联层数", "value": 3},
    "S3_PERC": {"label": "第3层下渗", "value": "无（避免不闭合的损失项）"},
    "UH": {"label": "汇流方式", "value": "层内蓄量过程（无独立单位线）"},
}

TRUTH = {
    "H1": 9.0,
    "A1": 0.19,
    "H1I": 4.0,
    "B1": 0.05,
    "H2": 12.0,
    "A2": 0.10,
    "H2I": 3.0,
    "B2": 0.035,
    "H3": 40.0,
    "A3": 0.014,
    "EVP2": 0.45,
    "XE": 0.22,
}

SPEC = ModelSpec(
    key="tank",
    name="Tank 水箱模型",
    origin="日本 菅原正巳，1961 / 1972；防灾科学技术中心业务版本",
    structure="三层串联水箱：每层「出流孔高 + 出流系数」，层间以「下渗孔高 + 下渗系数」供水；第 3 层出基流",
    notes=(
        "三层串联、无独立汇流单位线——层内蓄量本身即汇流过程。"
        "第 3 层不设下渗，否则下渗量会成为不闭合的损失项。"
        "蒸散发先取第 1 层，不足部分按 EVP2 折减后取第 2 层。"
    ),
    closure_expr="ΣP = ΣE + Σq + ΔS（三层蓄量之和）",
    params=PARAMS,
    fixed=FIXED,
    truth=TRUTH,
    simulate=simulate_unit,
    refs=(
        "Sugawara M. et al. Tank model and its application to Bird Creek. Research Note of National Research Center for Disaster Prevention, No.8, 1972.",
        "菅原正巳. 流出解析法. 共立出版, 1972.",
    ),
)
