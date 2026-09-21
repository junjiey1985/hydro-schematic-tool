"""图层接口：SHP 导入、要素增删改、图层管理。"""
from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .. import storage as st
from ..config import LAYER_TYPES, UPLOAD_DIR
from ..core.shp_io import extract_zip, read_shapefile
from .deps import (
    GEOM_TO_TYPE,
    get_layer_or_404,
    get_project_or_404,
    guess_layer_type,
    normalize_features,
)

router = APIRouter(prefix="/api/projects", tags=["layers"])


@router.get("/{pid}/layers")
def list_layers(pid: str):
    meta = get_project_or_404(pid)
    return {"layers": meta.get("layers", []), "types": LAYER_TYPES, "extent": meta.get("extent")}


@router.post("/{pid}/layers")
def create_layer(pid: str, payload: dict):
    get_project_or_404(pid)
    name = (payload.get("name") or "").strip() or "新建图层"
    ltype = payload.get("type") or "other"
    features = normalize_features(payload.get("features") or [])
    geom = LAYER_TYPES.get(ltype, LAYER_TYPES["other"])["geometry"]
    if features:
        geom = features[0]["geometry"]["type"]
    info = st.add_layer(pid, name, ltype, {"features": features}, source=payload.get("source") or "draw",
                        style=payload.get("style") or {}, metadata={"geometry": geom})
    return info


@router.post("/{pid}/layers/upload")
async def upload_layers(
    pid: str,
    files: list[UploadFile] = File(...),
    layer_type: str = Form(None),
    name: str = Form(None),
    encoding: str = Form(None),
):
    """上传 SHP（可多个文件或 zip 打包），自动转 WGS84 并入库。"""
    get_project_or_404(pid)
    work = UPLOAD_DIR / st.new_id("up_")
    work.mkdir(parents=True, exist_ok=True)
    try:
        for uf in files:
            fn = Path(uf.filename or "unnamed").name
            dst = work / fn
            with dst.open("wb") as f:
                shutil.copyfileobj(uf.file, f)
            if dst.suffix.lower() == ".zip":
                extract_zip(dst, work)

        shps = [p for p in work.rglob("*") if p.suffix.lower() == ".shp"]
        if not shps:
            raise HTTPException(400, "上传内容中未找到 .shp 文件（请同时上传同名 .dbf/.shx/.prj）")

        created = []
        for p in shps:
            try:
                gj = read_shapefile(p, encoding=encoding or None)
            except Exception as e:  # noqa: BLE001
                created.append({"file": p.name, "ok": False, "error": str(e)})
                continue
            if not gj["features"]:
                created.append({"file": p.name, "ok": False, "error": "要素为空"})
                continue
            gt = gj["features"][0]["geometry"]["type"]
            lt = layer_type or guess_layer_type(name or p.stem, gt)
            info = st.add_layer(
                pid,
                name if name and len(shps) == 1 else p.stem,
                lt,
                {"features": normalize_features(gj["features"])},
                source="import",
                metadata={
                    "source_file": p.name,
                    "source_crs": gj.get("source_crs"),
                    "reprojected": gj.get("reprojected"),
                    "fields": list((gj["features"][0].get("properties") or {}).keys()),
                },
            )
            created.append({"file": p.name, "ok": True, "layer": info, "count": len(gj["features"])})
        return {"ok": True, "results": created}
    finally:
        shutil.rmtree(work, ignore_errors=True)


@router.get("/{pid}/layers/{lid}")
def get_layer(pid: str, lid: str):
    info = get_layer_or_404(pid, lid)
    gj = st.read_layer(pid, lid)
    if gj is None:
        raise HTTPException(404, "图层数据缺失")
    return {"info": info, "geojson": gj}


@router.patch("/{pid}/layers/{lid}")
def update_layer(pid: str, lid: str, payload: dict):
    get_layer_or_404(pid, lid)
    patch = {k: v for k, v in payload.items() if k in ("name", "type", "visible", "style", "metadata")}
    return st.update_layer_info(pid, lid, patch)


@router.delete("/{pid}/layers/{lid}")
def delete_layer(pid: str, lid: str):
    get_layer_or_404(pid, lid)
    st.delete_layer(pid, lid)
    return {"ok": True}


# ---------------------------------------------------------------- 要素级操作
@router.post("/{pid}/layers/{lid}/features")
def add_features(pid: str, lid: str, payload: dict):
    info = get_layer_or_404(pid, lid)
    gj = st.read_layer(pid, lid) or {"type": "FeatureCollection", "features": []}
    feats = normalize_features(payload.get("features") or [])
    if not feats:
        raise HTTPException(400, "未提供要素")
    expected = LAYER_TYPES.get(info.get("type", "other"), {}).get("geometry")
    for f in feats:
        gt = f["geometry"]["type"]
        if expected and expected != "Any" and not _compatible(expected, gt):
            raise HTTPException(400, f"几何类型不匹配：图层为 {expected}，要素为 {gt}")
    gj["features"].extend(feats)
    st.write_layer(pid, lid, gj)
    return {"ok": True, "added": len(feats), "ids": [f["id"] for f in feats]}


@router.put("/{pid}/layers/{lid}/features/{fid}")
def update_feature(pid: str, lid: str, fid: str, payload: dict):
    get_layer_or_404(pid, lid)
    gj = st.read_layer(pid, lid) or {"type": "FeatureCollection", "features": []}
    for f in gj["features"]:
        if str(f.get("id")) == fid:
            if payload.get("geometry"):
                f["geometry"] = payload["geometry"]
            if payload.get("properties"):
                f["properties"] = {**(f.get("properties") or {}), **payload["properties"]}
            st.write_layer(pid, lid, gj)
            return {"ok": True, "feature": f}
    raise HTTPException(404, f"要素不存在: {fid}")


@router.delete("/{pid}/layers/{lid}/features/{fid}")
def delete_feature(pid: str, lid: str, fid: str):
    get_layer_or_404(pid, lid)
    gj = st.read_layer(pid, lid) or {"type": "FeatureCollection", "features": []}
    before = len(gj["features"])
    gj["features"] = [f for f in gj["features"] if str(f.get("id")) != fid]
    if len(gj["features"]) == before:
        raise HTTPException(404, f"要素不存在: {fid}")
    st.write_layer(pid, lid, gj)
    return {"ok": True}


def _compatible(expected: str, actual: str) -> bool:
    if expected == actual:
        return True
    pairs = {
        "LineString": {"MultiLineString"},
        "MultiLineString": {"LineString"},
        "Polygon": {"MultiPolygon"},
        "MultiPolygon": {"Polygon"},
        "Point": {"MultiPoint"},
        "MultiPoint": {"Point"},
    }
    return actual in pairs.get(expected, set())
