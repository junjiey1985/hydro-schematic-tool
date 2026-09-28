# -*- coding: utf-8 -*-
"""P3 离线冒烟测试：SCE-UA 链式率定能否回收 OSSE 真值参数。"""
import json
import os
import sys
import time

sys.path.insert(0, ".")

from app import storage as st
from app.core.model.calibrate import run_calibration
from app.core.model.models import get_model
from app.core.model.params import param_keys
from app.core.timeseries import csv_to_records

PID = sys.argv[1] if len(sys.argv) > 1 else "p_dee39fceeab6"
MAX_EVALS = int(sys.argv[2]) if len(sys.argv) > 2 else 1200

sub = st.read_subbasins(PID)
man = st.read_ts_manifest(PID)
# 必须用**项目当前模型**率定：演示观测是由该模型的真值参数生成的（见 routers/timeseries.py）。
# 不传 model 会回退到全局默认（xaj），导致用错模型贴合观测、真值回收完全失真。
MODEL = st.read_project_model(PID)
CALIB_KEYS = set(param_keys(MODEL))
print(f"项目 {PID}  模型 = {MODEL}（{get_model(MODEL).name}）")


def loader(kind, key):
    p = st.ts_series_path(PID, kind, key)
    return csv_to_records(p.read_text(encoding="utf-8")) if p.exists() else []


progress = []


def on_progress(payload):
    progress.append(payload)
    if payload.get("phase") in ("start", "done", "skip", "stopped"):
        print("  ·", {k: v for k, v in payload.items() if k != "params"})


truth_path = os.path.join("data", "projects", PID, "calibration", "demo_truth.json")
truth = json.loads(open(truth_path, encoding="utf-8").read()) if os.path.exists(truth_path) else {}
if truth.get("model") and truth["model"] != MODEL:
    print(f"  !! 演示真值模型 = {truth['model']}，与项目模型 {MODEL} 不一致 —— 观测数据需重新生成后再率定")

print("=" * 76)
print(f"SCE-UA 链式率定（max_evals={MAX_EVALS}，split=0.7）")
t0 = time.perf_counter()
res = run_calibration(sub, man, loader,
                      config={"max_evals": MAX_EVALS, "seed": 7, "split": 0.7, "model": MODEL},
                      on_progress=on_progress)
el = time.perf_counter() - t0
if not res.get("ok"):
    print("FAIL:", res)
    sys.exit(1)
print(f"\n总耗时 {el:.1f}s  状态 {res['status']}")

print("\n-- 逐单元 --")
for u in res["units"]:
    print(f"  {u['code']} calibrated={u['calibrated']} borrowed={u['borrowed_from']} "
          f"evals={u['evals']} gens={u['gens']} {u['status']} {u['elapsed_s']}s obj={u.get('objective')}")
    print(f"      参数: " + ", ".join(f"{k}={v:.3f}" for k, v in sorted(u["params"].items()) if k in CALIB_KEYS))

print("\n-- 真值回收对比（率定值 vs 真值，相对偏差%）--")
tp = truth.get("params") or {k: float(v) for k, v in get_model(MODEL).truth.items()}
for u in res["units"]:
    if not u["calibrated"]:
        continue
    diffs = []
    for k, tv in tp.items():
        pv = u["params"].get(k)
        if pv is None or tv in (None, 0):
            continue
        rel = (pv - tv) / abs(tv) * 100
        diffs.append(f"{k}:{pv:.3f}/{tv}{rel:+.1f}%")
    print(f"  {u['code']}: " + "  ".join(diffs))

print("\n-- 分期指标（最终参数集全流域复算）--")
for m in res["metrics"]:
    c, v = m.get("calib") or {}, m.get("valid") or {}
    print(f"  {m['code']} 率定期[{m['calib_range'][0][:10]}~{m['calib_range'][1][:10]}] "
          f"NSE={c.get('nse'):.4f} PBIAS={c.get('pbias'):+.2f}% "
          f"| 验证期[{m['valid_range'][0][:10]}~] NSE={v.get('nse'):.4f} PBIAS={v.get('pbias'):+.2f}%")

best = min((m["calib"]["nse"] for m in res["metrics"] if m.get("calib")), default=-9)
bestv = min((m["valid"]["nse"] for m in res["metrics"] if m.get("valid")), default=-9)
print(f"\n验收: 率定期最差 NSE = {best:.4f} (目标 >0.9)   验证期最差 NSE = {bestv:.4f}")
print("PASS" if best > 0.9 and bestv > 0.9 else "CHECK")
