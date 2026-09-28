# -*- coding: utf-8 -*-
"""P12 验收：多模型对比任务（后端接口）。

用法：PYTHONPATH=. python scripts/_test_p12_compare.py [PID]

覆盖：
  ① 入参校验/错误分支（模型不足、项目不存在、任务不存在）
  ② 完整对比运行（2 模型）——进度、结果结构、参数隔离、历次列表、CSV 导出
  ③ 并发保护与终止语义（对比×对比、对比×率定、运行中导出、stop、重复 stop）
"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8013"
PID = sys.argv[1] if len(sys.argv) > 1 else "p_dee39fceeab6"
OK = []


def call(method, path, body=None, timeout=900):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw)
            except Exception:
                return r.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw[:300]


def raw_get(path, timeout=300):
    try:
        with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]


def check(name, cond, detail=""):
    OK.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name} {detail}")


def poll(cid, want=("done", "stopped", "failed"), timeout=600):
    """轮询直到终态，返回最终 doc。"""
    t0 = time.time()
    last = {}
    while time.time() - t0 < timeout:
        s, d = call("GET", f"/api/projects/{PID}/calibration/compare/{cid}")
        if s != 200:
            return d if isinstance(d, dict) else {"status": f"HTTP {s}"}
        last = d
        if d.get("status") in want:
            return d
        time.sleep(0.6)
    return last


print("=" * 76)
print("① 入参校验与错误分支")

s, r = call("POST", f"/api/projects/{PID}/calibration/compare", {"models": ["xaj"]})
check("单模型被拒（400）", s == 400, f"(实际 {s}: {str(r)[:70]})")

s, r = call("POST", f"/api/projects/{PID}/calibration/compare", {"models": ["nope", "xaj"]})
check("有效模型不足被拒（400）", s == 400, f"(实际 {s})")

s, r = call("POST", "/api/projects/p_not_exist/calibration/compare", {"models": ["xaj", "tank"]})
check("项目不存在（404）", s == 404, f"(实际 {s})")

s, r = call("GET", f"/api/projects/{PID}/calibration/compare/nope123456")
check("任务不存在（404）", s == 404, f"(实际 {s})")

print("\n" + "=" * 76)
print("② 完整对比运行（xaj vs tank, max_evals=120）")
t0 = time.time()
s, r = call("POST", f"/api/projects/{PID}/calibration/compare",
            {"models": ["xaj", "tank"], "max_evals": 120, "split": 0.7, "seed": 3})
check("启动 200", s == 200, f"(实际 {s})")
if s != 200:
    print("   ", r)
    sys.exit(1)
cid = r["cid"]
cfg = r.get("config") or {}
check("返回 cid", bool(cid), f"({cid})")
check("config 回显 models/max_evals",
      cfg.get("models") == ["xaj", "tank"] and cfg.get("max_evals") == 120,
      f"({cfg.get('models')}, {cfg.get('max_evals')})")

s, d = call("GET", f"/api/projects/{PID}/calibration/compare/{cid}")
check("查询 200", s == 200, f"(实际 {s})")
check("含两个模型条目且带名称",
      set((d.get("models") or {}).keys()) == {"xaj", "tank"}
      and all(m.get("name") for m in (d.get("models") or {}).values()))
check("初始状态合法", d.get("status") in ("queued", "running", "done"))

doc = poll(cid)
check("任务到达 done", doc.get("status") == "done", f"(实际 {doc.get('status')}, {time.time() - t0:.1f}s)")
mds = doc.get("models") or {}
check("两模型均 done", all(mds.get(k, {}).get("status") == "done" for k in ("xaj", "tank")),
      f"({[mds.get(k, {}).get('status') for k in ('xaj', 'tank')]})")
check("elapsed_s 已记录", (doc.get("elapsed_s") or 0) > 0, f"({doc.get('elapsed_s')}s)")

for mk in ("xaj", "tank"):
    m = mds.get(mk) or {}
    ms = m.get("metrics") or []
    check(f"[{mk}] 逐单元指标非空", len(ms) >= 1, f"({len(ms)} 单元)")
    check(f"[{mk}] 每单元含 calib/valid 且 nse 有效",
          all((x.get("calib") or {}).get("nse") is not None
              and (x.get("valid") or {}).get("nse") is not None for x in ms))
    lo, hi = m.get("calib_nse_range") or [None, None]
    check(f"[{mk}] 率定 NSE 区间合理", lo is not None and hi is not None and -1 <= lo <= hi <= 1,
          f"({lo}~{hi})")
    lo2, hi2 = m.get("valid_nse_range") or [None, None]
    check(f"[{mk}] 验证 NSE 区间存在", lo2 is not None and hi2 is not None, f"({lo2}~{hi2})")
    check(f"[{mk}] 单模型耗时>0 且 progress 已清空",
          (m.get("elapsed_s") or 0) > 0 and m.get("progress") is None,
          f"({m.get('elapsed_s')}s)")
    check(f"[{mk}] 无错误字段", not m.get("error"), f"({m.get('error')})")
    codes = {x.get("code") for x in ms}
    check(f"[{mk}] 单元 code 唯一且非空", len(codes) == len(ms) and all(codes))
    # 形态学指标：洪峰偏差（%）与峰现偏移（时段）
    check(f"[{mk}] 逐单元补出 peak_bias_pct",
          all("peak_bias_pct" in (x.get("calib") or {}) and "peak_bias_pct" in (x.get("valid") or {})
              for x in ms))
    check(f"[{mk}] peak_error 为多场洪峰口径",
          all(isinstance(((x.get("calib") or {}).get("peak_error") or {}).get("events"), list)
              for x in ms))
    ps = m.get("peak_summary") or {}
    check(f"[{mk}] peak_summary 含 calib/valid 两口径",
          set(ps.keys()) >= {"calib", "valid"}
          and ps["calib"].get("peak_bias_absmax") is not None
          and ps["calib"].get("peak_shift_absmax") is not None,
          f"(率定 {ps.get('calib')})")
    check(f"[{mk}] 洪峰偏差为绝对值且非负", (ps.get("calib") or {}).get("peak_bias_absmax", -1) >= 0,
          f"({(ps.get('calib') or {}).get('peak_bias_absmax')}%)")

# 参数隔离：final_params 形如 {单元code: {参数名: 值}}
# 注册表 params=自由参数；KE/XE 是河道汇流参数（单元有来流才出现，各模型共用）；fixed 为结构常量不参与率定
ROUTING = {"KE", "XE"}
s, mi = call("GET", f"/api/projects/{PID}/calibration/models")
free = {m["key"]: {p["key"] for p in (m.get("params") or [])} for m in (mi.get("models") or [])}
names_of = {}
for mk in ("xaj", "tank"):
    fp = (mds.get(mk) or {}).get("final_params") or {}
    names = set().union(*[set(v.keys()) for v in fp.values()]) if fp else set()
    names_of[mk] = names
    check(f"[{mk}] 自由参数全部进入最终参数集", free.get(mk, set()) <= names,
          f"(缺 {sorted(free.get(mk, set()) - names)})")
    check(f"[{mk}] 无越界参数名（仅路由参数 KE/XE 可共享）",
          names <= free.get(mk, set()) | ROUTING, f"(越界 {sorted(names - free.get(mk, set()) - ROUTING)})")
check("参数体系按模型隔离（参数集不同）", names_of["xaj"] != names_of["tank"],
      f"(xaj {len(names_of['xaj'])} 个 / tank {len(names_of['tank'])} 个, 共享 {sorted(names_of['xaj'] & names_of['tank'])})")

s, r = call("GET", f"/api/projects/{PID}/calibration/compare")
check("历次列表 200", s == 200, f"(实际 {s})")
runs = (r.get("runs") or []) if isinstance(r, dict) else []
hit = [x for x in runs if x.get("cid") == cid]
check("列表中可查到本次任务", len(hit) == 1, f"({len(runs)} 条历史)")
check("列表条目含模型状态映射",
      bool(hit) and set((hit[0].get("models") or {}).keys()) == {"xaj", "tank"})

s, body = raw_get(f"/api/projects/{PID}/calibration/compare/{cid}/export?what=metrics")
lines = [ln for ln in (body or "").splitlines() if ln.strip()]
head = lines[0].lstrip("\ufeff") if lines else ""  # 后端带 BOM 便于 Excel 直开
check("CSV 导出 200 且表头正确", s == 200 and head == "model,unit,split,metric,value",
      f"(实际 {s}, 表头 {head[:40]!r})")
check("CSV 含两模型数据行", len(lines) > 5 and any(ln.startswith("xaj,") for ln in lines)
      and any(ln.startswith("tank,") for ln in lines), f"({len(lines)} 行)")
check("CSV 含形态学指标行",
      any(",peak_bias_pct," in ln for ln in lines) and any(",peak_shift_steps," in ln for ln in lines),
      f"(peak 行 {sum(1 for ln in lines if ',peak_' in ln)})")

s, r = call("POST", f"/api/projects/{PID}/calibration/compare/{cid}/stop")
check("已结束任务 stop 返回 409", s == 409, f"(实际 {s})")

s, r = raw_get(f"/api/projects/{PID}/calibration/compare/{cid}/export?what=bogus")
check("非法导出类型被拒（400）", s == 400, f"(实际 {s})")

print("\n" + "=" * 76)
print("③ 并发保护与终止语义（gr4j vs hbv, max_evals=900）")
s, r = call("POST", f"/api/projects/{PID}/calibration/compare",
            {"models": ["gr4j", "hbv"], "max_evals": 900})
check("启动长任务 200", s == 200, f"(实际 {s})")
cid2 = (r or {}).get("cid")

s, r = call("POST", f"/api/projects/{PID}/calibration/compare", {"models": ["xaj", "tank"]})
check("对比×对比 并发被拒（409）", s == 409, f"(实际 {s})")

s, r = call("POST", f"/api/projects/{PID}/calibration/run", {"mode": "chain", "max_evals": 100})
check("对比运行中启动率定被拒（409）", s == 409, f"(实际 {s})")

s, body = raw_get(f"/api/projects/{PID}/calibration/compare/{cid2}/export?what=metrics")
check("运行中导出被拒（409）", s == 409, f"(实际 {s})")

s, r = call("POST", f"/api/projects/{PID}/calibration/compare/{cid2}/stop")
check("stop 返回 200", s == 200 and (r or {}).get("stopping"), f"(实际 {s})")

doc2 = poll(cid2, want=("stopped", "done", "failed"), timeout=300)
check("终止后到达 stopped", doc2.get("status") == "stopped", f"(实际 {doc2.get('status')})")
st2 = [(k, (doc2.get("models") or {}).get(k, {}).get("status")) for k in ("gr4j", "hbv")]
check("未跑单元标记 not_run", any(x[1] == "not_run" for x in st2), f"({st2})")
check("用户终止不记为失败", all(x[1] in ("stopped", "done", "not_run") for x in st2), f"({st2})")

s, r = call("POST", f"/api/projects/{PID}/calibration/compare/{cid2}/stop")
check("重复 stop 返回 409", s == 409, f"(实际 {s})")

print("\n" + "=" * 76)
n, tot = sum(OK), len(OK)
print(f"P12 验收：{n}/{tot} PASS" + ("" if n == tot else f"  ❌ FAIL={tot - n}"))
sys.exit(0 if n == tot else 1)
