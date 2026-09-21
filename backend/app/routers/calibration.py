"""模型模拟与率定接口。

P2 已实现：参数集查询/保存、全流域模拟（simulate）。
P3 将增加：SCE-UA 率定任务（runs/<rid>）、进度轮询、终止。
"""
from __future__ import annotations

import json
import urllib.parse

from fastapi import APIRouter, HTTPException

from .. import storage as st
from ..core.model.params import FIXED_SPEC, PARAM_KEYS, PARAM_SPEC, default_params
from ..core.model.simulate import simulate_basin
from .deps import get_project_or_404, series_loader

router = APIRouter(prefix="/api/projects", tags=["calibration"])


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
    """保存参数集为项目默认（body: {params: {code: {...}}）或 {reset: true} 恢复默认。"""
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
    params_in = payload.get("params") or {}
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
