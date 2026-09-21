# -*- coding: utf-8 -*-
"""P3 接口级验收：启动率定 → 轮询进度 → 终止/结果 → 采纳参数 → 复核模拟。"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8013"
PID = sys.argv[1] if len(sys.argv) > 1 else "p_dee39fceeab6"
MAX_EVALS = int(sys.argv[2]) if len(sys.argv) > 2 else 800
OK = []


def call(method, path, body=None, timeout=120):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"}, method=method)
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
print("① 启动率定任务")
st, r = call("POST", f"/api/projects/{PID}/calibration/run",
             {"max_evals": MAX_EVALS, "seed": 7, "split": 0.7})
check("HTTP 200", st == 200, f"(实际 {st})")
if st != 200:
    print("   ", r)
    sys.exit(1)
rid = r["run_id"]
print(f"   run_id={rid}  max_evals={r['config']['max_evals']}")

print("\n② 轮询进度")
t0 = time.time()
last = None
while True:
    s, p = call("GET", f"/api/projects/{PID}/calibration/runs/{rid}/status")
    if s != 200:
        print("   状态接口异常", s, p)
        break
    if p.get("phase") == "finished" or p.get("status") in ("done", "stopped", "failed"):
        print(f"   结束：status={p.get('status')} phase={p.get('phase')} elapsed={p.get('elapsed_s')}s err={p.get('error')}")
        break
    line = f"   {p.get('unit')} gen={p.get('gen')} evals={p.get('evals')} best_f={p.get('best_f')}"
    if line != last:
        print(line)
        last = line
    if time.time() - t0 > 300:
        print("   超时")
        break
    time.sleep(1.0)

check("任务完成", p.get("phase") == "finished" and p.get("status") in ("done", "stopped"),
      f"(实际 status={p.get('status')} phase={p.get('phase')})")
check("无错误", not p.get("error"), f"({p.get('error')})")

print("\n③ 结果")
s, res = call("GET", f"/api/projects/{PID}/calibration/runs/{rid}/result")
check("HTTP 200", s == 200, f"(实际 {s})")
if s != 200:
    print("   ", res)
    sys.exit(1)
for u in res["units"]:
    print(f"   {u['code']} calibrated={u['calibrated']} borrowed={u['borrowed_from']} "
          f"evals={u['evals']} gens={u['gens']} obj={u.get('objective')} {u['elapsed_s']}s")
print("   真值对比:")
for c in res.get("truth_compare") or []:
    sens = [f"{x['key']}{x['rel_pct']:+.1f}%" for x in c["rows"] if x["rel_pct"] is not None and abs(x["rel_pct"]) < 6]
    print(f"     {c['code']} 高敏参数回收(<6%): {len(sens)} 个 → {' '.join(sens)}")
print("   分期指标:")
worst_c = worst_v = 9
for m in res["metrics"]:
    c, v = m.get("calib") or {}, m.get("valid") or {}
    worst_c = min(worst_c, c.get("nse") or -9)
    worst_v = min(worst_v, v.get("nse") or -9)
    print(f"     {m['code']} 率定期 NSE={c.get('nse'):.4f}  验证期 NSE={v.get('nse'):.4f}")
check("率定期最差 NSE > 0.9", worst_c > 0.9, f"({worst_c:.4f})")
check("验证期最差 NSE > 0.9", worst_v > 0.9, f"({worst_v:.4f})")
sim = res.get("simulation") or {}
check("含权威复算过程线", bool(sim.get("units")) and bool((sim["units"][0].get("series") or {}).get("time")))
wb_ok = all(abs(w.get("closure_rate") or 0) < 1e-9 for w in sim.get("water_balance") or [])
check("复算水量平衡闭合", wb_ok)

print("\n④ 历次任务列表")
s, lst = call("GET", f"/api/projects/{PID}/calibration/runs")
check("HTTP 200", s == 200)
runs = lst.get("runs") or []
check("包含本次任务", any(x["run_id"] == rid for x in runs), f"(共 {len(runs)} 条)")

print("\n⑤ 采纳参数（run_id）→ 复核模拟")
s, a = call("POST", f"/api/projects/{PID}/calibration/apply", {"run_id": rid})
check("HTTP 200", s == 200, f"(实际 {s})")
if s == 200:
    saved = {u["code"]: u["params"] for u in a.get("units") or []}
    print("   已保存参数:", {k: {kk: round(vv, 3) for kk, vv in v.items() if kk in ("K", "B", "XE")} for k, v in saved.items()})
    check("与结果一致", abs(saved.get("S01", {}).get("K", 0) - res["final_params"]["S01"]["K"]) < 1e-9)
s, pr = call("GET", f"/api/projects/{PID}/calibration/params")
check("has_saved=True", pr.get("has_saved") is True)
s, sm = call("POST", f"/api/projects/{PID}/calibration/simulate", {})
nses = [round((u.get("metrics") or {}).get("nse") or -9, 4) for u in sm.get("units") or []]
print(f"   采纳后默认参数模拟 NSE = {nses}")
check("采纳后 NSE 全部 > 0.9", all(x > 0.9 for x in nses))

print("\n⑥ 恢复默认参数（清空参数集）")
s, rr = call("POST", f"/api/projects/{PID}/calibration/apply", {"reset": True})
check("HTTP 200", s == 200)
s, pr2 = call("GET", f"/api/projects/{PID}/calibration/params")
check("has_saved=False", pr2.get("has_saved") is False)

print("\n" + "=" * 76)
print(f"结果: {sum(OK)}/{len(OK)} 通过")
