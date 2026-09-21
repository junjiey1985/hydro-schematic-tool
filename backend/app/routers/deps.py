"""接口公共依赖与辅助函数。"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import HTTPException

from .. import storage as st
from ..config import LAYER_TYPES

GEOM_TO_TYPE = {
    "LineString": "river",
    "MultiLineString": "river",
    "Point": "other",
    "MultiPoint": "other",
    "Polygon": "lake",
    "MultiPolygon": "lake",
}

NAME_HINTS = [
    (("水文", "HYDRO", "STATION"), "hydro_station"),
    (("雨量", "RAIN", "PRECIP"), "rain_station"),
    (("湖泊", "水库", "湖", "LAKE", "RESERVOIR"), "lake"),
    (("河流", "水系", "河道", "RIVER", "STREAM", "CHANNEL"), "river"),
    (("边界", "流域", "BOUNDARY", "BASIN", "WATERSHED"), "boundary"),
]


def get_project_or_404(pid: str) -> dict:
    meta = st.get_project(pid)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"项目不存在: {pid}")
    return meta


def series_loader(pid: str):
    """时序序列读取回调：``series_loader(pid)(kind, key) -> [(datetime, value)]``。"""

    def load(kind: str, key: str):
        from ..core.timeseries import csv_to_records

        path = st.ts_series_path(pid, kind, key)
        if not path.exists():
            return []
        return csv_to_records(path.read_text(encoding="utf-8"))

    return load


def get_layer_or_404(pid: str, lid: str) -> dict:
    meta = get_project_or_404(pid)
    for l in meta.get("layers", []):
        if l["id"] == lid:
            return l
    raise HTTPException(status_code=404, detail=f"图层不存在: {lid}")


def normalize_features(features: list[dict]) -> list[dict]:
    """补齐要素 id 与 properties。"""
    out = []
    for f in features or []:
        if not isinstance(f, dict) or not f.get("geometry"):
            continue
        f.setdefault("properties", {})
        if not f.get("id"):
            f["id"] = uuid.uuid4().hex[:12]
        f["properties"] = {k: v for k, v in f["properties"].items() if not str(k).startswith("_layer")}
        out.append(f)
    return out


def guess_layer_type(name: str, geom_type: str) -> str:
    up = (name or "").upper()
    for keys, t in NAME_HINTS:
        if any(k in up for k in keys):
            return t
    return GEOM_TO_TYPE.get(geom_type, "other")


def layer_label(layer_type: str) -> str:
    return LAYER_TYPES.get(layer_type, LAYER_TYPES["other"])["label"]


def geojson_of(pid: str, types: list[str]) -> list[dict]:
    return st.all_features(pid, types)


def dem_context(pid: str, cache: bool = True) -> Optional[dict]:
    """载入 DEM 及填洼/流向/汇流累积结果（供拓扑流向判定与子流域划分使用），带磁盘缓存。

    缓存用无损的 .npy：早期用 ASCII Grid 定点写回，2 位小数会把填洼的 eps 抬升
    梯度抹平，读回后在平地上产生大量「假洼地」，导致 DEM 流向与汇流断裂。
    """
    from ..core.dem import (
        d8_directions_resolved,
        fill_depressions,
        flow_accumulation_topo,
        read_dem,
        read_raster_cache,
        write_raster_cache,
    )

    info = st.read_dem_meta(pid)
    if not info:
        return None
    ddir = st.dem_dir(pid)
    src = ddir / info.get("file", "dem.asc")
    if not src.exists():
        return None

    dem, meta = read_dem(src)
    nodata = meta.get("nodata", -9999.0)
    ff, af = ddir / "filled.npy", ddir / "acc.npy"
    cached = (read_raster_cache(ff), read_raster_cache(af)) if cache else (None, None)
    if cached[0] is not None and cached[1] is not None and cached[0].shape == dem.shape:
        return {"dem": dem, "meta": meta, "filled": cached[0], "acc": cached[1]}

    filled = fill_depressions(dem, nodata=nodata)
    _fdir, di, dj = d8_directions_resolved(filled, nodata=nodata)
    acc = flow_accumulation_topo(di, dj)
    if cache:
        write_raster_cache(ff, filled)
        write_raster_cache(af, acc)
        for stale in ("filled.asc", "acc.asc"):  # 清掉旧的低精度缓存
            p = ddir / stale
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass
        new_info = dict(info)
        new_info["filled_file"] = "filled.npy"
        new_info["acc_file"] = "acc.npy"
        st.save_dem_meta(pid, new_info)
    return {"dem": dem, "meta": meta, "filled": filled, "acc": acc}


def build_and_save_analysis(pid: str, topo_options: dict | None = None, sch_options: dict | None = None) -> dict:
    """一站式：构建拓扑 + 生成概化图并落盘。样例导入与接口都复用这套逻辑。"""
    from ..core.schematic import build_schematic
    from ..core.topology import build_topology

    rivers = st.all_features(pid, ["river"])
    if not rivers:
        return {"ok": False, "error": "没有河流图层"}
    ctx = None
    try:
        ctx = dem_context(pid)
    except Exception:  # noqa: BLE001
        ctx = None

    topo = build_topology(
        rivers,
        st.all_features(pid, ["hydro_station"]),
        st.all_features(pid, ["rain_station"]),
        st.all_features(pid, ["lake"]),
        dem_ctx=ctx,
        options=topo_options or {},
    )
    if not topo.get("ok"):
        return topo
    topo["use_dem"] = ctx is not None
    st.save_topology(pid, topo)

    sch = build_schematic(
        topo,
        options={k: v for k, v in (sch_options or {}).items() if v is not None},
        previous=st.read_schematic(pid),
    )
    if sch.get("ok"):
        st.save_schematic(pid, sch)
    return {"ok": True, "topology": topo, "schematic": sch}
