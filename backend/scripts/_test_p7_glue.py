# -*- coding: utf-8 -*-
"""P7 验收：GLUE 参数不确定性分析接口。

用法：python _test_p7_glue.py [PID] [N_SAMPLES]
"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8013"
PID = sys.argv[1] if len(sys.argv) > 1 else "p_dee39fceeab6"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 500
OK = []


def call(method, path, body=None, timeout=600):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw[:300]


def check(name, cond, detail=""):
    OK.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name} {detail}")


print("=" * 76)
print(f"① GLUE 分析（n_samples={N}, threshold=0.7）")
t0 = time.time()
s, r = call("POST", f"/api/projects/{PID}/calibration/glue",
            {"n_samples": N, "threshold": 0.7, "seed": 7})
check("HTTP 200", s == 200, f"(实际 {s}, 用时 {time.time() - t0:.1f}s)")
if s != 200:
    print("   ", r)
    sys.exit(1)

check("method=GLUE", r.get("method") == "GLUE")
check("n_samples 回显", r.get("n_samples") == N)
check("用时合理（<120s）", (r.get("elapsed_s") or 999) < 120, f"({r.get('elapsed_s')}s)")

stations = r.get("stations") or []
check("三站均有输出", len(stations) == 3, f"({[x['code'] for x in stations]})")
for u in stations:
    se = u.get("series") or {}
    n_pts = len(se.get("time") or [])
    check(f"{u['code']} 序列长度一致", n_pts == len(se.get("q05") or []) == len(se.get("q95") or []) == len(se.get("q50") or []) == len(se.get("sim_best") or []) and n_pts > 100, f"({n_pts} 点)")
    q05, q95 = se.get("q05") or [], se.get("q95") or []
    import math
    ok_band = all((b >= a - 1e-6) for a, b in zip(q05, q95) if a is not None and b is not None)
    check(f"{u['code']} q95 ≥ q05", ok_band)
    check(f"{u['code']} 有行为样本", (u.get("behavioral_count") or 0) >= 10,
          f"(count={u.get('behavioral_count')}, NSE_best={u.get('nse_best')}, 回退={u.get('fallback')})")
    check(f"{u['code']} 最优样本 NSE ≥ 0.9（演示数据可回收）", (u.get("nse_best") or 0) >= 0.9,
          f"(NSE_best={u.get('nse_best')})")
    cov = u.get("cover_90")
    check(f"{u['code']} 90% 区间覆盖率合理（30%~100%）", cov is not None and 0.3 <= cov <= 1.0,
          f"(cover={cov})")

# 参数范围应落在率定区间内
rows = r.get("param_ranges") or []
check("参数范围表非空", len(rows) > 0, f"({len(rows)} 行)")
bad = [x for x in rows if x["min"] < x["range_lo"] - 1e-6 or x["max"] > x["range_hi"] + 1e-6]
check("行为参数范围不越率定区间", not bad, f"(越界 {len(bad)} 行)")
if rows:
    x = rows[0]
    check("范围行含率定区间对照", all(k in x for k in ("min", "max", "range_lo", "range_hi")))

print("\n② 边界")
s2, _ = call("POST", f"/api/projects/{PID}/calibration/glue", {"n_samples": 10})
check("n_samples=10 被钳制到 50", s2 == 200 and _ .get("n_samples") == 50, f"(实际 {s2})")

print("\n③ 模拟回归")
s3, r3 = call("POST", f"/api/projects/{PID}/calibration/simulate", {})
check("simulate 不受影响", s3 == 200 and not r3.get("forecast"), f"(实际 {s3})")

print("\n" + "=" * 76)
print(f"PASS {sum(OK)}/{len(OK)}  FAIL {len(OK) - sum(OK)}")
sys.exit(0 if all(OK) else 1)
