# -*- coding: utf-8 -*-
"""P6 验收：情景预报接口（设计雨型 / 逐日序列 / 参数与参数集 / 边界）。

用法：python _test_p6_forecast.py [PID]
前置：后端已启动（127.0.0.1:8013），项目已有划分 + 时序数据。
"""
import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8013"
PID = sys.argv[1] if len(sys.argv) > 1 else "p_dee39fceeab6"
OK = []


def call(method, path, body=None, timeout=180):
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
print("① 设计雨型预报（100mm/7天，峰位 0.4，预见期 30 天）")
s, r = call("POST", f"/api/projects/{PID}/calibration/forecast",
            {"horizon_days": 30, "rain": {"mode": "design", "total_mm": 100,
                                          "duration_days": 7, "peak_pos": 0.4}})
check("HTTP 200", s == 200, f"(实际 {s})")
if s != 200:
    print("   ", r)
    sys.exit(1)
fc = r.get("forecast") or {}
check("forecast 元信息", fc.get("days") == 30 and int(fc.get("n_obs") or 0) > 30,
      f"(days={fc.get('days')}, n_obs={fc.get('n_obs')})")
check("n_steps = n_obs + 30", r.get("n_steps") == (fc.get("n_obs") or 0) + 30,
      f"(n_steps={r.get('n_steps')})")
check("rain_scenario 描述", "设计雨型 100 mm / 7 天" in ((r.get("rain_scenario") or {}).get("desc") or ""))
vals = (r.get("rain_scenario") or {}).get("values") or []
check("雨型总量守恒", abs(sum(vals[:7]) - 100.0) < 0.5, f"(前7天合计={sum(vals[:7]):.1f})")
check("雨型峰值位置≈0.4", max(range(7), key=lambda i: vals[i]) in (2, 3),
      f"(峰在第 {max(range(7), key=lambda i: vals[i]) + 1} 天)")

codes = [u["code"] for u in r.get("units") or []]
for u in r.get("units") or []:
    f = u.get("forecast") or {}
    check(f"{u['code']} 预报段有洪峰", (f.get("peak_q") or 0) > 0,
          f"(peak={f.get('peak_q')} m³/s @ {f.get('peak_time')}, 径流深 {f.get('runoff_mm')} mm)")
    check(f"{u['code']} 预报洪峰在预见期内", f.get("peak_time") is not None and f["peak_time"] >= fc.get("start", ""))

# 预报应大于零雨情景：同参数跑 0 mm 情景，洪峰应明显更小
s2, r0 = call("POST", f"/api/projects/{PID}/calibration/forecast",
              {"horizon_days": 30, "rain": {"mode": "series", "values": [0.0] * 30}})
if s2 == 200:
    f100 = {u["code"]: u["forecast"]["peak_q"] for u in r["units"] if u.get("forecast")}
    f0 = {u["code"]: u["forecast"]["peak_q"] for u in r0["units"] if u.get("forecast")}
    check("降雨情景洪峰 > 无雨情景洪峰", all(f100[c] > f0[c] for c in codes if c in f0 and c in f100),
          f"({ {c: (round(f100[c],1), round(f0.get(c,0),1)) for c in codes} })")
else:
    check("无雨情景可运行", False, f"(实际 {s2})")

print("\n② 逐日序列预报 + 参数覆盖 + 蒸发假设")
s3, r3 = call("POST", f"/api/projects/{PID}/calibration/forecast",
              {"horizon_days": 10, "evap_mm": 2.0,
               "rain": {"mode": "series", "values": [50, 40, 30, 20, 10, 5, 0, 0, 0, 0]}})
check("HTTP 200", s3 == 200, f"(实际 {s3})")
if s3 == 200:
    check("10 天预见期", (r3.get("forecast") or {}).get("days") == 10)
    check("蒸发假设生效", any("2.0 mm/d" in w for w in r3.get("warnings") or []),
          f"(warnings={len(r3.get('warnings') or [])} 条)")

s4, r4 = call("POST", f"/api/projects/{PID}/calibration/forecast",
              {"horizon_days": 10, "rain": {"mode": "design", "total_mm": 80},
               "params": {codes[0]: {"K": 0.5}}})
check("参数覆盖可运行", s4 == 200, f"(实际 {s4})")

print("\n③ 边界与容错")
s5, _ = call("POST", f"/api/projects/{PID}/calibration/forecast",
             {"horizon_days": 10, "rain": {"mode": "design", "total_mm": 0}})
check("total_mm=0 返回 400", s5 == 400, f"(实际 {s5})")
s6, _ = call("POST", f"/api/projects/{PID}/calibration/forecast",
             {"horizon_days": 10, "rain": {"mode": "series", "values": []}})
check("空序列返回 400", s6 == 400, f"(实际 {s6})")
s7, r7 = call("POST", f"/api/projects/{PID}/calibration/forecast",
              {"horizon_days": 99999, "rain": {"mode": "design", "total_mm": 50}})
check("超长预见期被钳制", s7 == 200 and (r7.get("forecast") or {}).get("days") <= 3650,
      f"(实际 {s7}, days={(r7.get('forecast') or {}).get('days')})")

print("\n④ 模拟与率定回归（extend 默认不影响原路径）")
s8, r8 = call("POST", f"/api/projects/{PID}/calibration/simulate", {})
check("simulate 正常", s8 == 200 and not (r8.get("forecast")), f"(实际 {s8})")
check("n_steps 不变", s8 == 200 and r8.get("n_steps") == (fc.get("n_obs") or 0),
      f"(n_steps={r8.get('n_steps')})")

print("\n" + "=" * 76)
print(f"PASS {sum(OK)}/{len(OK)}  FAIL {len(OK) - sum(OK)}")
sys.exit(0 if all(OK) else 1)
