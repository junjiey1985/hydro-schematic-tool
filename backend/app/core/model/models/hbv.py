"""HBV 模型（含融雪模块）—— 雪量平衡 + 土壤湿度带 + 响应函数 + 单位线汇流。

出处：瑞典 SMHI，Bergström & Forsman (1973)、Bergström (1976/1992)；
本实现遵循 Seibert 的 HBV-light 版本（最广泛使用与验证的简化版）。

**雪模块按输入自动启停**：调用方传入气温序列 ``t`` 即启用（度日因子法），
不传（或全缺测）则退化为降雨版，与无长期积雪流域的 XAJ 口径一致。

雪模块（逐时段）::

    T ≥ TT（TT 为雨雪分界气温）：
        降雨 rain = P；融雪 melt = min(SP, CFMAX·(T − TT))
        SP ← SP − melt；SWC ← SWC + melt
    T < TT：
        降雪 snow = SFCF·P 入雪当量 SP；再冻结 RF = min(SWC, CFR·CFMAX·(TT − T))
        SP ← SP + snow + RF；SWC ← SWC − RF
    融雪水出流：液态持水 SWC 超出持水容量 CWH·SP 的部分排出（雪化尽则全排）；
    有雪层时降雨先留在雪内（SWC），土壤输入 = 排出的液态水；无雪时降雨直接入土壤。

土壤湿度带与响应函数与降雨版相同（输入换成 rain + 融雪水出流）::

    土壤：E_act = CET·E0·min(1, SM/(FC·LP))；recharge = in·(SM/FC)^BETA
          （显式欧拉下 SM 候选值越过 FC 的部分即刻下渗、计入补给，严禁截断丢弃）
    响应：SUZ（Q0/K0 阈值出流 + PERC 下渗 + Q1/K1）、SLZ（Q2/K2）
    汇流：MAXBAS 三角单位线

水量平衡恒等式：``ΣP = ΣE + Σq + ΔS``，
其中 S = 雪当量 SP + 融雪持水 SWC + 土壤 SM + 两层响应箱 SUZ/SLZ + 单位线在途蓄量；
启用雪模块时 ``P`` 为**经 SFCF 校正后的模型输入**（固态 × SFCF + 液态原值）。
单位线用累积曲线差分实现并显式记账在途蓄量，土壤带超容下渗显式入账，恒等式严格闭合。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .base import CumulativeUH, ModelSpec, balance_of, clamp, triangular_s_curve


@dataclass
class UnitState:
    """HBV 状态量。雪当量/融雪持水/单位线在途蓄量全部入账，保证水量平衡可闭合。"""

    SM: float = 100.0    # 土壤含水量 (mm)
    SUZ: float = 10.0    # 上层响应箱蓄量 (mm)
    SLZ: float = 50.0    # 下层响应箱蓄量 (mm)
    SP: float = 0.0      # 雪当量 (mm)
    SWC: float = 0.0     # 雪内持水（液态） (mm)
    uh: CumulativeUH | None = field(default=None, repr=False)

    def storage(self) -> float:
        return self.SM + self.SUZ + self.SLZ + self.SP + self.SWC + (
            self.uh.storage() if self.uh else 0.0
        )


def simulate_unit(
    p: np.ndarray,
    e0: np.ndarray,
    params: dict,
    state: UnitState | None = None,
    dt_days: float = 1.0,
    t: "np.ndarray | None" = None,
) -> dict:
    """单单元逐时段演算（可率定参数见 PARAMS）。

    ``t``（气温，°C）为可选输入：提供且含有效值时启用融雪模块，
    否则视为降雨版（全部降水直接入土壤），与 P10 行为逐位一致。
    """
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
    # 雪模块参数（仅气温输入有效时参与演算）
    tt = clamp(float(params.get("TT", 0.0)), -2.5, 2.5)
    cfmax = clamp(float(params.get("CFMAX", 3.0)), 0.2, 12.0)
    sfcf = clamp(float(params.get("SFCF", 1.0)), 0.4, 1.6)
    cwh = clamp(float(params.get("CWH", 0.1)), 0.0, 0.2)
    cfr = clamp(float(params.get("CFR", 0.05)), 0.0, 0.1)

    st = state or UnitState()
    st.SM = clamp(st.SM, 0.0, fc)
    dt = max(1e-6, float(dt_days))
    if st.uh is None:
        st.uh = CumulativeUH(
            lambda x, _t=max(maxbas, dt): triangular_s_curve(x, _t),
            dt,
            max(maxbas, dt),
        )

    # 雪模块启停判定：气温序列存在且含有效值
    snow_mode = t is not None and len(t) == len(p) and bool(np.isfinite(np.asarray(t, dtype=float)).any())

    n = len(p)
    q = np.zeros(n)
    sum_p = sum_e = sum_r = sum_q = 0.0
    storage0 = st.storage()

    t_arr = np.asarray(t, dtype=float) if snow_mode else None

    for i in range(n):
        pt = max(0.0, float(p[i]))
        e0t = max(0.0, float(e0[i]))

        if snow_mode:
            tc = float(t_arr[i])
            if not np.isfinite(tc):
                tc = 10.0  # 缺测气温按无雪处理
            # ---- 雪量平衡：雨雪分配 / 融雪 / 再冻结
            if tc < tt:
                snow = sfcf * pt          # 固态降水（SFCF 校正）
                rain = 0.0
                st.SP += snow
                rf = min(st.SWC, cfr * cfmax * (tt - tc))
                st.SWC -= rf
                st.SP += rf
            else:
                snow = 0.0
                rain = pt
                melt = min(st.SP, cfmax * (tc - tt))
                st.SP -= melt
                st.SWC += melt
            # ---- 融雪水出流：持水容量 CWH·SP，超出部分必须排出（雪化尽则全排）
            if st.SP > 0.0 or st.SWC > 0.0:
                st.SWC += rain            # 有雪层时降雨先留在雪内
                out_melt = max(0.0, st.SWC - cwh * st.SP)
                st.SWC -= out_melt
                water_in = out_melt
            else:
                water_in = rain
            sum_p += snow + rain          # 恒等式口径：SFCF 校正后的模型输入
        else:
            water_in = pt
            sum_p += pt

        # ---- 土壤湿度带：蒸发（用步初 SM）+ 产流补给
        e_act = cet * e0t * min(1.0, st.SM / (fc * lp))
        e_act = min(e_act, st.SM + water_in)      # 不能超过可用水量
        if water_in > 0.0:
            recharge = water_in * min(1.0, st.SM / fc) ** beta
        else:
            recharge = 0.0
        # 显式欧拉下 recharge 用的是**步初** SM，SM 可能越过 FC（SM 略低于 FC 时来一场大
        # 降水，按旧 SM 算出的补给偏少 → 候选值超容）。超容的水不能凭空丢弃——那会破坏
        # 水量平衡恒等式（曾在大 BETA 参数下每个时段漏 1~4 mm，1095 时段累计 7.1 mm）。
        # 按物理含义处理：超出田间持水量的部分即刻下渗，计入本步补给（→ 响应箱 → 出流）；
        # 若候选值为负（防御性分支）则从补给中回补，两侧都严格守恒。
        sm_cand = st.SM + water_in - recharge - e_act
        st.SM = min(fc, max(0.0, sm_cand))
        recharge += sm_cand - st.SM

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
        q[i] = st.uh.step(q_gen)

        sum_e += e_act
        sum_r += recharge
        sum_q += float(q[i])

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
    "TT": {"label": "雨雪分界气温", "default": 0.0, "min": -2.5, "max": 2.5, "unit": "°C", "calibrate": True},
    "CFMAX": {"label": "度日融雪因子", "default": 3.0, "min": 0.5, "max": 12.0, "unit": "mm/°C/d", "calibrate": True},
    "SFCF": {"label": "固态降水校正系数", "default": 1.0, "min": 0.4, "max": 1.6, "calibrate": True},
    "CWH": {"label": "雪持水率", "default": 0.1, "min": 0.0, "max": 0.2, "calibrate": True},
    "CFR": {"label": "再冻结系数", "default": 0.05, "min": 0.0, "max": 0.1, "calibrate": True},
    "K0": {"label": "上层快出流系数", "default": 0.3, "min": 0.02, "max": 0.8, "calibrate": True},
    "K1": {"label": "上层慢出流系数", "default": 0.1, "min": 0.005, "max": 0.5, "calibrate": True},
    "K2": {"label": "基流出流系数", "default": 0.02, "min": 0.001, "max": 0.15, "calibrate": True},
    "UZL": {"label": "上层出流阈值", "default": 20.0, "min": 0.0, "max": 100.0, "unit": "mm", "calibrate": True},
    "PERC": {"label": "下层下渗量", "default": 2.0, "min": 0.0, "max": 12.0, "unit": "mm/时段", "calibrate": True},
    "MAXBAS": {"label": "单位线汇流时间", "default": 3.0, "min": 0.5, "max": 8.0, "unit": "d", "calibrate": True},
}

FIXED = {
    "SNOW": {"label": "积雪/融雪模块", "value": "提供气温序列时自动启用（度日因子法）"},
    "UH": {"label": "汇流单位线", "value": "三角分布（MAXBAS）"},
}

TRUTH = {
    "FC": 260.0,
    "LP": 0.85,
    "BETA": 1.8,
    "CET": 0.95,
    "TT": 0.3,
    "CFMAX": 3.5,
    "SFCF": 1.05,
    "CWH": 0.1,
    "CFR": 0.05,
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
    name="HBV",
    origin="瑞典 SMHI，Bergström 1976；本实现按 Seibert 的 HBV-light",
    structure="雪量平衡（TT/CFMAX/SFCF/CWH/CFR，有气温时启用）→ 土壤湿度带（FC/LP/BETA）→ 两层响应函数（K0/K1/K2/UZL/PERC）→ MAXBAS 单位线汇流",
    notes=(
        "气温序列存在时自动启用积雪/融雪模块（度日因子法），"
        "无气温时为降雨版（全部降水直接入土壤）。"
        "单位线为三角分布，汇流时间 MAXBAS 以「天」计，随模拟时段自动离散。"
    ),
    closure_expr="ΣP = ΣE + Σq + ΔS（雪当量 + 融雪持水 + 土壤含水量 + 两层响应箱 + 单位线在途蓄量；雪版 P 为 SFCF 校正后输入）",
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
