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

import numpy as np
from fastapi import APIRouter, HTTPException, Response

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


# ---------------------------------------------------------------- 情景预报（P6）
def _design_rain(total_mm: float, duration_days: int, peak_pos: float | None) -> list[float]:
    """设计雨型：三角权重（峰在 peak_pos×历时 处，谷底 0.05），按总量缩放。"""
    duration = max(1, int(duration_days))
    if duration == 1:
        return [round(float(total_mm), 3)]
    pk = min(max(float(peak_pos if peak_pos is not None else 0.4), 0.05), 0.95) * (duration - 1)
    weights = [max(1.0 - abs(i - pk) / max(pk, duration - 1 - pk), 0.05) for i in range(duration)]
    scale = float(total_mm) / sum(weights)
    return [round(w * scale, 3) for w in weights]


@router.post("/{pid}/calibration/forecast")
def run_forecast(pid: str, payload: dict | None = None):
    """情景预报（P6）：未来雨情（设计雨型或逐日序列）拼接在实测时序末尾，
    用当前参数沿 parent 链连续演算——历史段天然热启动，预报段即未来出口流量。

    body: {
      horizon_days: 30,                       # 预见期（1~3650 天）
      rain: {mode: "design", total_mm, duration_days?, peak_pos?}   # 设计雨型
          | {mode: "series", values: [逐日mm]},                      # 预测降雨序列
      evap_mm?: 蒸发假设（默认取历史均值）,
      params?: {code:{...}}（缺省用当前参数集）,
      period?: {start,end,warmup_days}        # 历史段窗口
    }
    返回 simulate 结构 + forecast{start,days,n_obs} + 各单元 forecast 摘要 + rain_scenario。
    """
    get_project_or_404(pid)
    sub = _require_subbasins(pid)
    manifest = st.read_ts_manifest(pid)
    payload = payload or {}
    horizon = min(max(int(payload.get("horizon_days") or 30), 1), 3650)

    rain_in = payload.get("rain") or {}
    if (rain_in.get("mode") or "design") == "series":
        values = [float(v) for v in (rain_in.get("values") or [])]
        if not values:
            raise HTTPException(400, "rain.mode=series 时 values 不能为空")
        rain_mm = (values + [0.0] * horizon)[:horizon]
        rain_desc = f"逐日预测序列（合计 {sum(rain_mm):.1f} mm / {horizon} 天）"
    else:
        total = float(rain_in.get("total_mm") or 0)
        if total <= 0:
            raise HTTPException(400, "设计雨型需要 rain.total_mm > 0")
        dur = min(max(int(rain_in.get("duration_days") or min(horizon, 7)), 1), horizon)
        rain_mm = _design_rain(total, dur, rain_in.get("peak_pos"))
        rain_mm = rain_mm + [0.0] * (horizon - len(rain_mm))
        rain_desc = (
            f"设计雨型 {total:.0f} mm / {dur} 天"
            f"（峰位 {float(rain_in.get('peak_pos') or 0.4):.2f}）"
        )

    extend = {"days": horizon, "rain_mm": rain_mm}
    if payload.get("evap_mm") is not None:
        extend["evap_mm"] = float(payload["evap_mm"])

    saved = _read_default_params(pid) or {}
    merged = dict(saved)
    for code, prm in (payload.get("params") or {}).items():
        merged.setdefault(code, {}).update(prm or {})

    res = simulate_basin(sub, manifest, series_loader(pid), params=merged or None,
                         period=payload.get("period"), extend=extend)
    if not res.get("ok"):
        raise HTTPException(400, res.get("error") or "预报失败")

    fc = res.get("forecast") or {}
    n_obs = int(fc.get("n_obs") or res["n_steps"])
    start_label = fc.get("start") or ""
    for u in res.get("units") or []:
        s = u.get("series") or {}
        times, sim = s.get("time") or [], s.get("sim") or []
        idxs = [i for i, t in enumerate(times) if t >= start_label]
        fv = [float(sim[i]) for i in idxs if i < len(sim)]
        area = float(u.get("area_km2") or 0)
        depth = sum(fv) * res["dt_s"] / (area * 1000.0) if area and fv else 0.0
        peak_i = max(range(len(fv)), key=lambda i: fv[i]) if fv else None
        u["forecast"] = {
            "peak_q": round(fv[peak_i], 3) if fv else None,
            "peak_time": times[idxs[peak_i]] if peak_i is not None and idxs else None,
            "mean_q": round(sum(fv) / len(fv), 3) if fv else None,
            "runoff_mm": round(depth, 2),
            "days": len(fv),
        }

    res["rain_scenario"] = {"desc": rain_desc, "values": rain_mm, "horizon_days": horizon}
    res["warnings"] = [*(res.get("warnings") or []), f"预报情景：{rain_desc}（非实测、非实时接入）"]
    return res


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
        "history": [],          # 逐代收敛历史（运行中即可画收敛曲线）
        "free_keys": [], "units_plan": [s["code"] for s in (sub.get("subbasins") or [])],
    }

    def on_progress(payload: dict):
        prog.update(payload)
        prog["elapsed_s"] = round(time.time() - started, 1)
        ph = payload.get("phase")
        if ph == "run" and payload.get("unit"):
            hist = prog.get("history")
            if not isinstance(hist, list):
                hist = []
                prog["history"] = hist
            hist.append({
                "unit": payload["unit"], "gen": payload.get("gen"),
                "evals": payload.get("evals"), "best_f": payload.get("best_f"),
            })
        elif ph == "done" and payload.get("unit"):
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
            "mode": res.get("mode") or "chain",
            "joint": bool(res.get("joint")),
            "objective": res.get("objective"),
            "slots": res.get("slots") or [],
            "joint_convergence": res.get("joint_convergence") or [],
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
    """启动 SCE-UA 率定（后台线程），返回 run_id。

    body: {mode?: "chain"|"joint", period{start,end,warmup_days}, split, max_evals,
           seed, tol, lock: {code:{参数:值}},
           share?: {参数:[单元...]}（联合模式参数共享分组）,
           weights?: {code: 权重}（联合模式各站权重，默认 1）}
    """
    get_project_or_404(pid)
    sub = _check_runnable(pid)
    with _RUNS_LOCK:
        for rid, e in _RUNS.items():
            if e.get("pid") == pid and e.get("thread") and e["thread"].is_alive():
                raise HTTPException(409, f"该项目已有率定任务在运行（{rid}），请先等待完成或终止")
    payload = payload or {}
    mode = str(payload.get("mode") or "chain").strip().lower()
    if mode not in ("chain", "joint"):
        raise HTTPException(400, f"不支持的率定模式: {mode}（可选 chain / joint）")
    cfg = {
        "mode": mode,
        "period": payload.get("period") or None,
        "split": payload.get("split") or 0.7,
        "max_evals": int(payload.get("max_evals") or 3000),
        "seed": int(payload.get("seed") or 0),
        "tol": payload.get("tol") or 1e-4,
        "lock": payload.get("lock") or {},
        "share": payload.get("share") or {},
        "weights": payload.get("weights") or {},
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
        dirs = [d for d in rdir.iterdir() if d.is_dir()]
        # rid 是随机十六进制，按目录名排序≠按时间排序；以 progress.started_at 为准（新→旧）
        def _key(d):
            prog = st.read_json_file(d / "progress.json") or {}
            return prog.get("started_at") or ""

        for d in sorted(dirs, key=_key, reverse=True):
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
    """收敛曲线（轻量接口，**运行中即可用**，不拖全量结果）。

    任务结束后优先返回 result.json 各单元完整的收敛史；运行中则把 progress.json
    累积的逐代历史按单元分组返回（前端可实时画曲线）。
    """
    get_project_or_404(pid)
    doc = st.read_json_file(st.cal_run_dir(pid, rid) / "result.json")
    if doc:
        if doc.get("joint_convergence"):
            # 联合模式：整条链一个收敛史（逐代多站加权目标）
            curve = [{"code": "JOINT", "points": doc["joint_convergence"]}]
        else:
            curve = [
                {"code": u["code"], "points": u.get("convergence") or []}
                for u in doc.get("units") or []
            ]
        return {"ok": True, "finished": True, "joint": bool(doc.get("joint")), "curve": curve}
    prog = _read_progress(pid, rid)
    if not prog:
        raise HTTPException(404, f"任务不存在: {rid}")
    grouped: dict[str, list] = {}
    for h in prog.get("history") or []:
        grouped.setdefault(h.get("unit") or "?", []).append(
            {"gen": h.get("gen"), "best_f": h.get("best_f"), "evals": h.get("evals")}
        )
    return {
        "ok": True, "finished": False,
        "status": prog.get("status"), "phase": prog.get("phase"),
        "unit": prog.get("unit"), "units_done": prog.get("units_done") or [],
        "units_plan": prog.get("units_plan") or [],
        "free_keys": prog.get("free_keys") or [],
        "evals": prog.get("evals"), "elapsed_s": prog.get("elapsed_s"),
        "curve": [{"code": c, "points": pts} for c, pts in grouped.items()],
    }


# ---------------------------------------------------------------- 导出（P5：导出表入模）
def _csv_response(body: str, filename: str) -> Response:
    """CSV 下载响应（带 BOM，Excel 直接打开不乱码）。"""
    return Response(
        content="\ufeff" + body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{pid}/calibration/export")
def export_csv(pid: str, what: str = "params", rid: str = ""):
    """导出 CSV（P5）。

    - ``what=params``：参数长表 ``unit_code,param,value``。有 ``rid`` 取该次率定的
      final_params，否则取当前项目参数集（default.json）。表可直接回导入（前端
      「导入参数 CSV」→ apply），也可作为其他模型的输入表；
    - ``what=flow``：各站出口的全分辨率过程线 ``time,<unit>_obs,<unit>_sim,...``。
      有 ``rid`` 用该次最终参数整体复算（含其模拟时段），否则用当前参数集全时段模拟。
    """
    get_project_or_404(pid)
    sub = _require_subbasins(pid)
    sbs = sorted(sub.get("subbasins", []), key=lambda s: (s.get("order_index") or 0))

    result_doc = st.read_json_file(st.cal_run_dir(pid, rid) / "result.json") if rid else None
    if rid and not result_doc:
        raise HTTPException(404, f"任务 {rid} 的结果尚未生成")

    if what == "params":
        params = (result_doc or {}).get("final_params") if result_doc else None
        if params is None:
            params = _read_default_params(pid) or {}
        lines = ["unit_code,param,value"]
        for sb in sbs:
            code = sb["code"]
            prm = params.get(code) or {}
            for k in PARAM_KEYS:
                if k in prm:
                    lines.append(f"{code},{k},{float(prm[k]):.6g}")
        tag = rid or "current"
        return _csv_response("\n".join(lines) + "\n", f"params_{tag}.csv")

    if what == "flow":
        params = (result_doc or {}).get("final_params") if result_doc else None
        if params is None:
            params = _read_default_params(pid) or {}
        period = ((result_doc or {}).get("config") or {}).get("period") if result_doc else None
        sim = simulate_basin(sub, st.read_ts_manifest(pid), series_loader(pid),
                             params=params or None, period=period, return_raw=True)
        if not sim.get("ok"):
            raise HTTPException(400, sim.get("error") or "复算失败")
        raw = sim["_raw"]
        times = raw["times"]
        header = ["time"]
        cols: list[list] = []
        for u in raw["units"]:
            code = u["code"]
            obs = u.get("obs")
            header += [f"{code}_obs", f"{code}_sim"]
            cols.append([
                (f"{float(v):.4f}" if v is not None and np.isfinite(v) else "")
                for v in (obs if obs is not None else [None] * len(times))
            ])
            cols.append([f"{float(v):.4f}" for v in u["q"]])
        lines = [",".join(header)]
        for i, t in enumerate(times):
            lines.append(t + "," + ",".join(c[i] if i < len(c) else "" for c in cols))
        tag = rid or "current"
        return _csv_response("\n".join(lines) + "\n", f"flow_{tag}.csv")

    raise HTTPException(400, f"不支持的导出类型: {what}（可选 params / flow）")
