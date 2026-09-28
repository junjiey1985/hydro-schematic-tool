# -*- coding: utf-8 -*-
"""P11 离线验收：HBV 融雪模块（气温驱动）。

覆盖：
  A. 雪模块单元级行为：水量平衡（含 Δ雪当量）闭合到机器精度；
     全负温→全部积雪零出流；全高温→与降雨版逐位一致；SWC ≤ CWH·SP
  B. 无气温回退：与降雨版逐位一致；气温缺测序列退化为降雨版
  C. simulate_basin / unit_context / eval_unit 气温透传（内存注入气温序列）
  D. OSSE + 率定冒烟（雪版真值回收，小预算）
  E. 其他三模型不受影响（t 参数被忽略）
"""
import copy
import sys

import numpy as np

sys.path.insert(0, ".")

from app import storage as st
from app.core.model.calibrate import run_calibration
from app.core.model.glue import run_glue
from app.core.model.models import get_model
from app.core.model.params import param_keys
from app.core.model.simulate import simulate_basin, truth_basin_flow, unit_context, eval_unit
from app.core.timeseries import csv_to_records

PID = "p_dee39fceeab6"

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


sub = st.read_subbasins(PID)
man = st.read_ts_manifest(PID)


def loader(kind, key):
    if kind == "temp":
        return TEMP_RECORDS
    p = st.ts_series_path(PID, kind, key)
    return csv_to_records(p.read_text(encoding="utf-8")) if p.exists() else []


# 气温序列（与演示数据同款季节公式，覆盖项目期 2015-01-01 起 1095 天）
from datetime import datetime, timedelta

_noise_rng = np.random.default_rng(99)
_t0 = datetime(2015, 1, 1)
TEMP_RECORDS = []
for i in range(1095):
    d = _t0 + timedelta(days=i)
    t = 11.0 - 12.5 * np.cos(2 * np.pi * (d.timetuple().tm_yday - 15) / 365.0)
    TEMP_RECORDS.append((d, round(float(t) + float(_noise_rng.normal(0, 0.9)), 2)))

man_temp = copy.deepcopy(man)
man_temp["series"]["temp"] = {
    "default": {"name": "流域平均气温", "interval_s": 86400,
                "start": TEMP_RECORDS[0][0].strftime("%Y-%m-%d %H:%M"),
                "end": TEMP_RECORDS[-1][0].strftime("%Y-%m-%d %H:%M")}
}
# 「无气温」场景：项目 manifest 现已含气温（演示数据自带），剔除后即为降雨版
man_no_temp = copy.deepcopy(man)
man_no_temp["series"].pop("temp", None)

hbv = get_model("hbv")
rng = np.random.default_rng(21)

print("=" * 78)
print("A. 雪模块单元级行为")
truth = dict(hbv.truth)
worst = 0.0
for _ in range(5):
    n = int(rng.integers(100, 400))
    p = np.round(rng.gamma(0.8, 6.0, n), 3)
    e = np.round(rng.uniform(0.5, 6.0, n), 3)
    t = np.round(rng.normal(3.0, 7.0, n), 2)
    r = hbv.simulate(p, e, truth, dt_days=1.0, t=t)
    worst = max(worst, abs(r["balance"]["closure"]))
    # 持水容量约束
    assert r["state"].SWC <= truth["CWH"] * r["state"].SP + 1e-9
check("A1 雪版闭合 ≤1e-9 mm", worst <= 1e-9, f"max|closure|={worst:.3e}")
n = 300
p = np.round(rng.gamma(0.8, 6.0, n), 3)
e = np.round(rng.uniform(0.5, 6.0, n), 3)
t_cold = np.full(n, -5.0)
r = hbv.simulate(p, e, truth, dt_days=1.0, t=t_cold)
snow_in = float(np.sum(truth["SFCF"] * p))
check("A2 全负温：雪当量 = Σ(SFCF·P)，出流仅初始蓄量", abs(r["state"].SP - snow_in) < 1e-9 and r["state"].SWC < 1e-9)
t_hot = np.full(n, 25.0)
r_no = hbv.simulate(p, e, truth, dt_days=1.0)
r_hot = hbv.simulate(p, e, truth, dt_days=1.0, t=t_hot)
check("A3 全高温与降雨版逐位一致", float(np.max(np.abs(r_hot["q"] - r_no["q"]))) == 0.0)
t_nan = np.full(n, np.nan)
r_nan = hbv.simulate(p, e, truth, dt_days=1.0, t=t_nan)
check("A4 气温全缺测退化为降雨版", float(np.max(np.abs(r_nan["q"] - r_no["q"]))) == 0.0)
t_short = np.full(n - 1, 10.0)
r_short = hbv.simulate(p, e, truth, dt_days=1.0, t=t_short)
check("A5 气温长度不符退化为降雨版", float(np.max(np.abs(r_short["q"] - r_no["q"]))) == 0.0)

print("=" * 78)
print("B. 演算链路透传（真实项目 + 内存注入气温）")
ctx0 = unit_context(sub, man_no_temp, loader, "S03", model="hbv")
check("B1 无气温时 ctx['t'] 为 None", ctx0.get("ok") and ctx0.get("t") is None)
ctx1 = unit_context(sub, man_temp, loader, "S03", model="hbv")
check("B2 有气温时 ctx['t'] 长度=序列长", ctx1.get("ok") and ctx1.get("t") is not None and len(ctx1["t"]) == ctx1["n"])
ev0 = eval_unit(ctx0, dict(truth))
ev1 = eval_unit(ctx1, dict(truth))
check("B3 eval_unit 有/无气温出流不同", float(np.max(np.abs(ev0["q"] - ev1["q"]))) > 1e-6)
check("B4 雪版 eval_unit 闭合", abs(ev1["balance"]["closure"]) <= 1e-6)

sim0 = simulate_basin(sub, man_no_temp, loader, model="hbv", return_raw=True)
sim1 = simulate_basin(sub, man_temp, loader, model="hbv", return_raw=True)
check("B5 simulate_basin 无气温 ok", bool(sim0.get("ok")))
check("B6 simulate_basin 有气温 ok 且闭合", bool(sim1.get("ok"))
      and max(abs(w["closure"]) for w in sim1["water_balance"]) <= 1e-6)
d = max(
    float(np.max(np.abs(np.array(a["series"]["sim"], dtype=float) - np.array(b["series"]["sim"], dtype=float))))
    for a, b in zip(sim0["units"], sim1["units"])
)
check("B7 雪版与降雨版流域出流有差异", d > 1e-3, f"maxΔ={d:.2f} m³/s")

print("=" * 78)
print("C. OSSE + 率定冒烟（雪版）")
flows = truth_basin_flow(sub, man_temp, loader, model="hbv")
check("C1 雪版 OSSE 真值流量非空", bool(flows), f"stations={len(flows)}")
res = run_calibration(sub, man_temp, loader,
                      config={"model": "hbv", "max_evals": 300, "seed": 7, "split": 0.7})
if res.get("ok"):
    spec = hbv.params
    bad = [f"{u['code']}.{k}={v}" for u in res["units"] for k, v in u["params"].items()
           if k in spec and not (spec[k]["min"] - 1e-9 <= v <= spec[k]["max"] + 1e-9)]
    nse = [(m.get("calib") or {}).get("nse") for m in res.get("metrics") or []]
    nse = [v for v in nse if v is not None]
    check("C2 雪版率定 ok，参数在区间内", not bad, f"nse={min(nse):.3f}~{max(nse):.3f}" if nse else "")
    check("C3 雪版率定 NSE 有限", bool(nse) and all(np.isfinite(v) for v in nse))
else:
    check("C2 雪版率定 ok", False, str(res.get("error"))[:80])

g = run_glue(sub, man_temp, loader, config={"model": "hbv", "n_samples": 120, "seed": 3, "split": 0.7})
check("C4 雪版 GLUE ok", bool(g.get("ok")) and len(g.get("stations") or []) > 0)

print("=" * 78)
print("D. 其他模型不受影响")
for k in ("xaj", "gr4j", "tank"):
    s = simulate_basin(sub, man_temp, loader, model=k, return_raw=True)
    check(f"D1 {k}: 有气温清单时照常（t 被忽略）", bool(s.get("ok"))
          and max(abs(w["closure"]) for w in s["water_balance"]) <= 1e-6)
check("D2 HBV 参数表含 5 个雪参数",
      all(k in param_keys("hbv") for k in ("TT", "CFMAX", "SFCF", "CWH", "CFR")),
      f"n_calib={hbv.n_calib}")

print("=" * 78)
print(f"TOTAL: PASS={PASS} FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
