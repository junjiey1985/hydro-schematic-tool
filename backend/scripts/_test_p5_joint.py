# -*- coding: utf-8 -*-
"""P5 验收：联合率定（多站加权 + 参数共享分组）+ 导出 CSV 回导。

用法：
    python _test_p5_joint.py [PID] [JOINT_MAX_EVALS]
前置：后端已启动（127.0.0.1:8013），项目已有划分 + 演示时序数据。
"""
import csv
import io
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8013"
PID = sys.argv[1] if len(sys.argv) > 1 else "p_dee39fceeab6"
JOINT_EVALS = int(sys.argv[2]) if len(sys.argv) > 2 else 2000
OK = []

sys.path.insert(0, ".")
from app.core.model.params import PARAM_KEYS as XAJ_PARAM_KEYS  # noqa: E402  （仅作回退）


def call(method, path, body=None, timeout=300, raw=False):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read().decode("utf-8")
            if raw:
                return r.status, payload, {k.lower(): v for k, v in r.headers.items()}
            return r.status, json.loads(payload)
    except urllib.error.HTTPError as e:
        payload = e.read().decode("utf-8", "replace")
        if raw:
            return e.code, payload, {k.lower(): v for k, v in e.headers.items()}
        try:
            return e.code, json.loads(payload)
        except Exception:
            return e.code, payload[:300]


def check(name, cond, detail=""):
    OK.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name} {detail}")


# ---- 当前项目的模型与参数体系（P10 起模型可插拔：不能硬编码新安江的参数名/个数）
_s, _pm = call("GET", f"/api/projects/{PID}/calibration/params")
MODEL = (_pm or {}).get("model") or "xaj"
SPEC_KEYS = list(((_pm or {}).get("spec") or {}).keys())
PARAM_KEYS = SPEC_KEYS or (list(XAJ_PARAM_KEYS) + ["XE"])
FREE_KEYS = [k for k in SPEC_KEYS if k != "XE"] or ["K"]
SHARE_KEY = FREE_KEYS[0]          # 共享分组用该模型的第一个产汇流参数
print(f"当前模型：{MODEL}｜自由参数：{FREE_KEYS}｜共享组键：{SHARE_KEY}")


def wait_run(pid, rid, label, max_s=600):
    t0 = time.time()
    last = None
    while True:
        s, p = call("GET", f"/api/projects/{pid}/calibration/runs/{rid}/status")
        if s != 200:
            print("   状态接口异常", s, p)
            return None
        if p.get("phase") in ("finished", "failed") or p.get("status") in ("done", "stopped", "failed"):
            print(f"   [{label}] 结束：status={p.get('status')} elapsed={p.get('elapsed_s')}s evals={p.get('evals')}")
            return p
        line = f"   [{label}] {p.get('unit')} gen={p.get('gen')} evals={p.get('evals')} best_f={p.get('best_f')}"
        if line != last:
            print(line)
            last = line
        if time.time() - t0 > max_s:
            print("   超时")
            return None
        time.sleep(1.5)


print("=" * 76)
print("⓪ 离线：_build_slots 决策变量表")
from app.core.model.calibrate import _build_slots, free_spec_for  # noqa: E402
from app.core.model.simulate import unit_context  # noqa: E402
from app import storage as st  # noqa: E402
from app.routers.deps import series_loader  # noqa: E402
sub = st.read_subbasins(PID)
sbs = sorted(sub["subbasins"], key=lambda s: (s.get("order_index") or 0))
codes = [s["code"] for s in sbs]
ctxs = {c: unit_context(sub, st.read_ts_manifest(PID), series_loader(PID), c, model=MODEL) for c in codes}
total_free = sum(len(free_spec_for(ctxs[c])[0]) for c in codes)
slots, meta = _build_slots(sbs, ctxs, {}, {SHARE_KEY: codes})
k_slots = [s for s in slots if s["key"] == SHARE_KEY]
check(f"共享 {SHARE_KEY} 合并为 1 个变量",
      len(k_slots) == 1 and k_slots[0]["shared"] and len(k_slots[0]["units"]) == 3,
      f"({SHARE_KEY} slots={len(k_slots)}, units={k_slots[0]['units'] if k_slots else '-'})")
check("维度 = Σ单元自由参数 − (组员−1)", len(slots) == total_free - 2,
      f"(slots={len(slots)}, Σfree={total_free})")
check("无 share 时退化为逐单元变量", len(_build_slots(sbs, ctxs, {}, {})[0]) == total_free)

# SCE-UA 一代的评估次数 = 复合体数 s × 每复合体点数 m = p(2p+1)（见 calibrate.sce_ua）。
# 联合模式维度 p 是"Σ单元自由参数 −(共享组员−1)"，HBV（15 参数/单元）下 p 可达 40+，
# 一代就要 4000+ 次评估；预算小于一代则 gens=0、收敛曲线为空（不是实现缺陷）。
_p_slots = len(slots)
_auto_evals = _p_slots * (2 * _p_slots + 1) + 150
if JOINT_EVALS < _auto_evals:
    print(f"  · 联合维度 p={_p_slots}：SCE-UA 每代需 p(2p+1)={_p_slots * (2 * _p_slots + 1)} 次评估，"
          f"自动把 max_evals 由 {JOINT_EVALS} 提升到 {_auto_evals}")
    JOINT_EVALS = _auto_evals

print("\n① 启动联合率定（%s 三单元共享，max_evals=%d）" % (SHARE_KEY, JOINT_EVALS))
st_, r = call("POST", f"/api/projects/{PID}/calibration/run",
              {"mode": "joint", "max_evals": JOINT_EVALS, "seed": 7, "split": 0.7,
               "share": {SHARE_KEY: codes}})
check("HTTP 200", st_ == 200, f"(实际 {st_})")
if st_ != 200:
    print("   ", r)
    sys.exit(1)
rid_j = r["run_id"]
check("config.mode=joint", r["config"].get("mode") == "joint")
print(f"   run_id={rid_j}")
prog = wait_run(PID, rid_j, "joint")
check("联合任务完成", bool(prog) and prog.get("status") == "done")

s, res = call("GET", f"/api/projects/{PID}/calibration/runs/{rid_j}/result")
check("result 可读", s == 200)
if s == 200:
    check("result.joint=True", res.get("joint") is True)
    check("mode=joint", res.get("mode") == "joint")
    check(f"slots 含共享组（{SHARE_KEY}）",
          any(sl["shared"] and sl["key"] == SHARE_KEY for sl in res.get("slots") or []))
    jmet = {m["code"]: m for m in res.get("metrics") or []}
    for c in codes:
        m = (jmet.get(c) or {}).get("calib") or {}
        check(f"{c} 联合率定期 NSE ≥ 0.9", (m.get("nse") or -1) >= 0.9, f"(NSE={m.get('nse')})")
    share_keys = {u["code"]: u.get("shared_keys") for u in res.get("units") or []}
    check(f"三单元 {SHARE_KEY} 共享同值",
          len({round((res.get("final_params") or {}).get(c, {}).get(SHARE_KEY, -1), 6)
               for c in codes}) == 1,
          f"({SHARE_KEY}={[round((res.get('final_params') or {}).get(c, {}).get(SHARE_KEY, -1), 4) for c in codes]})")

s, conv = call("GET", f"/api/projects/{PID}/calibration/runs/{rid_j}/convergence")
check("收敛曲线（联合）", s == 200 and conv.get("curve") and conv["curve"][0]["code"] == "JOINT"
      and len(conv["curve"][0]["points"]) > 0,
      f"(gens={len(conv['curve'][0]['points']) if conv.get('curve') else 0})")

print("\n② 链式模式回归（不受重构影响）")
st_, r = call("POST", f"/api/projects/{PID}/calibration/run",
              {"mode": "chain", "max_evals": 300, "seed": 7, "split": 0.7})
check("HTTP 200", st_ == 200, f"(实际 {st_})")
rid_c = r.get("run_id", "")
prog = wait_run(PID, rid_c, "chain")
check("链式任务完成", bool(prog) and prog.get("status") == "done")
s, res_c = call("GET", f"/api/projects/{PID}/calibration/runs/{rid_c}/result")
check("result.mode=chain", s == 200 and res_c.get("mode") == "chain")
if s == 200:
    jmet = {m["code"]: m for m in res_c.get("metrics") or []}
    nses = [(jmet.get(c) or {}).get("calib", {}).get("nse") for c in codes]
    # 注：原阈值为 0.95（新安江时代）。P10 起模型可插拔，HBV 雪版/GR4J 在同预算下
    # 拟合能力不同（见 docs/model-comparison.html），故改用模型无关下限 0.90，
    # 并在 detail 里打出模型名与实际值，便于人工判断。
    check("链式率定期 NSE ≥ 0.90（模型无关下限）", all((v or 0) >= 0.90 for v in nses),
          f"(模型 {MODEL}, NSE={[round(v, 4) if v else v for v in nses]})")

print("\n③ 导出 CSV + 回导 round-trip")
s, csv_text, headers = call("GET", f"/api/projects/{PID}/calibration/export?what=params&rid={rid_j}", raw=True)
check("参数表导出 200", s == 200, f"(len={len(csv_text)})")
check("Content-Disposition", "attachment" in (headers.get("content-disposition") or ""))
rows = list(csv.reader(io.StringIO(csv_text.lstrip("\ufeff"))))
check("参数表表头", rows[0] == ["unit_code", "param", "value"], f"({rows[0]})")
n_data = len(rows) - 1
check("参数表行数 = Σ单元参数数", n_data == sum(
    1 for c in codes for k in PARAM_KEYS if k in (res.get("final_params") or {}).get(c, {})),
    f"({n_data})")
# 回导：CSV → params → apply → 再导出比对
back = {}
for unit, k, v in rows[1:]:
    back.setdefault(unit, {})[k] = float(v)
s, r2 = call("POST", f"/api/projects/{PID}/calibration/apply", {"params": back})
check("参数表回导（apply）", s == 200, f"(实际 {s})")
s, csv_text2, _ = call("GET", f"/api/projects/{PID}/calibration/export?what=params", raw=True)
rows2 = list(csv.reader(io.StringIO(csv_text2.lstrip("\ufeff"))))
same = {(a[0], a[1]): round(float(a[2]), 5) for a in rows[1:]} == {
    (a[0], a[1]): round(float(a[2]), 5) for a in rows2[1:]}
check("导出 → 回导 → 再导出 逐值一致", same)

s, flow_text, headers = call("GET", f"/api/projects/{PID}/calibration/export?what=flow&rid={rid_j}", raw=True)
check("过程线导出 200", s == 200, f"(len={len(flow_text)})")
frows = list(csv.reader(io.StringIO(flow_text.lstrip("\ufeff"))))
expect_hdr = ["time"] + [x for c in codes for x in (f"{c}_obs", f"{c}_sim")]
check("过程线表头", frows[0] == expect_hdr, f"({frows[0][:5]}...)")
check("过程线行数 > 1000（全分辨率）", len(frows) - 1 > 1000, f"({len(frows)-1} 行)")
sim_col = frows[0].index(f"{codes[-1]}_sim")
vals = [float(r[sim_col]) for r in frows[1:] if r[sim_col]]
check("模拟流量列非全零", len(vals) > 0 and max(vals) > 0, f"(max={max(vals) if vals else 0:.1f})")

print("\n" + "=" * 76)
print(f"PASS {sum(OK)}/{len(OK)}  FAIL {len(OK) - sum(OK)}")
sys.exit(0 if all(OK) else 1)
