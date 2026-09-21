"""DEM 上传与河网自动提取接口。"""
from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from .. import storage as st
from ..config import DEFAULT_OPTIONS
from ..core.dem import extract_network, make_relief, read_dem, write_raster_cache
from ..core.topology import build_topology, name_rivers
from .deps import get_project_or_404, normalize_features

router = APIRouter(prefix="/api/projects", tags=["dem"])

ALLOWED_SUFFIX = {".asc", ".txt", ".grd", ".tif", ".tiff", ".img"}


@router.get("/{pid}/dem")
def get_dem(pid: str):
    get_project_or_404(pid)
    info = st.read_dem_meta(pid)
    if not info:
        return {"ok": False, "error": "项目尚未上传 DEM"}
    info = dict(info)
    info["ok"] = True
    return info


@router.post("/{pid}/dem/upload")
async def upload_dem(pid: str, file: UploadFile = File(...)):
    get_project_or_404(pid)
    fn = Path(file.filename or "dem.asc").name
    if Path(fn).suffix.lower() not in ALLOWED_SUFFIX:
        raise HTTPException(400, f"不支持的栅格格式：{Path(fn).suffix}（支持 .asc/.tif）")
    dst = st.dem_dir(pid) / fn
    with dst.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    # 清理旧的中间文件
    for stale in ("filled.asc", "acc.asc", "filled.npy", "acc.npy"):
        p = st.dem_dir(pid) / stale
        if p.exists():
            p.unlink()

    try:
        dem, meta = read_dem(dst)
    except Exception as e:  # noqa: BLE001
        dst.unlink(missing_ok=True)
        raise HTTPException(400, str(e)) from e

    relief = st.dem_dir(pid) / "dem_relief.png"
    rmeta = make_relief(dem, meta, relief)
    bounds = [
        round(meta["xll"], 7),
        round(meta["yll"], 7),
        round(meta["xll"] + meta["ncols"] * meta["cellsize"], 7),
        round(meta["yll"] + meta["nrows"] * meta["cellsize"], 7),
    ]
    info = {
        "file": fn,
        "relief": relief.name,
        "bounds": rmeta.get("bounds") or bounds,
        "elev_min": rmeta.get("elev_min"),
        "elev_max": rmeta.get("elev_max"),
        "cellsize": meta["cellsize"],
        "ncols": meta["ncols"],
        "nrows": meta["nrows"],
        "crs": "EPSG:4326",
        "accum_threshold": DEFAULT_OPTIONS["dem"]["accum_threshold"],
    }
    st.save_dem_meta(pid, info)
    return {"ok": True, "dem": info}


@router.get("/{pid}/dem/relief")
def dem_relief(pid: str):
    info = st.read_dem_meta(pid)
    if not info or not info.get("relief"):
        raise HTTPException(404, "没有晕渲底图")
    p = st.dem_dir(pid) / info["relief"]
    if not p.exists():
        raise HTTPException(404, "晕渲底图文件缺失")
    return FileResponse(p, media_type="image/png")


@router.delete("/{pid}/dem")
def delete_dem(pid: str):
    get_project_or_404(pid)
    d = st.dem_dir(pid)
    shutil.rmtree(d, ignore_errors=True)
    meta = st.get_project(pid)
    if meta:
        meta["dem"] = None
        st.save_project(meta)
    return {"ok": True}


@router.post("/{pid}/dem/extract")
def extract(pid: str, payload: dict | None = None):
    """由 DEM 实时生成河网并入库（填洼 → 流向 → 汇流累积 → 阈值提取 → 矢量化 → 分级 → 命名）。"""
    payload = payload or {}
    get_project_or_404(pid)
    info = st.read_dem_meta(pid)
    if not info:
        raise HTTPException(400, "请先上传 DEM")

    src = st.dem_dir(pid) / info.get("file", "dem.asc")
    dem, meta = read_dem(src)
    opts = dict(DEFAULT_OPTIONS["dem"])
    opts.update({k: v for k, v in (payload.get("options") or {}).items() if v is not None})
    threshold = float(payload.get("threshold") or opts["accum_threshold"])
    simplify_m = float(payload.get("simplify_m") or opts["simplify_m"])
    smooth = bool(payload.get("smooth", opts["smooth"]))

    net = extract_network(dem, meta, threshold=threshold, simplify_m=simplify_m, smooth=smooth)
    segs = net["segments"]
    if not segs:
        raise HTTPException(400, "未提取到河网，请降低汇流累积阈值后重试")

    # 缓存填洼与汇流累积（无损 .npy），后续拓扑构建与子流域划分直接复用
    write_raster_cache(st.dem_dir(pid) / "filled.npy", net["filled"])
    write_raster_cache(st.dem_dir(pid) / "acc.npy", net["acc"])
    info = dict(info)
    info["filled_file"] = "filled.npy"
    info["acc_file"] = "acc.npy"
    info["accum_threshold"] = threshold
    st.save_dem_meta(pid, info)

    # 由拓扑识别干流并命名
    from .deps import dem_context

    ctx = dem_context(pid)
    feats = [
        {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [[round(c[0], 7), round(c[1], 7)] for c in s["coords"]],
            },
            "properties": {
                "级别": int(s["order"]),
                "长度_KM": round(s["length_m"] / 1000.0, 3),
                "上游面积": s["upstream_area_km2"],
            },
        }
        for s in segs
    ]
    tmp = build_topology(feats, dem_ctx=ctx, options={"dissolve_pseudo_nodes": False})
    name_map: dict[str, str] = {}
    main_name = payload.get("main_name") or "干流"
    if tmp.get("ok"):
        name_map = name_rivers(tmp, main_name=main_name, trib_names=payload.get("trib_names") or ())
        # 用河段中点匹配回矢量要素
        for f in feats:
            mid = f["geometry"]["coordinates"][len(f["geometry"]["coordinates"]) // 2]
            best, bd = None, 1e18
            for e in tmp["edges"]:
                c = e["coords"]
                cm = c[len(c) // 2]
                dd = (cm[0] - mid[0]) ** 2 + (cm[1] - mid[1]) ** 2
                if dd < bd:
                    bd, best = dd, e["id"]
            f["properties"]["河名"] = name_map.get(best or "", "")

    for f in feats:
        f["properties"].setdefault("河名", "")

    created = None
    if payload.get("create_layer", True):
        replace_id = payload.get("replace_layer_id")
        if replace_id:
            try:
                st.delete_layer(pid, replace_id)
            except Exception:  # noqa: BLE001
                pass
        created = st.add_layer(
            pid,
            payload.get("layer_name") or f"DEM生成河网({int(threshold)}格)",
            "river",
            {"features": normalize_features(feats)},
            source="dem",
            metadata={"threshold": threshold, "simplify_m": simplify_m, "segment_count": len(feats)},
        )

    return {
        "ok": True,
        "threshold": threshold,
        "segment_count": len(feats),
        "max_order": max(s["order"] for s in segs),
        "total_length_km": round(sum(s["length_m"] for s in segs) / 1000.0, 2),
        "layer": created,
    }
