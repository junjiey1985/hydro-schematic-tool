# -*- coding: utf-8 -*-
"""P2 验收测试：演示数据（真值模型生成）+ 模拟引擎（指标 / 水量平衡）。"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8013"
PID = sys.argv[1] if len(sys.argv) > 1 else "p_dee39fceeab6"
OK = []


def call(method, path, body=None, timeout=600):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json"}, method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]


def check(name, cond, detail=""):
    OK.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name} {detail}")


def f(v, n=4):
    return "None" if v is None else (f"{v:.{n}f}" if isinstance(v, float) else str(v))


print("=" * 72)
print("① POST /calibration/apply {\"reset\": true} —— 参数集逻辑重置")
st, r = call("POST", f"/api/projects/{PID}/calibration/apply", {"reset": True})
check("HTTP 200", st == 200, f"(实际 {st})")
if st == 200:
    check("reset 生效", r.get("reset") is True and r.get("units"))
    st2, r2 = call("GET", f"/api/projects/{PID}/calibration/params")
    check("has_saved=False", r2.get("has_saved") is False, f"(实际 {r2.get('has_saved')})")
else:
    print("   ", r)

print("=" * 72)
print("② POST /timeseries/demo —— 合成数据（流量由真值模型跑出，OSSE）")
t0 = time.time()
st, r = call("POST", f"/api/projects/{PID}/timeseries/demo", {"days": 1095, "replace": True})
dt = time.time() - t0
check("HTTP 200", st == 200, f"(实际 {st}, {dt:.1f}s)")
if st != 200:
    print("   ", r)
    sys.exit(1)
check("flow_source=真值模型", r.get("flow_source") == "生成·真值模型", f"(实际 {r.get('flow_source')})")
for kind, items in (r.get("created") or {}).items():
    print(f"   {kind:5s}: {len(items)} 条  " + ", ".join(f"{i['name']}({i['count']})" for i in items[:6]))
print("   truth:", json.dumps(r.get("truth"), ensure_ascii=False))

print("=" * 72)
print("③ GET /timeseries/manifest —— 覆盖率")
st, man = call("GET", f"/api/projects/{PID}/timeseries/manifest")
cov = man.get("coverage") or {}
rows = cov.get("rows") or []
check("全部单元 status=ok", all(x.get("status") == "ok" for x in rows),
      f"({[x.get('status') for x in rows]})")
for x in rows:
    print(f"   {x['code']} 雨量覆盖={x['rain_coverage']} 出口站={x.get('outlet_station')} 流量={x.get('flow_available')}")
# 实测流量统计（判断合成流量量级是否合理）
st, ser = call("GET", f"/api/projects/{PID}/timeseries/flow/H1")
if st == 200:
    vals = [p[1] for p in ser["points"]]
    n = len(vals)
    mean = sum(vals) / n
    print(f"   H1(黄龙滩站) 实测: n={n} 均值={mean:.1f} m³/s 峰值={max(vals):.0f} m³/s "
          f"→ 年径流深≈{mean * 31.536 / 10668 * 1000:.0f} mm")

print("=" * 72)
print("④ POST /calibration/simulate —— 默认参数模拟")
t0 = time.time()
st, sim = call("POST", f"/api/projects/{PID}/calibration/simulate", {})
dt = time.time() - t0
check("HTTP 200", st == 200, f"(实际 {st}, {dt:.2f}s)")
if st != 200:
    print("   ", sim)
    sys.exit(1)
print(f"   时段: {sim['period']['start']} ~ {sim['period']['end']}  n={sim['n_steps']}")
if sim.get("warnings"):
    print("   警告:", sim["warnings"])
for wb in sim.get("water_balance") or []:
    print(f"   {wb['code']} 平衡: P={f(wb['sum_p'],1)} E={f(wb['sum_e'],1)} q={f(wb['sum_q'],1)} "
          f"ΔS={f(wb['storage_change'],1)} 闭合={wb['closure']} 率={wb['closure_rate']}")
    check(f"{wb['code']} 水量闭合≈0", abs(wb.get("closure_rate") or 0) < 1e-6, f"(率={wb['closure_rate']})")
for u in sim.get("units") or []:
    m = u.get("metrics") or {}
    pe = m.get("peak_error") or {}
    print(f"   {u['code']} NSE={f(m.get('nse'))} R²={f(m.get('r2'))} KGE={f(m.get('kge'))} "
          f"RMSE={f(m.get('rmse'),2)} PBIAS={f(m.get('pbias'),2)}% "
          f"峰差中位={pe.get('median_steps')}{pe.get('unit')}({pe.get('n_events')}场)")
    for e in (pe.get("events") or [])[:3]:
        print(f"        峰 {e.get('obs_time')} → 模拟 {e.get('sim_time')}  实测{e.get('obs_peak')} 模拟{e.get('sim_peak')}")

print("=" * 72)
print("⑤ 真值参数模拟 —— 应接近完美（验证模型与数据自洽 / 率定基准）")
truth_path = f"/api/projects/{PID}/calibration/simulate"
st, r = call("POST", truth_path, {})
st, man2 = call("GET", f"/api/projects/{PID}/timeseries/manifest")
# 读真值
import os
tp = os.path.join("data", "projects", PID, "calibration", "demo_truth.json")
truth = json.loads(open(tp, encoding="utf-8").read())
tparams = truth["params"]
st, sim2 = call("POST", truth_path, {"params": {u["code"]: dict(tparams) for u in sim["units"]}})
check("HTTP 200", st == 200, f"(实际 {st})")
if st == 200:
    for u in sim2.get("units") or []:
        m = u.get("metrics") or {}
        print(f"   {u['code']} NSE={f(m.get('nse'))} RMSE={f(m.get('rmse'),2)} PBIAS={f(m.get('pbias'),2)}%")
    best = min((u.get("metrics") or {}).get("nse") or -9 for u in sim2["units"])
    check("真值参数 NSE ≥ 0.9", best >= 0.9, f"(最差 {f(best)})")

print("=" * 72)
print(f"结果: {sum(OK)}/{len(OK)} 通过")
