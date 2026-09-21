"""拓扑构建与概化图接口。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import storage as st
from ..core.schematic import apply_manual_edits, build_schematic
from ..core.topology import build_topology, topology_to_geojson
from .deps import dem_context, get_project_or_404

router = APIRouter(prefix="/api/projects", tags=["analysis"])


# ================================================================= 拓扑
@router.get("/{pid}/topology")
def get_topology(pid: str):
    get_project_or_404(pid)
    topo = st.read_topology(pid)
    if topo is None:
        return {"ok": False, "error": "尚未构建拓扑关系"}
    return topo


@router.post("/{pid}/topology/build")
def build(pid: str, payload: dict | None = None):
    payload = payload or {}
    get_project_or_404(pid)

    rivers = st.all_features(pid, ["river"])
    if not rivers:
        raise HTTPException(400, "项目中没有河流图层，请先导入 SHP 或手工绘制河流")

    hydro = st.all_features(pid, ["hydro_station"])
    rain = st.all_features(pid, ["rain_station"])
    lakes = st.all_features(pid, ["lake"])

    ctx = None
    if payload.get("use_dem", True):
        try:
            ctx = dem_context(pid)
        except Exception:  # noqa: BLE001
            ctx = None

    topo = build_topology(rivers, hydro, rain, lakes, dem_ctx=ctx, options=payload.get("options") or {})
    if not topo.get("ok"):
        raise HTTPException(400, topo.get("error", "拓扑构建失败"))
    topo["use_dem"] = ctx is not None
    st.save_topology(pid, topo)
    return topo


@router.get("/{pid}/topology/geojson")
def topology_geojson(pid: str):
    topo = st.read_topology(pid)
    if not topo or not topo.get("ok"):
        raise HTTPException(404, "尚未构建拓扑关系")
    return topology_to_geojson(topo)


@router.delete("/{pid}/topology")
def clear_topology(pid: str):
    get_project_or_404(pid)
    p = st.project_dir(pid) / "topology.json"
    if p.exists():
        p.unlink()
    return {"ok": True}


# ================================================================= 概化图
@router.get("/{pid}/schematic")
def get_schematic(pid: str):
    get_project_or_404(pid)
    sch = st.read_schematic(pid)
    if sch is None:
        return {"ok": False, "error": "尚未生成概化图"}
    return sch


@router.post("/{pid}/schematic/build")
def build_sch(pid: str, payload: dict | None = None):
    payload = payload or {}
    topo = st.read_topology(pid)
    if not topo or not topo.get("ok"):
        raise HTTPException(400, "请先构建拓扑关系，再生成概化图")
    prev = st.read_schematic(pid) if payload.get("keep_manual", True) else None
    options = {k: v for k, v in (payload.get("options") or {}).items() if v is not None}
    sch = build_schematic(topo, options=options, previous=prev)
    if not sch.get("ok"):
        raise HTTPException(400, sch.get("error", "概化图生成失败"))
    st.save_schematic(pid, sch)
    return sch


@router.post("/{pid}/schematic/edit")
def edit_schematic(pid: str, payload: dict):
    sch = st.read_schematic(pid)
    if not sch or not sch.get("ok"):
        raise HTTPException(400, "尚未生成概化图")
    sch = apply_manual_edits(sch, payload or {})
    st.save_schematic(pid, sch)
    return {"ok": True, "schematic": sch}


@router.post("/{pid}/schematic/reset")
def reset_schematic(pid: str, payload: dict | None = None):
    payload = payload or {}
    topo = st.read_topology(pid)
    if not topo or not topo.get("ok"):
        raise HTTPException(400, "尚未构建拓扑关系")
    options = {k: v for k, v in (payload.get("options") or {}).items() if v is not None}
    sch = build_schematic(topo, options=options, previous=None)
    st.save_schematic(pid, sch)
    return sch


@router.get("/{pid}/schematic/export.svg")
def export_svg(pid: str):
    from fastapi.responses import Response

    from ..core.export import schematic_to_svg

    sch = st.read_schematic(pid)
    if not sch or not sch.get("ok"):
        raise HTTPException(404, "尚未生成概化图")
    svg = schematic_to_svg(sch)
    return Response(
        content=svg,
        media_type="image/svg+xml; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="schematic.svg"'},
    )
