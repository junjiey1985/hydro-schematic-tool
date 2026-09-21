# -*- coding: utf-8 -*-
"""性能基线：拆解 simulate_basin 的耗时构成，判断率定是否需要加速。"""
import sys
import time

sys.path.insert(0, ".")

import numpy as np

from app import storage as st
from app.core.model import routing
from app.core.model.params import default_params
from app.core.model.simulate import simulate_basin
from app.core.model.xaj import simulate_unit
from app.core.timeseries import csv_to_records

PID = sys.argv[1] if len(sys.argv) > 1 else "p_dee39fceeab6"
sub = st.read_subbasins(PID)
manifest = st.read_ts_manifest(PID)


def loader(kind, key):
    p = st.ts_series_path(PID, kind, key)
    if not p.exists():
        return []
    return csv_to_records(p.read_text(encoding="utf-8"))


n = 1095

# ---------- 1) 单次 simulate_unit（纯模型核）
p = np.random.default_rng(1).gamma(0.4, 6.0, n)
e = np.full(n, 1.83)
prm = default_params(100.0)
runs = 20
t0 = time.perf_counter()
for _ in range(runs):
    simulate_unit(p, e, prm)
t_unit = (time.perf_counter() - t0) / runs
print(f"simulate_unit          : {t_unit * 1000:7.2f} ms  ({n} 步)")

# ---------- 2) 马斯京根演算（单河段）
q = np.random.default_rng(2).gamma(1.5, 40.0, n)
runs = 20
t0 = time.perf_counter()
for _ in range(runs):
    routing.muskingum_route(q, 12.0, 0.2, 24.0)
t_route = (time.perf_counter() - t0) / runs
print(f"muskingum_route        : {t_route * 1000:7.2f} ms")

# ---------- 3) 首次 simulate_basin（含序列读盘）
t0 = time.perf_counter()
r1 = simulate_basin(sub, manifest, loader, params={})
t_first = time.perf_counter() - t0
print(f"simulate_basin 首次     : {t_first * 1000:7.2f} ms  (含读盘/面雨量装配)")

# ---------- 4) 再次调用（loader_cached 默认参数缓存命中）
t0 = time.perf_counter()
simulate_basin(sub, manifest, loader, params={})
t_warm = time.perf_counter() - t0
print(f"simulate_basin 二次     : {t_warm * 1000:7.2f} ms")

# ---------- 5) 面雨量装配单独计时
evap_map = None
t0 = time.perf_counter()
for sb in sub["subbasins"]:
    sts = [(s.get("id") or s.get("name"), float(s.get("weight") or 0)) for s in sb.get("rain_stations") or []]
    maps = []
    for sid, w in sts:
        pth = st.project_dir(PID) / "timeseries" / "rain" / f"{sid}.csv"
        if pth.exists():
            maps.append(({dt: v for dt, v in csv_to_records(pth.read_text(encoding="utf-8"))}, w))
print(f"面雨量装配(含读盘)      : {(time.perf_counter() - t0) * 1000:7.2f} ms")

print()
print(f"→ 单次「单元产流 + 单河段演算」理论成本 ≈ {(t_unit + t_route) * 1000:.1f} ms")
print(f"→ 5000 次评估 ≈ {(t_unit + t_route) * 5000 / 60:.1f} 分钟/单元")
print(f"→ 若按当前 simulate_basin 全流域成本: {t_warm * 5000 / 60:.1f} 分钟/单元")
