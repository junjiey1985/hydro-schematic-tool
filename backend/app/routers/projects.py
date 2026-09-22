"""项目相关接口。"""
from __future__ import annotations

import io
import json
import re
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from .. import storage as st
from ..config import SAMPLES_DIR
from ..core.shp_io import list_shapefiles, read_shapefile
from .deps import guess_layer_type, get_project_or_404, normalize_features

router = APIRouter(prefix="/api/projects", tags=["projects"])

# 导出时跳过的大文件 / 缓存目录（按相对路径首段）
_EXPORT_SKIP_DIRS = {"__pycache__"}


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
    meta["has_dem"] = bool(meta.get("dem"))
    return meta


@router.patch("/{pid}")
def update_project(pid: str, payload: dict):
    """重命名 / 修改项目说明。body `{name?, description?}`，仅更新提供的字段。"""
    get_project_or_404(pid)
    try:
        return st.update_project(
            pid,
            name=payload.get("name") if "name" in payload else None,
            description=payload.get("description") if "description" in payload else None,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"项目不存在: {pid}")


@router.delete("/{pid}")
def delete_project(pid: str):
    get_project_or_404(pid)
    st.delete_project(pid)
    return {"ok": True}


# ---------------------------------------------------------------- 导出 / 导入
@router.get("/{pid}/export")
def export_project(pid: str):
    """把整个项目（元数据 + 图层 + 拓扑/概化图/单元 + DEM + 时序 + 率定）打包为 zip 下载。"""
    meta = get_project_or_404(pid)
    d = st.project_dir(pid)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(d.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(d)
            if any(part in _EXPORT_SKIP_DIRS for part in rel.parts):
                continue
            z.write(p, rel.as_posix())
    # ASCII 文件名兜底 + RFC 5987 中文文件名
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", meta.get("name") or pid) or pid
    fname = f"{safe}_{pid}.zip"
    from urllib.parse import quote

    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f"attachment; filename=\"{fname}\"; filename*=UTF-8''{quote(meta.get('name') or pid)}.zip"
        },
    )


@router.post("/import")
async def import_project(
    file: UploadFile = File(...),
    name: str = Form(None),
    description: str = Form(None),
):
    """导入项目 zip（须为本平台 `/export` 导出的包），创建为新项目。"""
    data = await file.read()
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise HTTPException(400, "不是有效的 zip 文件")

    names = zf.namelist()
    if "project.json" not in names:
        raise HTTPException(400, "zip 缺少 project.json——请使用本平台「导出项目」生成的 zip")

    try:
        src_meta = json.loads(zf.read("project.json").decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"project.json 解析失败: {e}")

    new_name = (name or "").strip() or f"{src_meta.get('name', '导入项目')}（导入）"
    meta = st.create_project(new_name, (description or "").strip() or src_meta.get("description") or "")
    new_pid = meta["id"]
    d = st.project_dir(new_pid)

    try:
        zf.extractall(d)
    except Exception as e:  # noqa: BLE001
        st.delete_project(new_pid)
        raise HTTPException(500, f"解压失败: {e}")

    # 重写 project.json：换 id / 名称 / 时间戳（图层清单等其余字段原样保留）
    src_meta["id"] = new_pid
    src_meta["name"] = new_name
    if description is not None and description.strip():
        src_meta["description"] = description.strip()
    src_meta["created_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    src_meta["updated_at"] = src_meta["created_at"]
    (d / "project.json").write_text(
        json.dumps(src_meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    out = st.get_project(new_pid)
    out["layer_count"] = len(out.get("layers", []))
    out["has_topology"] = st.read_topology(new_pid) is not None
    out["has_schematic"] = st.read_schematic(new_pid) is not None
    out["has_dem"] = bool(out.get("dem"))
    return out


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
