"""模型模拟与率定接口。

- P2：参数集查询/保存、全流域模拟（simulate）
- P3：SCE-UA 链式率定（后台线程 + 进度落盘 + 可终止 + 结果落盘 + 参数应用）
"""
from __future__ import annotations

import json
import threading
import time
import urllib.parse
import uuid

from fastapi import APIRouter, HTTPException

from .. import storage as st
from ..core.model.calibrate import run_calibration
from ..core.model.params import FIXED_SPEC, PARAM_KEYS, PARAM_SPEC, default_params
from ..core.model.simulate import simulate_basin
from ..core.timeseries import summarize
from .deps import get_project_or_404, series_loader

router = APIRouter(prefix="/api/projects", tags=["calibration"])

# 内存态：rid -> {thread, stop(Event), pid}；进度与结果均落盘，重启后可读不可续
_RUNS: dict[str, dict] = {}
_RUNS_LOCK = threading.Lock()


def _require_subbasins(pid: str) -> dict:
    sub = st.read_subbasins(pid)
    if not sub or not sub.get("subbasins"):
        raise HTTPException(400, "请先划分子流域（模拟与率定按预报单元组织）")
    return sub


def _default_set_path(pid: str):
    return st.project_dir(pid) / "calibration" / "default.json"


def _read_default_params(pid: str) -> dict | None:
    p = _default_set_path(pid)
    if not p.exists():
        return None
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return doc.get("params") or None


def _unit_rows(pid: str, sub: dict):
    """每个单元的默认参数（叠加已保存的 default.json）。"""
    saved = _read_default_params(pid) or {}
    rows = []
    for sb in sorted(sub.get("subbasins", []), key=lambda s: (s.get("order_index") or 0)):
        code = sb["code"]
        prm = default_params(float(sb.get("river_length_km") or 0.0))
        prm.update({k: v for k, v in (saved.get(code) or {}).items() if k in PARAM_KEYS})
        rows.append(
            {
                "code": code,
                "name": sb.get("name"),
                "area_km2": sb.get("area_km2"),
                "order_index": sb.get("order_index"),
                "outlet_station": (sb.get("outlet_station") or {}).get("name"),
                "river_length_km": sb.get("river_length_km"),
                "params": prm,
            }
        )
    return rows


# ---------------------------------------------------------------- 参数集
@router.get("/{pid}/calibration/params")
def get_params(pid: str):
    """每单元的当前参数（default.json 覆盖默认值）+ 参数规范（供前端表单）。"""
    get_project_or_404(pid)
    sub = _require_subbasins(pid)
    return {
        "ok": True,
        "spec": PARAM_SPEC,
        "fixed": FIXED_SPEC,
        "units": _unit_rows(pid, sub),
        "has_saved": bool(_read_default_params(pid)),
    }


@router.post("/{pid}/calibration/apply")
def apply_params(pid: str, payload: dict):
    """保存参数集为项目默认。

    body 三选一：
      {params: {code: {...}}}      保存指定参数
      {reset: true}                恢复默认参数
      {run_id: "..."}              采纳某次率定任务的最终参数
    """
    get_project_or_404(pid)
    sub = _require_subbasins(pid)
    path = _default_set_path(pid)
    if payload.get("reset"):
        # 逻辑重置优先：把参数集写成空（不依赖删除文件，Windows 下文件可能被占用）
        if not st.remove_file(path) and path.exists():
            path.write_text(
                json.dumps({"saved_at": st.now_iso(), "params": {}}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        st.touch_project(pid)
        return {"ok": True, "reset": True, "units": _unit_rows(pid, sub)}

    params_in = payload.get("params")
    if payload.get("run_id"):
        rid = str(payload["run_id"])
        doc = st.read_json_file(st.cal_run_dir(pid, rid) / "result.json")
        if not doc:
            raise HTTPException(404, f"任务 {rid} 的结果尚未生成")
        params_in = doc.get("final_params") or {}
    if not isinstance(params_in, dict):
        raise HTTPException(400, "params 需为 {单元code: {参数}} 结构")
    clean = {}
    for sb in sub.get("subbasins", []):
        code = sb["code"]
        if code in params_in:
            clean[code] = {
                k: float(params_in[code].get(k, PARAM_SPEC[k]["default"])) for k in PARAM_KEYS
            }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"saved_at": st.now_iso(), "params": clean}, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    st.touch_project(pid)
    return {"ok": True, "saved": list(clean.keys()), "units": _unit_rows(pid, sub)}


# ---------------------------------------------------------------- 模拟
@router.post("/{pid}/calibration/simulate")
def run_simulate(pid: str, payload: dict | None = None):
    """用给定（或缺省）参数做全流域模拟，返回过程线 / 指标 / 水量平衡。

    body: {params?: {code:{...}}, period?: {start,end,warmup_days}}
    """
    get_project_or_404(pid)
    sub = _require_subbasins(pid)
    manifest = st.read_ts_manifest(pid)
    payload = payload or {}
    saved = _read_default_params(pid) or {}
    merged = dict(saved)
    for code, prm in (payload.get("params") or {}).items():
        merged.setdefault(code, {}).update(prm or {})
    res = simulate_basin(
        sub,
        manifest,
        series_loader(pid),
        params=merged,
        period=payload.get("period"),
    )
    if not res.get("ok"):
        raise HTTPException(400, res.get("error") or "模拟失败")
    return res


@router.get("/{pid}/calibration/simulate")
def run_simulate_get(pid: str, start: str = "", end: str = "", warmup_days: int = 0):
    """GET 版本（便于浏览器直接访问 /docs 调试），参数用当前 default.json。"""
    period = {}
    if start:
        period["start"] = urllib.parse.unquote(start)
    if end:
        period["end"] = urllib.parse.unquote(end)
    if warmup_days:
        period["warmup_days"] = warmup_days
    return run_simulate(pid, {"period": period} if period else {})


# ---------------------------------------------------------------- 率定任务（P3）
def _progress_path(pid: str, rid: str):
    return st.cal_run_dir(pid, rid) / "progress.json"


def _write_progress(pid: str, rid: str, doc: dict) -> None:
    st.write_json_file(_progress_path(pid, rid), doc)


def _read_progress(pid: str, rid: str) -> dict | None:
    return st.read_json_file(_progress_path(pid, rid))


def _worker(pid: str, rid: str, cfg: dict, sub: dict) -> None:
    """率定线程：进度逐代落盘，结果写 result.json。"""
    entry = _RUNS.get(rid) or {}
    stop_event = entry.get("stop")
    started = time.time()
    prog = {
        "run_id": rid, "pid": pid, "status": "running", "phase": "prepare",
        "unit": None, "units_done": [], "gen": 0, "evals": 0, "best_f": None,
        "best": None, "started_at": st.now_iso(), "elapsed_s": 0.0, "error": None,
    }

    def on_progress(payload: dict):
        prog.update(payload)
        prog["elapsed_s"] = round(time.time() - started, 1)
        if payload.get("phase") == "done" and payload.get("unit"):
            done = list(prog.get("units_done") or [])
            if payload["unit"] not in done:
                done.append(payload["unit"])
            prog["units_done"] = done
        _write_progress(pid, rid, prog)

    def stop_flag():
        return bool(stop_event and stop_event.is_set())

    try:
        res = run_calibration(
            sub, st.read_ts_manifest(pid), series_loader(pid),
            config=cfg, on_progress=on_progress, stop_flag=stop_flag,
        )
        elapsed = round(time.time() - started, 1)
        if not res.get("ok"):
            stat = res.get("status") or "failed"
            prog.update({"status": stat if stat in ("stopped", "failed") else "failed",
                         "phase": "finished" if stat == "stopped" else "failed",
                         "error": res.get("error") if stat != "stopped" else None,
                         "finished_at": st.now_iso(), "elapsed_s": elapsed})
            _write_progress(pid, rid, prog)
            return

        # 真值对比（演示数据才有；便于验证率定是否回收真值）
        truth = st.read_json_file(st.project_dir(pid) / "calibration" / "demo_truth.json") or {}
        tparams = truth.get("params") or {}
        compare = []
        if tparams:
            for u in res.get("units") or []:
                if not u.get("calibrated"):
                    continue
                compare.append(
                    {
                        "code": u["code"],
                        "rows": [
                            {"key": k, "truth": float(tv), "calib": round(float(u["params"].get(k, 0)), 4),
                             "rel_pct": round((float(u["params"].get(k, 0)) - float(tv)) / abs(float(tv)) * 100, 1)
                             if float(tv) else None}
                            for k, tv in tparams.items()
                        ],
                    }
                )

        result = {
            "ok": True, "run_id": rid, "pid": pid, "finished_at": st.now_iso(),
            "status": res.get("status") or "done",
            "stopped": res.get("status") == "stopped",
            "elapsed_s": elapsed, "config": cfg,
            "units": res.get("units") or [],
            "metrics": res.get("metrics") or [],
            "split": res.get("split"),
            "final_params": res.get("final_params") or {},
            "simulation": res.get("simulation") or {},
            "truth_compare": compare,
            "warnings": res.get("warnings") or [],
        }
        st.write_json_file(st.cal_run_dir(pid, rid) / "result.json", result)
        prog.update({"status": "stopped" if res.get("status") == "stopped" else "done",
                     "phase": "finished", "finished_at": st.now_iso(),
                     "elapsed_s": elapsed, "result_ready": True, "error": None})
        _write_progress(pid, rid, prog)
        st.touch_project(pid)
    except Exception as e:  # noqa: BLE001 —— 后台线程兜底，错误必须可见
        import traceback

        prog.update({"status": "failed", "phase": "failed",
                     "error": f"{type(e).__name__}: {e}",
                     "traceback": traceback.format_exc()[-1500:],
                     "finished_at": st.now_iso(),
                     "elapsed_s": round(time.time() - started, 1)})
        _write_progress(pid, rid, prog)


def _check_runnable(pid: str) -> dict:
    """率定前置条件：划分 + 时序数据 + 至少一个单元有出口实测。"""
    sub = _require_subbasins(pid)
    manifest = st.read_ts_manifest(pid)
    s = summarize(manifest)
    counts = s.get("counts") or {}
    if not counts.get("rain") or not counts.get("evap"):
        raise HTTPException(400, "缺少降雨/蒸发时序数据：请先在「时序数据」中导入或生成演示数据")
    if not counts.get("flow"):
        raise HTTPException(400, "缺少流量时序数据：没有可率定的目标，请先导入各站实测流量")
    return sub


@router.post("/{pid}/calibration/run")
def start_run(pid: str, payload: dict | None = None):
    """启动 SCE-UA 链式率定（后台线程），返回 run_id。

    body: {period{start,end,warmup_days}, split, max_evals, seed, tol,
           lock: {code:{参数:值}}}
    """
    get_project_or_404(pid)
    sub = _check_runnable(pid)
    with _RUNS_LOCK:
        for rid, e in _RUNS.items():
            if e.get("pid") == pid and e.get("thread") and e["thread"].is_alive():
                raise HTTPException(409, f"该项目已有率定任务在运行（{rid}），请先等待完成或终止")
    payload = payload or {}
    cfg = {
        "period": payload.get("period") or None,
        "split": payload.get("split") or 0.7,
        "max_evals": int(payload.get("max_evals") or 3000),
        "seed": int(payload.get("seed") or 0),
        "tol": payload.get("tol") or 1e-4,
        "lock": payload.get("lock") or {},
    }
    rid = uuid.uuid4().hex[:10]
    st.write_json_file(
        st.cal_run_dir(pid, rid) / "config.json",
        {**cfg, "units": [sb["code"] for sb in sub.get("subbasins", [])], "created_at": st.now_iso()},
    )
    _write_progress(pid, rid, {"run_id": rid, "pid": pid, "status": "running", "phase": "queued",
                               "config": cfg, "started_at": st.now_iso()})
    stop_event = threading.Event()
    th = threading.Thread(target=_worker, args=(pid, rid, cfg, sub), daemon=True, name=f"cal-{rid}")
    with _RUNS_LOCK:
        _RUNS[rid] = {"pid": pid, "thread": th, "stop": stop_event}
    th.start()
    return {"ok": True, "run_id": rid, "config": cfg}


@router.get("/{pid}/calibration/runs")
def list_runs(pid: str):
    """历次率定任务（新→旧）。"""
    get_project_or_404(pid)
    rdir = st.cal_runs_dir(pid)
    out = []
    if rdir.exists():
        for d in sorted(rdir.iterdir(), reverse=True):
            if not d.is_dir():
                continue
            prog = st.read_json_file(d / "progress.json") or {}
            cfg = st.read_json_file(d / "config.json") or {}
            has_result = (d / "result.json").exists()
            out.append(
                {
                    "run_id": prog.get("run_id") or d.name,
                    "status": prog.get("status"),
                    "phase": prog.get("phase"),
                    "unit": prog.get("unit"),
                    "units_done": prog.get("units_done") or [],
                    "gen": prog.get("gen"),
                    "evals": prog.get("evals"),
                    "best_f": prog.get("best_f"),
                    "elapsed_s": prog.get("elapsed_s"),
                    "started_at": prog.get("started_at"),
                    "finished_at": prog.get("finished_at"),
                    "error": prog.get("error"),
                    "config": cfg,
                    "has_result": has_result,
                }
            )
    return {"ok": True, "runs": out}


def _get_run_entry(pid: str, rid: str) -> dict:
    with _RUNS_LOCK:
        e = _RUNS.get(rid)
    if e and e.get("pid") != pid:
        raise HTTPException(404, f"任务不存在: {rid}")
    return e or {}


@router.get("/{pid}/calibration/runs/{rid}/status")
def run_status(pid: str, rid: str):
    """进度与当前最优目标值（前端轮询）。"""
    get_project_or_404(pid)
    prog = _read_progress(pid, rid)
    if not prog:
        raise HTTPException(404, f"任务不存在: {rid}")
    out = dict(prog)
    entry = _get_run_entry(pid, rid)
    alive = bool(entry.get("thread") and entry["thread"].is_alive())
    out["alive"] = alive
    if prog.get("status") == "running" and not alive:
        # 服务重启等导致任务丢失：如实标注，避免前端无限轮询
        out["status"] = "failed"
        out["error"] = out.get("error") or "任务进程已丢失（服务重启或异常退出）"
    if (d := st.cal_run_dir(pid, rid)) and (d / "result.json").exists():
        out["result_ready"] = True
    return out


@router.post("/{pid}/calibration/runs/{rid}/stop")
def stop_run(pid: str, rid: str):
    """终止任务（当前代结束后退出，已完成单元的参数保留）。"""
    get_project_or_404(pid)
    entry = _get_run_entry(pid, rid)
    prog = _read_progress(pid, rid)
    alive = bool(entry.get("thread") and entry["thread"].is_alive())
    if not alive:
        if not prog:
            raise HTTPException(404, f"任务不存在: {rid}")
        if prog.get("phase") in ("finished", "failed"):
            raise HTTPException(409, f"任务已结束（{prog.get('status')}），无需终止")
        return {"ok": True, "stopped": False, "note": "任务已不在运行", "status": prog.get("status")}
    entry["stop"].set()
    return {"ok": True, "stopped": True, "note": "已请求终止，等待当前代结束"}


@router.get("/{pid}/calibration/runs/{rid}/result")
def run_result(pid: str, rid: str):
    """率定结果：参数表 + 分期指标 + 收敛过程 + 权威复算（过程线/水量平衡）。"""
    get_project_or_404(pid)
    doc = st.read_json_file(st.cal_run_dir(pid, rid) / "result.json")
    if not doc:
        prog = _read_progress(pid, rid) or {}
        raise HTTPException(409, f"结果尚未生成（任务状态：{prog.get('status')}）")
    return doc


@router.get("/{pid}/calibration/runs/{rid}/convergence")
def run_convergence(pid: str, rid: str):
    """轻量接口：只返回收敛曲线（供进度页画图，不拖全量结果）。"""
    get_project_or_404(pid)
    doc = st.read_json_file(st.cal_run_dir(pid, rid) / "result.json")
    if doc:
        return {"ok": True,
                "curve": [{"code": u["code"], "points": u.get("convergence") or []} for u in doc.get("units") or []]}
    prog = _read_progress(pid, rid) or {}
    raise HTTPException(409, f"结果尚未生成（任务状态：{prog.get('status')}）")
