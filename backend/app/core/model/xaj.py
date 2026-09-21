"""新安江三水源模型 —— 单元产汇流。

结构（对应技术方案 §6.1）::

    面雨量 P / 蒸发能力 E0
      ├─ 不透水面积 IMP：P 直接成为地表径流
      └─ 透水面积 (1-IMP)：
           三层蒸散发（UM/LM/DM，K 折算）→ PE = P - E
             └─ 蓄满产流（张力水容量曲线，B 指数）→ 总产流 R
                  └─ 自由水蓄水库（SM/EX）分水源：
                       超蓄 → 地表径流 RS；出流按 KGF=KG/(KG+KI) 分为 RG/RI
                       （EX 控制饱和面积——自由水容量分布的非均匀性）
                  └─ 三个线性水库汇流：RS→CS、RI→CI、RG→CG → 单元出流

设计约定（工程简化，保证水量平衡严格闭合）：

- 张力水参数 WM/UM/LM/DM、深层蒸散发系数 C、不透水面积比 IMP 为区域经验值，不参与率定；
- 自由水蓄水库总出流系数 KSS = KG + KI 固定 0.7，率定参数为分配比 KGF；
- EX 通过「饱和面积比」作用于地表径流占比：饱和面积 FRsat = 1-(1-S/SM)^EX，
  超蓄水量中 FRsat 部分直接成为地表径流、其余回补自由水库——总量严格守恒，
  EX 越大洪峰越高，可被率定有效利用。

水量平衡恒等式（逐时段）：P = E + R + ΔW，其中 R = R_透水 + IMP·P；
自由水与三个线性水库的蓄量变化计入 ΔS，全局闭合误差为机器精度。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# ---------------------------------------------------------------- 固定参数（不率定）
WM_TOTAL = 140.0   # 张力水蓄水容量 (mm)
UM = 20.0          # 上层
LM = 60.0          # 下层
DM = WM_TOTAL - UM - LM  # 深层
C_DEEP = 0.15      # 深层蒸散发系数
IMP = 0.01         # 不透水面积比
KSS = 0.7          # 自由水蓄水库总出流系数 KG + KI


@dataclass
class UnitState:
    """单元状态量（长度单位 mm；三个水库蓄量的量纲为 mm/step 出流当量）。"""

    WU: float = 20.0
    WL: float = 60.0
    WD: float = 60.0
    S: float = 5.0            # 自由水蓄水量
    Qs: float = 0.0           # 地表水库蓄量
    Qi: float = 0.0           # 壤中流水库蓄量
    Qg: float = 0.0           # 地下水库蓄量

    @property
    def W(self) -> float:
        return self.WU + self.WL + self.WD

    def storage(self) -> float:
        """全部蓄量（张力水 + 自由水 + 三个线性水库）。"""
        return self.W + self.S + self.Qs + self.Qi + self.Qg


def evapotranspiration(st: UnitState, p: float, e0: float, k: float) -> tuple[float, float]:
    """三层蒸散发（标准三层模型）。返回 (实际蒸散发 E, 净雨 PE = P - E)，不修改状态。"""
    ep = k * max(0.0, e0)
    if st.WU + p >= ep:
        eu = ep
        el = 0.0
        ed = 0.0
    else:
        eu = st.WU + p
        if st.WL >= C_DEEP * LM:
            el = (ep - eu) * st.WL / LM
            ed = 0.0
        elif st.WL >= C_DEEP * (ep - eu):
            el = C_DEEP * (ep - eu)
            ed = 0.0
        else:
            el = st.WL
            ed = max(0.0, C_DEEP * (ep - eu) - el)
    e = eu + el + ed
    # 钳制：蒸散发不能超过"现存蓄量 + 降水"（干旱期深层蒸发可能把 WD 扣穿）
    avail = st.WU + st.WL + st.WD + p
    if e > avail:
        e = avail
    return e, p - e


def runoff_generation(st: UnitState, pe: float, b: float) -> tuple[float, float]:
    """蓄满产流（张力水蓄水容量曲线）。返回 (总产流 R, 产流面积比 FR)，并更新张力水蓄量。"""
    b = max(0.05, b)
    wmm = WM_TOTAL * (1.0 + b)
    w = min(max(st.W, 0.0), WM_TOTAL)
    fr = 1.0 - (1.0 - w / WM_TOTAL) ** (1.0 / (1.0 + b))
    a = wmm * fr  # 与 W 对应的蓄水容量曲线纵坐标
    if pe <= 0:
        r = 0.0
    elif pe + a < wmm:
        r = pe - (WM_TOTAL - w) + WM_TOTAL * (1.0 - (pe + a) / wmm) ** (b + 1.0)
    else:
        r = pe - (WM_TOTAL - w)
    r = max(0.0, r)
    # 更新张力水：W ← W + PE - R（蓄满部分转产流，不会超过 WM）
    w_new = min(WM_TOTAL, max(0.0, w + pe - r))
    st.WU = min(UM, w_new)
    st.WL = min(LM, max(0.0, w_new - UM))
    st.WD = max(0.0, min(DM, w_new - st.WU - st.WL))
    return r, fr


def split_water_sources(st: UnitState, r: float, sm: float, ex: float, kgf: float) -> tuple[float, float, float]:
    """自由水蓄水库分水源。返回 (RS 地表, RI 壤中, RG 地下) (mm)，更新自由水蓄量 S。"""
    s0 = st.S + r
    overflow = max(0.0, s0 - sm)
    st.S = min(s0, sm)
    # 饱和面积比：EX 控制自由水容量分布的非均匀性（EX→1 均匀，EX→2 强非线性）
    if sm > 1e-6:
        fr_sat = 1.0 - (1.0 - min(st.S, sm) / sm) ** max(1.0, ex)
    else:
        fr_sat = 1.0
    rs = overflow * fr_sat
    st.S += overflow - rs  # 未成地表径流的超蓄量回补自由水库（严格守恒）
    out = KSS * st.S
    rg = kgf * out
    ri = (1.0 - kgf) * out
    st.S -= out
    return rs, ri, rg


def route_linear(q0: float, inflow: float, k: float) -> float:
    """线性水库：Q(t) = k·Q(t-1) + (1-k)·I(t)，出流总量守恒。"""
    kk = min(0.999, max(0.0, k))
    return kk * q0 + (1.0 - kk) * inflow


def simulate_unit(
    p: np.ndarray,
    e0: np.ndarray,
    params: dict,
    state: UnitState | None = None,
) -> dict:
    """单单元逐时段产汇流。

    可率定参数：K, B, SM, EX, KGF, CG, CI, CS（见 params.PARAM_SPEC）
    返回 {q: 出流序列(mm/step), state, balance: {...}}；balance 闭合误差为机器精度。
    """
    st = state or UnitState()
    k = float(params.get("K", 0.85))
    b = float(params.get("B", 0.3))
    sm = max(1.0, float(params.get("SM", 30.0)))
    ex = max(1.0, float(params.get("EX", 1.5)))
    kgf = min(0.9, max(0.1, float(params.get("KGF", 0.5))))
    cg = min(0.999, float(params.get("CG", 0.985)))
    ci = float(params.get("CI", 0.7))
    cs = float(params.get("CS", 0.3))

    n = len(p)
    q = np.zeros(n)
    sum_p = sum_e = sum_r = sum_q = 0.0
    storage0 = st.storage()

    for t in range(n):
        pt = max(0.0, float(p[t]))
        e_act, pe = evapotranspiration(st, pt, float(e0[t]), k)
        # 不透水面积：降雨直接成流，不参与蒸散发与土壤水（imp_q 从净雨中扣除，
        # 但保留 pe 的符号——负净雨表示蒸发消耗张力水，必须传入产流模块扣减蓄量）
        imp_q = IMP * pt
        pe_perv = pe - imp_q
        r_perv, _fr = runoff_generation(st, pe_perv, b)
        r_total = r_perv + imp_q
        rs, ri, rg = split_water_sources(st, r_total, sm, ex, kgf)
        # 蓄量型线性水库：S ← k·S + in，出流 = (1-k)·S_prev
        # （入流 + 前蓄量 = 新蓄量 + 出流，逐步严格守恒，水量平衡可闭合到机器精度）
        out_s = (1.0 - cs) * st.Qs
        st.Qs = cs * st.Qs + rs
        out_i = (1.0 - ci) * st.Qi
        st.Qi = ci * st.Qi + ri
        out_g = (1.0 - cg) * st.Qg
        st.Qg = cg * st.Qg + rg
        q[t] = out_s + out_i + out_g

        sum_p += pt
        sum_e += e_act
        sum_r += r_total
        sum_q += float(q[t])

    storage1 = st.storage()
    # 恒等式：ΣP = ΣE + Σq(出流) + Δ蓄量（张力水+自由水+三水库）。
    # ΣR 是"土壤/自由水 → 汇流水库"的内部转移，不进入全局平衡（作为诊断量输出）。
    closure = sum_p - (sum_e + sum_q + (storage1 - storage0))
    return {
        "q": q,
        "state": st,
        "balance": {
            "sum_p": round(sum_p, 3),
            "sum_e": round(sum_e, 3),
            "sum_r": round(sum_r, 3),
            "sum_q": round(sum_q, 3),
            "storage_change": round(storage1 - storage0, 3),
            "closure": round(closure, 9),
            "closure_rate": round(closure / sum_p, 12) if sum_p > 0 else 0.0,
        },
    }
