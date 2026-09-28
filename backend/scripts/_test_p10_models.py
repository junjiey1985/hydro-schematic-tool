# -*- coding: utf-8 -*-
"""P10 离线验收：多概念性集总模型（xaj / gr4j / tank / hbv）。

覆盖：
  A. 注册表与参数体系（spec / fixed / truth / closure / 默认参数钳制）
  B. 单元级水量平衡闭合（随机输入 × 多时段长度 × 步长）
  C. 全流域 simulate_basin 各模型可跑通且闭合
  D. 各模型 SCE-UA 率定冒烟（小预算）+ 参数区间合法
  E. GLUE 各模型小样本冒烟
  F. 模型切换语义（storage 读写 default.json 的 model 字段，参数不跨模型）
"""
import json
import sys
import time

import numpy as np

sys.path.insert(0, ".")

from app import storage as st
from app.core.model.calibrate import run_calibration
from app.core.model.glue import run_glue
from app.core.model.models import DEFAULT_MODEL, get_model, list_model_info
from app.core.model.params import (
    closure_expr,
    default_params,
    fixed_spec,
    param_keys,
    param_spec,
    sanitize_params,
    truth_params,
)
from app.core.model.simulate import simulate_basin, truth_basin_flow
from app.core.timeseries import csv_to_records

PID = sys.argv[1] if len(sys.argv) > 1 else "p_dee39fceeab6"
MAX_EVALS = int(sys.argv[2] if len(sys.argv) > 2 else 240)

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
    p = st.ts_series_path(PID, kind, key)
    return csv_to_records(p.read_text(encoding="utf-8")) if p.exists() else []


MODELS = ["xaj", "gr4j", "tank", "hbv"]

print("=" * 78)
print("A. 注册表与参数体系")
infos = {m["key"]: m for m in list_model_info()}
check("A1 四个模型已注册", sorted(infos.keys()) == sorted(MODELS), str(sorted(infos.keys())))
for k in MODELS:
    m = get_model(k)
    spec = param_spec(k)
    check(
        f"A2 {k}: spec/fixed/truth/closure 齐全",
        param_keys(k)
        and fixed_spec(k) is not None
        and truth_params(k)
        and bool(closure_expr(k).strip()),
        f"n_calib={infos[k]['n_calib']}",
    )
    # 默认参数全部落区间内
    dp = default_params(model=k)
    bad = [
        f"{kk}:{dp[kk]}∉[{spec[kk]['min']},{spec[kk]['max']}]"
        for kk in param_keys(k)
        if not (spec[kk]["min"] - 1e-12 <= dp[kk] <= spec[kk]["max"] + 1e-12)
    ]
    check(f"A3 {k}: 默认参数在率定区间内", not bad, "; ".join(bad))
    # 钳制：越界输入被拉回
    wild = {kk: spec[kk]["max"] * 10 + 5 for kk in param_keys(k)}
    clamped = sanitize_params(wild, k)
    bad = [kk for kk in param_keys(k) if clamped[kk] > spec[kk]["max"] + 1e-9]
    check(f"A4 {k}: sanitize 钳制越界参数", not bad)
    # 未知参数被剔除
    extra = dict(dp)
    extra["_junk"] = 1.23
    check(f"A5 {k}: 未知字段被剔除", "_junk" not in sanitize_params(extra, k))

print("=" * 78)
print("B. 单元级水量平衡闭合（随机输入）")
rng = np.random.default_rng(11)
for k in MODELS:
    worst = 0.0
    for _ in range(4):
        n = int(rng.integers(80, 300))
        p = np.round(rng.gamma(0.8, 6.0, n), 3)
        e = np.round(rng.uniform(0.5, 6.0, n), 3)
        for dt in (1.0, 0.5):
            r = get_model(k).simulate(p, e, dict(truth_params(k)), dt_days=dt)
            worst = max(worst, abs(r["balance"]["closure"]))
    check(f"B1 {k}: 闭合误差 ≤1e-9 mm", worst <= 1e-9, f"max|closure|={worst:.3e}")

print("=" * 78)
print("C. 全流域 simulate_basin（真实项目）")
sim_store = {}
for k in MODELS:
    t0 = time.perf_counter()
    res = simulate_basin(sub, man, loader, model=k, return_raw=True)
    el = time.perf_counter() - t0
    ok = bool(res.get("ok"))
    sim_store[k] = res
    if ok:
        wc = max(abs(w.get("closure", 0.0)) for w in res["water_balance"])
        nn = sum(
            1
            for u in res["units"]
            if all(v is not None and np.isfinite(v) for v in (u["series"]["sim"] or [0.0]))
        )
        check(f"C1 {k}: 模拟 ok，闭合 ≤1e-6", wc <= 1e-6, f"{el:.2f}s units={len(res['units'])} worst={wc:.2e}")
        check(f"C2 {k}: 出流全有限", nn == len(res["units"]), f"{nn}/{len(res['units'])}")
        check(f"C3 {k}: 回显 model/model_name", res.get("model") == k and bool(res.get("model_name")))
    else:
        check(f"C1 {k}: 模拟 ok", False, str(res.get("error"))[:80])

# 不同模型出流应有可感知差异（结构不同不应给出完全相同序列）
qa = np.array(sim_store["xaj"]["units"][-1]["series"]["sim"], dtype=float)
diffs = {
    k: float(np.max(np.abs(np.array(sim_store[k]["units"][-1]["series"]["sim"], dtype=float) - qa)))
    for k in MODELS
    if k != "xaj"
}
check("C4 各模型出流存在结构差异", all(v > 1e-6 for v in diffs.values()),
      ", ".join(f"{k}:{v:.1f}" for k, v in diffs.items()))

print("=" * 78)
print(f"D. SCE-UA 率定冒烟（max_evals={MAX_EVALS}）")
for k in MODELS:
    t0 = time.perf_counter()
    res = run_calibration(sub, man, loader,
                          config={"model": k, "max_evals": MAX_EVALS, "seed": 7, "split": 0.7})
    el = time.perf_counter() - t0
    if not res.get("ok"):
        check(f"D1 {k}: 率定 ok", False, str(res.get("error"))[:80])
        continue
    spec = param_spec(k)
    bad = []
    for u in res["units"]:
        for kk, vv in u["params"].items():
            if kk in spec and not (spec[kk]["min"] - 1e-9 <= vv <= spec[kk]["max"] + 1e-9):
                bad.append(f"{u['code']}.{kk}={vv}")
    nse = [
        (m.get("calib") or {}).get("nse")
        for m in (res.get("metrics") or [])
        if (m.get("calib") or {}).get("nse") is not None
    ]
    check(f"D1 {k}: 率定 ok，参数在区间内", not bad,
          f"{el:.1f}s nse={min(nse):.3f}~{max(nse):.3f}" if nse else f"{el:.1f}s 无指标")
    check(f"D2 {k}: NSE 有限", bool(nse) and all(np.isfinite(v) for v in nse))
    check(f"D3 {k}: 结果回显 model", res.get("model") == k)

print("=" * 78)
print("E. GLUE 冒烟（n=120）")
for k in ("xaj", "hbv", "gr4j", "tank"):
    t0 = time.perf_counter()
    res = run_glue(sub, man, loader,
                   config={"model": k, "n_samples": 120, "seed": 3, "split": 0.7})
    el = time.perf_counter() - t0
    ok = bool(res.get("ok"))
    if ok:
        nstation = len(res.get("stations") or {})
        check(f"E1 {k}: GLUE ok，站点带齐全", nstation > 0, f"{el:.1f}s stations={nstation} model={res.get('model')}")
    else:
        check(f"E1 {k}: GLUE ok", False, str(res.get("error"))[:80])

print("=" * 78)
print("F. 模型切换语义与 OSSE 真值")
saved_model = st.read_project_model(PID)
try:
    st.write_project_model(PID, "hbv")
    check("F1 write/read_project_model", st.read_project_model(PID) == "hbv")
    # 各模型 OSSE 真值流程可产出合成流量
    for k in MODELS:
        flows = truth_basin_flow(sub, man, loader, model=k)
        check(f"F2 {k}: OSSE 真值流量非空", bool(flows), f"stations={len(flows)}")
finally:
    st.write_project_model(PID, saved_model)
    check("F3 恢复项目原模型", st.read_project_model(PID) == saved_model, saved_model)
# 参数不跨模型：把新安江的参数名喂给 HBV，sanitize 应忽略无关键、
# 返回补全默认且全部在 HBV 区间内的完整参数集（路由层据此保证切换后不串味）
foreign = sanitize_params({"K": 0.5, "SM": 60.0, "CG": 0.9}, "hbv")
fspec = param_spec("hbv")
bad = [
    kk
    for kk, vv in foreign.items()
    if kk in fspec and not (fspec[kk]["min"] - 1e-9 <= vv <= fspec[kk]["max"] + 1e-9)
]
check("F4 异模型参数被忽略且返回 HBV 完整合法参数集",
      set(foreign) == set(param_keys("hbv")) | {"KE"} and not bad,
      f"keys={len(foreign)} bad={bad}")

print("=" * 78)
print(f"TOTAL: PASS={PASS} FAIL={FAIL}")
sys.exit(1 if FAIL else 0)
