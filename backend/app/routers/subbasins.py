"""子流域划分相关接口。

面向水文模型率定与实时预报：把流域按控制断面切成若干预报单元（子流域），
每个单元一套参数、一个产汇流计算单元，单元之间按上下游关系依次演算到出口。
"""
from __future__ import annotations

import csv
import io
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse

from .. import storage as st
from ..core.subbasin import (
    DEFAULT_SUBBASIN_OPTIONS,
    delineate_subbasins,
    report_rows,
    subbasin_to_geojson,
)
from ..core.shp_io import write_shapefile
from .deps import dem_context, get_project_or_404

router = APIRouter(prefix="/api/projects", tags=["subbasin"])


def _layer_features(pid: str, layer_type: str) -> list[dict]:
    return st.all_features(pid, [layer_type])


def _replace_layer(pid: str, name: str, layer_type: str, features: list[dict], metadata: dict | None = None) -> dict | None:
    """保证同类型图层只有一份（重复划分时整体替换）。"""
    for info in list(st.get_project(pid).get("layers", [])):
        if info.get("type") == layer_type:
            st.delete_layer(pid, info["id"])
    if not features:
        return None
    return st.add_layer(
        pid,
        name,
        layer_type,
        {"features": features},
        source="subbasin" if layer_type == "subbasin" else "manual",
        metadata=metadata or {},
    )


def _save_as_layer(pid: str, result: dict) -> None:
    """把划分结果落成地图图层：子流域面 + 单元出口断面点（便于地图上标注控制断面）。"""
    gj = subbasin_to_geojson(result)
    feats = list(gj["features"])
    n_poly = len([f for f in feats if f["properties"].get("kind") == "subbasin"])
    _replace_layer(
        pid,
        "子流域",
        "subbasin",
        feats,
        metadata={"subbasin_count": n_poly, "built_at": result.get("built_at")},
    )


# ---------------------------------------------------------------- 划分 / 查询
@router.post("/{pid}/subbasins/delineate")
def delineate(pid: str, payload: dict | None = None):
    """按控制断面划分子流域（station 水文站 | junction 汇流节点 | manual 手工控制点）。"""
    get_project_or_404(pid)
    payload = payload or {}
    topo = st.read_topology(pid)
    if not topo:
        raise HTTPException(400, "尚未构建拓扑关系，请先构建拓扑。")
    ctx = dem_context(pid)
    if not ctx:
        raise HTTPException(400, "子流域划分需要 DEM，请先上传 DEM 并生成河网。")

    info = st.read_dem_meta(pid) or {}
    opts = {k: v for k, v in (payload.get("options") or {}).items() if v is not None}
    for k in ("mode", "min_area_km2", "snap_max_m", "include_outlet", "name_prefix", "channel_accum_threshold", "merge_same_cell", "compute_rain_weights"):
        if payload.get(k) is not None:
            opts[k] = payload[k]
    ctx = dict(ctx)
    ctx["meta"] = dict(ctx["meta"])
    if opts.get("channel_accum_threshold") is None and info.get("accum_threshold"):
        ctx["meta"]["accum_threshold"] = info["accum_threshold"]

    outlets = payload.get("outlets")
    if opts.get("mode") == "manual" and not outlets:
        outlets = [
            {
                "id": f.get("properties", {}).get("id"),
                "name": f.get("properties", {}).get("名称") or f.get("properties", {}).get("name"),
                "lon": (f.get("geometry") or {}).get("coordinates", [None, None])[0],
                "lat": (f.get("geometry") or {}).get("coordinates", [None, None])[1],
            }
            for f in _layer_features(pid, "control_point")
            if f.get("geometry", {}).get("type") == "Point"
        ]
        if not outlets:
            raise HTTPException(400, "手工划分需要先在地图上绘制「控制断面」点（图层类型选控制断面）。")

    result = delineate_subbasins(ctx, topo, outlets=outlets, options=opts)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error") or "子流域划分失败")

    if payload.get("save", True):
        st.save_subbasins(pid, result)
        _save_as_layer(pid, result)
    return result


@router.get("/{pid}/subbasins")
def get_subbasins(pid: str):
    get_project_or_404(pid)
    res = st.read_subbasins(pid)
    if not res:
        return {"ok": False, "exists": False, "message": "尚未划分子流域"}
    return {**res, "exists": True, "rows": report_rows(res)}


@router.get("/{pid}/subbasins/geojson")
def subbasins_geojson(pid: str):
    get_project_or_404(pid)
    res = st.read_subbasins(pid)
    if not res:
        raise HTTPException(404, "尚未划分子流域")
    return subbasin_to_geojson(res)


@router.patch("/{pid}/subbasins/{code}")
def update_subbasin(pid: str, code: str, payload: dict):
    get_project_or_404(pid)
    res = st.read_subbasins(pid)
    if not res:
        raise HTTPException(404, "尚未划分子流域")
    patch = {k: v for k, v in (payload or {}).items() if k in ("name", "code", "note")}
    hit = None
    for sb in res.get("subbasins", []):
        if sb["code"] == code:
            hit = sb
            break
    if hit is None:
        raise HTTPException(404, f"子流域不存在：{code}")
    old_name = hit.get("name")
    hit.update(patch)
    st.save_subbasins(pid, res)
    # 图层属性同步（名称/编号）
    if patch.keys() & {"name", "code"} and old_name:
        layer = st.find_layer(pid, "subbasin")
        if layer:
            gj = st.read_layer(pid, layer["id"]) or {"type": "FeatureCollection", "features": []}
            for f in gj["features"]:
                p = f.get("properties") or {}
                if p.get("code") == code or p.get("name") == old_name:
                    if "name" in patch:
                        p["name"] = patch["name"]
                        p["属性"] = f"{p.get('code')} {patch['name']} {p.get('area_km2')}km²"
            st.write_layer(pid, layer["id"], gj)
    res["rows"] = report_rows(res)
    return res


@router.delete("/{pid}/subbasins")
def clear_subbasins(pid: str):
    get_project_or_404(pid)
    st.clear_subbasins(pid)
    layer = st.find_layer(pid, "subbasin")
    if layer:
        st.delete_layer(pid, layer["id"])
    return {"ok": True}


# ---------------------------------------------------------------- 导出
@router.get("/{pid}/subbasins/export.geojson")
def export_geojson(pid: str):
    get_project_or_404(pid)
    res = st.read_subbasins(pid)
    if not res:
        raise HTTPException(404, "尚未划分子流域")
    body = json.dumps(subbasin_to_geojson(res), ensure_ascii=False)
    return Response(
        content=body,
        media_type="application/geo+json",
        headers={"Content-Disposition": 'attachment; filename="subbasins.geojson"'},
    )


@router.get("/{pid}/subbasins/export.csv")
def export_csv(pid: str):
    get_project_or_404(pid)
    res = st.read_subbasins(pid)
    if not res:
        raise HTTPException(404, "尚未划分子流域")
    rows = report_rows(res)
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    data = buf.getvalue().encode("utf-8-sig")
    return StreamingResponse(
        io.BytesIO(data),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="subbasins.csv"'},
    )


@router.get("/{pid}/subbasins/export.shp")
def export_shp(pid: str):
    """导出子流域边界 Shapefile（zip 打包，属性为 UTF-8 编码的 DBF → GBK）。"""
    get_project_or_404(pid)
    res = st.read_subbasins(pid)
    if not res:
        raise HTTPException(404, "尚未划分子流域")
    gj = subbasin_to_geojson(res)
    polys = {"type": "FeatureCollection", "features": [f for f in gj["features"] if f["properties"]["kind"] == "subbasin"]}
    pts = {"type": "FeatureCollection", "features": [f for f in gj["features"] if f["properties"]["kind"] == "subbasin_outlet"]}
    tmp = Path(tempfile.mkdtemp(prefix="subbasin_"))
    try:
        out = tmp / "subbasins.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            if polys["features"]:
                write_shapefile(tmp / "subbasins.shp", polys, geom_type="polygon")
                for ext in (".shp", ".shx", ".dbf", ".prj"):
                    p = (tmp / f"subbasins{ext}")
                    if p.exists():
                        z.write(p, p.name)
            if pts["features"]:
                write_shapefile(tmp / "subbasin_outlets.shp", pts, geom_type="point")
                for ext in (".shp", ".shx", ".dbf", ".prj"):
                    p = (tmp / f"subbasin_outlets{ext}")
                    if p.exists():
                        z.write(p, p.name)
        data = out.read_bytes()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="subbasins.zip"'},
    )


@router.get("/{pid}/subbasins/options")
def subbasin_options(pid: str):
    """默认参数 + 可用的控制断面候选（供前端渲染参数面板）。"""
    get_project_or_404(pid)
    topo = st.read_topology(pid) or {}
    stations = [
        {"id": s.get("id"), "name": s.get("name"), "attached": bool(s.get("attached"))}
        for s in topo.get("stations", [])
        if s.get("type") == "hydro"
    ]
    junctions = len([n for n in topo.get("nodes", []) if n.get("kind") == "junction"])
    info = st.read_dem_meta(pid) or {}
    return {
        "options": DEFAULT_SUBBASIN_OPTIONS,
        "hydro_stations": stations,
        "junction_count": junctions,
        "has_dem": bool(info.get("file")),
        "accum_threshold": info.get("accum_threshold"),
        "control_points": len(_layer_features(pid, "control_point")),
    }
