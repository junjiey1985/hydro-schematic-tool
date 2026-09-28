# -*- coding: utf-8 -*-
"""多模型对比实验（P10/P11 框架验证）。

A 部分【结构误差实验】：同一套"实测"流量（当前项目演示数据 = HBV 雪版 OSSE 生成）
  下率定四个模型 → 量化「模型结构不匹配」的拟合代价。
B 部分【框架自洽性】：各模型用**自身真值参数**生成 OSSE 流量再率定回收
  → 每个模型都应能高 NSE 回收自己的真值（否则框架有 bug）。

产出：
  - backend/scripts/_compare_models_result.json   原始结果
  - docs/model-comparison.html                    自包含对比报告（内联 SVG，无外部依赖）
"""
import html
import json
import sys
import time
from datetime import datetime

import numpy as np

sys.path.insert(0, ".")

from app import storage as st
from app.core.model.calibrate import run_calibration
from app.core.model.models import get_model, list_model_info
from app.core.model.params import param_keys, truth_params
from app.core.model.simulate import simulate_basin, truth_basin_flow
from app.core.timeseries import csv_to_records

PID = "p_dee39fceeab6"
MAX_EVALS = int(sys.argv[1] if len(sys.argv) > 1 else 1200)
MODELS = ["xaj", "gr4j", "tank", "hbv"]

sub = st.read_subbasins(PID)
man = st.read_ts_manifest(PID)


def loader(kind, key):
    p = st.ts_series_path(PID, kind, key)
    return csv_to_records(p.read_text(encoding="utf-8")) if p.exists() else []


def calibrate(model_key, flow_loader=None, label=""):
    ld = flow_loader or loader
    t0 = time.perf_counter()
    res = run_calibration(sub, man, ld, config={"model": model_key, "max_evals": MAX_EVALS,
                                                "seed": 7, "split": 0.7})
    el = time.perf_counter() - t0
    assert res.get("ok"), (model_key, label, res.get("error"))
    metrics = {}
    for m in res.get("metrics") or []:
        metrics[m["code"]] = {
            "calib_nse": (m.get("calib") or {}).get("nse"),
            "valid_nse": (m.get("valid") or {}).get("nse"),
            "calib_kge": (m.get("calib") or {}).get("kge"),
            "calib_rmse": (m.get("calib") or {}).get("rmse"),
            "calib_pbias": (m.get("calib") or {}).get("pbias"),
        }
    return {"elapsed_s": round(el, 1), "metrics": metrics, "final_params": res.get("final_params") or {}}


def worst_best(d):
    vals = [v for v in d.values() if v is not None]
    return (min(vals), max(vals)) if vals else (None, None)


print("=" * 78)
print(f"A. 结构误差实验：同一实测（HBV 雪版 OSSE），四模型各 {MAX_EVALS} evals/单元")
part_a = {}
for k in MODELS:
    r = calibrate(k)
    c_lo, c_hi = worst_best({u: m["calib_nse"] for u, m in r["metrics"].items()})
    v_lo, v_hi = worst_best({u: m["valid_nse"] for u, m in r["metrics"].items()})
    part_a[k] = {**r, "calib_nse_range": [c_lo, c_hi], "valid_nse_range": [v_lo, v_hi]}
    print(f"  {k:5s} 率定 NSE {c_lo:.3f}~{c_hi:.3f}  验证 {v_lo:.3f}~{v_hi:.3f}  {r['elapsed_s']}s")

# 过程线（下游最末单元）：用各模型最终参数整体复算
print("A'. 全流域复算取过程线…")
series = {}
for k in MODELS:
    sim = simulate_basin(sub, man, loader, params=part_a[k]["final_params"], model=k, return_raw=True)
    u = sim["units"][-1]
    series[k] = {"time": u["series"]["time"], "sim": u["series"]["sim"], "obs": u["series"]["obs"],
                 "code": u["code"]}
last_code = series["xaj"]["code"]

print("=" * 78)
print("B. 框架自洽性：各模型对自身真值参数的 OSSE 回收")
part_b = {}
for k in MODELS:
    spec = get_model(k)
    flows = truth_basin_flow(sub, man, loader, model=k, seed=20260921)
    assert flows, k

    def flow_loader(kind, key, _flows=flows):
        if kind == "flow" and key in _flows:
            return _flows[key]["records"]
        return loader(kind, key)

    r = calibrate(k, flow_loader=flow_loader, label="self-osse")
    tp = truth_params(k)
    recover = []
    for code, prm in r["final_params"].items():
        for kk, tv in tp.items():
            if kk == "XE":
                continue
            cv = prm.get(kk)
            if cv is None or not tv:
                continue
            recover.append({"code": code, "param": kk, "truth": float(tv), "calib": float(cv),
                            "rel_pct": round((float(cv) - float(tv)) / abs(float(tv)) * 100, 1)})
    rec_bad = [x for x in recover if abs(x["rel_pct"]) > 15]
    c_lo, c_hi = worst_best({u: m["calib_nse"] for u, m in r["metrics"].items()})
    v_lo, v_hi = worst_best({u: m["valid_nse"] for u, m in r["metrics"].items()})
    part_b[k] = {**r, "calib_nse_range": [c_lo, c_hi], "valid_nse_range": [v_lo, v_hi],
                 "recover": recover, "recover_bad_ratio": round(len(rec_bad) / max(1, len(recover)), 3)}
    print(f"  {k:5s} 率定 NSE {c_lo:.3f}~{c_hi:.3f}  参数回收 |偏差|>15% 占比 {part_b[k]['recover_bad_ratio']:.0%}")

out = {
    "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    "pid": PID, "max_evals": MAX_EVALS, "obs_source": "HBV 雪版 OSSE（演示数据）",
    "models": {m["key"]: m for m in list_model_info()},
    "part_a": part_a, "part_b": part_b, "series": series, "last_code": last_code,
}
with open("_compare_models_result.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print("\n结果已写 _compare_models_result.json")
