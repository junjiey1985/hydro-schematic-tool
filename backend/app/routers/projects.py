"""项目相关接口。"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .. import storage as st
from ..config import SAMPLES_DIR
from ..core.shp_io import list_shapefiles, read_shapefile
from .deps import guess_layer_type, get_project_or_404, normalize_features

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("")
def list_projects():
    return {"projects": st.list_projects()}


@router.post("")
def create_project(payload: dict):
    name = (payload.get("name") or "").strip() or "未命名项目"
    return st.create_project(name, payload.get("description") or "")


@router.get("/{pid}")
def get_project(pid: str):
    meta = get_project_or_404(pid)
    meta["has_topology"] = st.read_topology(pid) is not None
    meta["has_schematic"] = st.read_schematic(pid) is not None
    return meta


@router.delete("/{pid}")
def delete_project(pid: str):
    get_project_or_404(pid)
    st.delete_project(pid)
    return {"ok": True}


# ---------------------------------------------------------------- 样例数据
@router.get("/samples/available")
def samples_available():
    f = SAMPLES_DIR / "samples.json"
    if not f.exists():
        return {"available": False}
    info = json.loads(f.read_text(encoding="utf-8"))
    info["available"] = True
    return info


@router.post("/samples/import")
def import_samples(payload: dict | None = None):
    """一键创建示例项目（导入 data/samples 下的 SHP + DEM）。"""
    payload = payload or {}
    info_file = SAMPLES_DIR / "samples.json"
    if not info_file.exists():
        raise HTTPException(500, "示例数据不存在，请先在 backend 目录执行：python scripts/make_samples.py")
    info = json.loads(info_file.read_text(encoding="utf-8"))

    meta = st.create_project(payload.get("name") or info.get("name") or "示例流域", "系统生成的水系样例数据")

    if payload.get("copy_shapefiles", True):
        out = SAMPLES_DIR / "shp"
        out.mkdir(exist_ok=True)
        for item in info.get("layers", []):
            src = SAMPLES_DIR / item["file"]
            if src.exists():
                shutil.copy2(src, out / src.name)
            for ext in (".shx", ".dbf", ".prj", ".cpg"):
                s = src.with_suffix(ext)
                if s.exists():
                    shutil.copy2(s, out / s.name)

    base = SAMPLES_DIR / "shp" if (SAMPLES_DIR / "shp").exists() else SAMPLES_DIR
    for item in info.get("layers", []):
        p = base / item["file"]
        if not p.exists():
            p = SAMPLES_DIR / item["file"]
        if not p.exists():
            continue
        gj = read_shapefile(p)
        st.add_layer(
            meta["id"],
            item.get("name") or p.stem,
            item.get("type") or guess_layer_type(p.stem, "LineString"),
            {"features": normalize_features(gj["features"])},
            source="samples",
            metadata={"source_file": p.name, "source_crs": gj.get("source_crs"), "reprojected": gj.get("reprojected")},
        )

    # DEM
    dem_info = info.get("dem") or {}
    dem_src = SAMPLES_DIR / dem_info.get("file", "dem.asc")
    if dem_src.exists():
        dst = st.dem_dir(meta["id"]) / dem_src.name
        shutil.copy2(dem_src, dst)
        relief_src = SAMPLES_DIR / dem_info.get("relief", "dem_relief.png")
        if relief_src.exists():
            shutil.copy2(relief_src, st.dem_dir(meta["id"]) / relief_src.name)
        dem_info = dict(dem_info)
        dem_info["relief"] = relief_src.name if relief_src.exists() else None
        st.save_dem_meta(meta["id"], dem_info)

    # 导入后直接建好拓扑与概化图，打开即可看到效果
    from .deps import build_and_save_analysis

    analysis = build_and_save_analysis(
        meta["id"],
        topo_options={**info.get("topo_options", {}), **(payload.get("topo_options") or {})},
    )

    return {"ok": True, "project": st.get_project(meta["id"]), "analysis": analysis}
