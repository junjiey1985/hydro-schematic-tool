"""子流域划分（sub-basin delineation）——面向水文模型率定与预报的预报单元切分。

设计目标
--------
水文模型（新安江 / SCS / HEC-HMS / SWAT 等）率定与实时预报时，不会把整条流域当作
单一单元，而是先按控制断面（水文站、水库坝址、汇流节点）把流域切成若干**子流域**，
每个子流域是一套参数、一个产汇流计算单元，子流域之间按拓扑关系依次演算到出口。

本模块在已有 DEM 流水线（填洼 → D8 → 汇流累积）与河网拓扑（节点-弧段）之上，
完成四件事：

1. **控制断面落位**：把控制点（水文站坐标 / 汇流节点 / 手工点）吸附到最近的河道格网；
2. **集水区归属**：对每个格网沿 D8 下游追踪，归属于下游方向遇到的**第一个**控制断面，
   得到子流域栅格标签；
3. **单元统计与关系**：面积、高程特征、河道长度、河段归属、出口站、上游子流域链
   （子流域嵌套树）；
4. **成图与导出**：子流域边界多边形（GeoJSON / SHP）、站点-子流域权重（雨量站泰森权重）、
   报表（CSV）。

算法要点
--------
- 标签传播按**汇流累积量降序**单趟完成：下游格网的累积量必然大于上游格网，因此降序
  遍历时下游标签一定已解析，等价于沿 D8 路径回溯，复杂度 O(N)。
- 面积按**逐行像元面积**累加（经向宽度随纬度变化），避免用平均格网面积带来的偏差。
- 边界提取优先用 `rasterio.features.shapes`（C 实现），无 rasterio 时退化为纯 Python 边界追踪。
"""
from __future__ import annotations

import math
import time
from typing import Any, Optional

import numpy as np
from shapely.geometry import Point, shape as shp_shape

from .dem import d8_directions, d8_directions_resolved, flow_accumulation_topo

DEFAULT_SUBBASIN_OPTIONS: dict[str, Any] = {
    # 划分依据：station 以水文站为控制断面 | junction 以河网汇流节点自动分区 | manual 手工控制断面
    "mode": "station",
    # 最小子流域面积（km²）：小于该值的单元会被并入下游（junction 模式下直接不作为出口）
    "min_area_km2": 30.0,
    # 控制点向河道吸附的最大距离（米），超出则丢弃并给出警告
    # 注意：粗分辨率 DEM（如 SRTM 重采样）的河道与实测站点位置常有数公里偏差，
    # 默认给到 10 km，并在结果中记录实际偏移量供人工核对。
    "snap_max_m": 10000.0,
    # 是否把流域总出口也作为一个控制断面（保证最下游子流域存在）
    "include_outlet": True,
    # 出口合并距离（米）：两个控制断面落位到同一格网时只保留上游汇流更大的那个
    "merge_same_cell": True,
    # 边界多边形抽稀容差（度）
    "simplify_deg": 0.0015,
    # 命名前缀
    "name_prefix": "子流域",
    # 是否计算雨量站泰森权重
    "compute_rain_weights": True,
}


# ================================================================ 工具函数
def _valid_mask(dem: np.ndarray, nodata: float) -> np.ndarray:
    return np.isfinite(dem) & (np.abs(dem - nodata) > 1e-6)


def row_cell_area_km2(meta: dict) -> np.ndarray:
    """逐行像元面积（km²），行号自北向南。"""
    h = meta["nrows"]
    cs = meta["cellsize"]
    if abs(cs) > 0.5:  # 投影坐标，单位已是米
        return np.full(h, (cs * cs) / 1e6, dtype=np.float64)
    lat = meta["yll"] + (meta["nrows"] - np.arange(h) - 0.5) * cs
    dx = cs * 111320.0 * np.cos(np.radians(lat))
    dy = cs * 110540.0
    return (dx * dy) / 1e6


def _row_col_of(meta: dict, lon: float, lat: float) -> tuple[int, int]:
    """经纬度 → 行列号（行号自北向南，格心）。"""
    cs = meta["cellsize"]
    j = int(round((lon - meta["xll"]) / cs - 0.5))
    i = int(round((meta["nrows"] - 0.5) - (lat - meta["yll"]) / cs))
    return i, j


def cell_center(meta: dict, i: int, j: int) -> tuple[float, float]:
    cs = meta["cellsize"]
    return meta["xll"] + (j + 0.5) * cs, meta["yll"] + (meta["nrows"] - i - 0.5) * cs


def _meters_per_deg(lat: float) -> tuple[float, float]:
    return 111320.0 * math.cos(math.radians(lat)), 110540.0


def planar_distance_m(meta: dict, lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    kx, ky = _meters_per_deg((lat1 + lat2) / 2.0)
    return math.hypot((lon1 - lon2) * kx, (lat1 - lat2) * ky)


def channel_threshold(meta: dict, acc: np.ndarray, options: Optional[dict] = None) -> float:
    """河道判定阈值（汇流格数）：优先用项目里的河网提取阈值，否则按累积量自适应。"""
    opts = options or {}
    thr = opts.get("channel_accum_threshold")
    if not thr:
        thr = meta.get("accum_threshold")
    if not thr:
        thr = max(30.0, float(acc.max()) * 0.0005)
    return float(thr)


def build_channel_index(meta: dict, acc: np.ndarray, options: Optional[dict] = None) -> dict:
    """建立「任意格网 → 最近河道格网」索引（距离变换，C 实现，全图一次）。"""
    thr = channel_threshold(meta, acc, options)
    channel = acc >= thr
    h, w = acc.shape
    if not channel.any():
        return {"channel": channel, "threshold": thr, "valid": False}
    try:
        from scipy import ndimage

        dist, (ri, rj) = ndimage.distance_transform_edt(~channel, return_indices=True)
    except Exception:  # noqa: BLE001
        dist = np.zeros(acc.shape, dtype=np.float64)
        ri = np.repeat(np.arange(h)[:, None], w, axis=1)
        rj = np.repeat(np.arange(w)[None, :], h, axis=0)
    return {"channel": channel, "threshold": thr, "valid": True, "dist_cells": dist, "near_i": ri, "near_j": rj}


def snap_to_channel(
    meta: dict,
    acc: np.ndarray,
    lon: float,
    lat: float,
    max_m: float,
    index: Optional[dict] = None,
) -> Optional[dict]:
    """把控制点落位到**最近的**河道格网（河道由汇流阈值界定）。

    返回 {i, j, lon, lat, acc, offset_m, on_channel}；最近的河道格网超过 `max_m`
    或控制点落在 DEM 之外时返回 None。
    """
    h, w = acc.shape
    idx = index if index is not None else build_channel_index(meta, acc)
    if not idx.get("valid"):
        return None
    i0, j0 = _row_col_of(meta, lon, lat)
    if not (0 <= i0 < h and 0 <= j0 < w):
        return None
    on_channel = bool(idx["channel"][i0, j0])
    i, j = (i0, j0) if on_channel else (int(idx["near_i"][i0, j0]), int(idx["near_j"][i0, j0]))
    d_cells = float(idx["dist_cells"][i0, j0])
    clon, clat = cell_center(meta, i, j)
    # 距离用逐格网实地尺寸折算
    kx, ky = _meters_per_deg(lat)
    off = float(math.hypot(d_cells * meta["cellsize"] * kx, d_cells * meta["cellsize"] * ky)) if d_cells else 0.0
    off = min(off, planar_distance_m(meta, lon, lat, clon, clat) + 1e-9) if d_cells else 0.0
    if not on_channel:
        off = planar_distance_m(meta, lon, lat, clon, clat)
    if off > max_m:
        return None
    return {
        "i": int(i),
        "j": int(j),
        "lon": clon,
        "lat": clat,
        "acc": float(acc[i, j]),
        "offset_m": round(off, 1),
        "on_channel": on_channel,
    }


# ================================================================ 1. 控制断面
def _station_outlets(topology: dict, hydro_only: bool = True) -> list[dict]:
    out = []
    for s in topology.get("stations") or []:
        if hydro_only and s.get("type") != "hydro":
            continue
        out.append(
            {
                "source": "station",
                "id": s.get("id"),
                "name": s.get("name") or s.get("id"),
                "lon": s.get("x"),
                "lat": s.get("y"),
                "attached": bool(s.get("attached")),
            }
        )
    return out


def _manual_outlets(points: list[dict]) -> list[dict]:
    out = []
    for k, p in enumerate(points or []):
        lon, lat = p.get("lon", p.get("x")), p.get("lat", p.get("y"))
        if lon is None or lat is None:
            continue
        out.append(
            {
                "source": "manual",
                "id": p.get("id") or f"P{k + 1}",
                "name": p.get("name") or f"控制断面{k + 1}",
                "lon": float(lon),
                "lat": float(lat),
                "attached": True,
            }
        )
    return out


def _junction_outlets(topology: dict, min_area_km2: float) -> list[dict]:
    out = []
    for n in topology.get("nodes") or []:
        if n.get("kind") == "junction":
            area = n.get("upstream_area_km2")
            if area is not None and area < min_area_km2:
                continue
            out.append(
                {
                    "source": "junction",
                    "id": n.get("id"),
                    "name": n.get("id"),
                    "lon": n.get("x"),
                    "lat": n.get("y"),
                    "attached": True,
                    "upstream_area_km2": area,
                }
            )
    return out


def _outlet_of_topology(topology: dict) -> Optional[dict]:
    for n in topology.get("nodes") or []:
        if n.get("kind") == "outlet":
            return {
                "source": "basin_outlet",
                "id": n.get("id"),
                "name": "流域出口",
                "lon": n.get("x"),
                "lat": n.get("y"),
                "attached": True,
            }
    return None


def _dem_outlet(meta: dict, acc: np.ndarray) -> dict:
    """DEM 栅格自身的出口（全图汇流累积最大格网）——截断窗口下最可靠的总出口。"""
    h, w = acc.shape
    k = int(np.argmax(acc))
    i, j = divmod(k, w)
    lon, lat = cell_center(meta, i, j)
    return {
        "source": "dem_outlet",
        "id": "DEM_OUTLET",
        "name": "流域出口",
        "lon": lon,
        "lat": lat,
        "attached": True,
        "upstream_area_km2": None,
    }


# ================================================================ 2. 标签传播
def _propagate_labels(acc: np.ndarray, down_flat: list[int], seed_flat: list[int]) -> np.ndarray:
    """按汇流累积降序单趟传播子流域标签（下游累积必然更大）。"""
    n = acc.size
    lab = [-1] * n
    for k, idx in enumerate(seed_flat):
        lab[idx] = k
    order = np.argsort(-acc.ravel(), kind="stable").tolist()
    for idx in order:
        if lab[idx] < 0:
            d = down_flat[idx]
            if d >= 0:
                lab[idx] = lab[d]
    return np.array(lab, dtype=np.int32).reshape(acc.shape)


def _trace_parent(lab_list: list[int], down_flat: list[int], start: int, limit: int = 200000) -> int:
    """从某个出口格网沿下游走，返回第一个「别的」子流域编号（-1 表示流到流域外）。"""
    idx, steps, own = down_flat[start], 0, lab_list[start]
    while idx >= 0 and steps < limit:
        if lab_list[idx] >= 0 and lab_list[idx] != own:
            return lab_list[idx]
        idx = down_flat[idx]
        steps += 1
    return -1


# ================================================================ 3. 边界多边形
def _mask_to_polygon(mask: np.ndarray, meta: dict, simplify_deg: float) -> Optional[list]:
    """子流域掩膜 → 最大的多边形外环坐标（经纬度）。优先 rasterio，退化纯 Python。"""
    if not mask.any():
        return None
    cs = meta["cellsize"]
    geom = None
    try:
        import rasterio.features as rfeat
        from rasterio.transform import from_origin

        transform = from_origin(meta["xll"], meta["yll"] + meta["nrows"] * cs, cs, cs)
        cand = [
            shp_shape(g)
            for g, v in rfeat.shapes(mask.astype(np.uint8), mask=mask.astype(np.uint8), transform=transform)
            if v == 1
        ]
        cand = [g for g in cand if not g.is_empty]
        if cand:
            geom = max(cand, key=lambda g: g.area)
    except Exception:  # noqa: BLE001
        geom = None

    if geom is None:
        geom = _mask_to_polygon_py(mask, meta)
        if geom is None:
            return None

    if geom.geom_type == "MultiPolygon":
        geom = max(geom.geoms, key=lambda g: g.area)
    if geom.geom_type != "Polygon":
        return None
    g = geom.simplify(simplify_deg) if simplify_deg else geom
    if g.geom_type == "MultiPolygon":
        g = max(g.geoms, key=lambda p: p.area)
    if g.is_empty or g.geom_type != "Polygon":
        return None
    coords = [[round(x, 7), round(y, 7)] for x, y in g.exterior.coords]
    return coords if len(coords) >= 4 else None


def _mask_to_polygon_py(mask: np.ndarray, meta: dict):
    """纯 Python 边界追踪（rasterio 不可用时的兜底）。"""
    from shapely.geometry import Polygon

    h, w = mask.shape
    cs = meta["cellsize"]
    m = np.zeros((h + 2, w + 2), dtype=bool)
    m[1:-1, 1:-1] = mask
    segs: dict[tuple, list] = {}

    def add(a, b):
        segs.setdefault((round(a[0], 9), round(a[1], 9)), []).append(b)

    for i, j in np.argwhere(mask):
        yn = meta["yll"] + (h - i) * cs
        ys = meta["yll"] + (h - 1 - i) * cs
        x0 = meta["xll"] + j * cs
        x1 = x0 + cs
        if not m[i, j + 1]:
            add((x0, yn), (x1, yn))
        if not m[i + 2, j + 1]:
            add((x1, ys), (x0, ys))
        if not m[i + 1, j]:
            add((x0, ys), (x0, yn))
        if not m[i + 1, j + 2]:
            add((x1, yn), (x1, ys))

    rings = []
    for start_key in list(segs.keys()):
        while segs.get(start_key):
            ring = []
            cur = start_key
            while True:
                nxt_list = segs.get(cur)
                if not nxt_list:
                    break
                nxt = nxt_list.pop()
                ring.append((cur, nxt))
                cur = (round(nxt[0], 9), round(nxt[1], 9))
                if cur == start_key:
                    break
            if len(ring) >= 4:
                rings.append([list(k) for k, _ in ring])
    polys = [Polygon(r).buffer(0) for r in rings if len(r) >= 4]
    polys = [p for p in polys if not p.is_empty and p.area > 1e-6]
    return max(polys, key=lambda g: g.area) if polys else None


# ================================================================ 4. 雨量站权重
def _lonlat(rec: dict) -> tuple[Optional[float], Optional[float]]:
    """兼容 lon/lat 与 x/y 两种字段命名。"""
    lon = rec.get("lon", rec.get("x"))
    lat = rec.get("lat", rec.get("y"))
    return (None if lon is None else float(lon), None if lat is None else float(lat))


def _rain_weights(meta: dict, lab: np.ndarray, stations: list[dict], n_sub: int) -> dict:
    """按最近雨量站归属格网（泰森多边形思想），统计每个子流域的面积权重。"""
    stations = [
        {**s, "lon": _lonlat(s)[0], "lat": _lonlat(s)[1]}
        for s in (stations or [])
        if None not in _lonlat(s)
    ]
    if not stations:
        return {}
    h, w = lab.shape
    cs = meta["cellsize"]
    ys = meta["yll"] + (meta["nrows"] - np.arange(h) - 0.5) * cs
    xs = meta["xll"] + (np.arange(w) + 0.5) * cs
    gx, gy = np.meshgrid(xs, ys)
    kx, ky = _meters_per_deg(float(ys.mean()))
    flat_x = (gx.ravel() * kx).astype(np.float64)
    flat_y = (gy.ravel() * ky).astype(np.float64)
    sid = np.full(h * w, -1, dtype=np.int32)
    valid_lab = lab.ravel() >= 0
    pts = np.array([[(s["lon"]) * kx, (s["lat"]) * ky] for s in stations], dtype=np.float64)
    try:
        from scipy.spatial import cKDTree

        tree = cKDTree(pts)
        _d, idx = tree.query(np.column_stack([flat_x[valid_lab], flat_y[valid_lab]]), k=1)
        sid[valid_lab] = idx.astype(np.int32)
    except Exception:  # noqa: BLE001
        for k in np.nonzero(valid_lab)[0]:
            dd = (pts[:, 0] - flat_x[k]) ** 2 + (pts[:, 1] - flat_y[k]) ** 2
            sid[k] = int(np.argmin(dd))

    row_area = np.repeat(row_cell_area_km2(meta), w)
    out: dict[int, list[dict]] = {}
    sub_lab = lab.ravel()
    for k in range(n_sub):
        sel = (sub_lab == k) & valid_lab
        if not sel.any():
            continue
        ids, cnts = np.unique(sid[sel], return_counts=True)
        area_tot = float(row_area[sel].sum()) or 1.0
        items = []
        for sidx, cnt in zip(ids, cnts):
            if sidx < 0:
                continue
            mask_s = sel & (sid == sidx)
            a = float(row_area[mask_s].sum())
            items.append(
                {
                    "id": stations[sidx].get("id"),
                    "name": stations[sidx].get("name"),
                    "lon": round(float(stations[sidx]["lon"]), 6),
                    "lat": round(float(stations[sidx]["lat"]), 6),
                    "area_km2": round(a, 2),
                    "weight": round(a / area_tot, 4),
                    "inside": bool(_point_label(meta, sub_lab, w, stations[sidx]) == k),
                }
            )
        items.sort(key=lambda d: -d["weight"])
        out[k] = items
    return out


def _point_label(meta: dict, sub_lab: np.ndarray, w: int, station: dict) -> int:
    lon, lat = _lonlat(station)
    if lon is None or lat is None:
        return -1
    i, j = _row_col_of(meta, lon, lat)
    h = sub_lab.size // w
    if 0 <= i < h and 0 <= j < w:
        return int(sub_lab[i * w + j])
    return -1


# ================================================================ 主流程
def delineate_subbasins(
    dem_ctx: dict,
    topology: dict,
    outlets: Optional[list[dict]] = None,
    options: Optional[dict] = None,
) -> dict:
    """划分子流域。

    参数
    ----
    dem_ctx : dict  含 dem / meta / filled / acc（见 routers/deps.dem_context）
    topology : dict build_topology 的结果（需要 nodes / edges / stations）
    outlets : list  手工控制断面 [{name, lon, lat}]，mode='manual' 时使用
    options : dict  见 DEFAULT_SUBBASIN_OPTIONS

    返回
    ----
    {ok, options, subbasins:[...], stats:{...}, warnings:[...], labels?}
    子流域字段：code/name/area_km2/outlet(位置+汇流)/parent/children/upstream/
    river_length_km/elev/edge_ids/rain_stations/rain_weights/outlet_station
    """
    if not dem_ctx:
        return {"ok": False, "error": "子流域划分需要 DEM。请先上传 DEM 并生成河网。"}
    opts = dict(DEFAULT_SUBBASIN_OPTIONS)
    opts.update({k: v for k, v in (options or {}).items() if v is not None})
    t0 = time.time()
    warnings: list[str] = []

    dem = np.asarray(dem_ctx["dem"], dtype=np.float64)
    meta = dict(dem_ctx["meta"])
    filled = np.asarray(dem_ctx.get("filled") if dem_ctx.get("filled") is not None else dem, dtype=np.float64)
    nodata = float(meta.get("nodata", -9999.0))
    h, w = filled.shape
    acc_meta = dict(meta)
    acc_meta["accum_threshold"] = (options or {}).get("channel_accum_threshold") or meta.get("accum_threshold")

    # 流向与汇流：必须用「严格 D8 + 平地解析」重算。
    # dem_context 缓存的 acc 基于未解析平地的 D8，截断 DEM 上会出现大量假汇。
    _fdir, down_i, down_j = d8_directions_resolved(filled, nodata=nodata)
    acc = flow_accumulation_topo(down_i, down_j)
    down_flat = np.where(down_i >= 0, down_i * w + down_j, -1).ravel().tolist()

    # ---------------------------------------------------------- 1. 控制断面
    mode = opts["mode"]
    if mode == "station":
        cand = _station_outlets(topology, hydro_only=True)
        if not cand:
            warnings.append("未找到已挂接的水文站，已退化为按河网汇流节点自动分区。")
            mode = "junction"
            cand = _junction_outlets(topology, opts["min_area_km2"])
    elif mode == "junction":
        cand = _junction_outlets(topology, opts["min_area_km2"])
    elif mode == "manual":
        cand = _manual_outlets(outlets or [])
        if not cand:
            return {"ok": False, "error": "手工模式下未提供控制断面，请先在地图上绘制控制点。"}
    else:
        return {"ok": False, "error": f"未知的划分依据：{mode}"}

    if mode != "manual" and opts.get("include_outlet", True):
        # 优先用 DEM 栅格自身的出口（截断窗口下拓扑的 outlet 节点未必是真实出口）
        cand = cand + [_dem_outlet(meta, acc)]

    if not cand:
        return {"ok": False, "error": "没有可用的控制断面，无法划分子流域。"}

    # 落位到最近河道
    chan_index = build_channel_index(acc_meta, acc, opts)
    if not chan_index.get("valid"):
        return {"ok": False, "error": "DEM 中没有可识别的河道（汇流阈值过高？），无法落位控制断面。"}
    snapped: list[dict] = []
    for c in cand:
        if c.get("lon") is None or c.get("lat") is None:
            continue
        s = snap_to_channel(meta, acc, float(c["lon"]), float(c["lat"]), float(opts["snap_max_m"]), index=chan_index)
        if s is None:
            warnings.append(
                f"控制断面「{c['name']}」距最近河道超过 {float(opts['snap_max_m']) / 1000:.0f} km 或落在 DEM 之外，已忽略。"
            )
            continue
        if s["offset_m"] > 2000:
            warnings.append(
                f"控制断面「{c['name']}」偏离 DEM 河道 {s['offset_m'] / 1000:.1f} km，已吸附到最近河道格网（粗分辨率 DEM 常见，建议人工核对）。"
            )
        rec = dict(c)
        rec.update(
            {
                "cell_i": s["i"],
                "cell_j": s["j"],
                "cell_lon": s["lon"],
                "cell_lat": s["lat"],
                "cell_acc": s["acc"],
                "offset_m": s["offset_m"],
                "on_channel": s["on_channel"],
            }
        )
        snapped.append(rec)

    if not snapped:
        return {"ok": False, "error": "所有控制断面都无法吸附到河道格网，请增大吸附距离。"}

    # 同一格网去重（保留汇流更大的）
    if opts.get("merge_same_cell", True):
        best: dict[int, dict] = {}
        for r in snapped:
            key = r["cell_i"] * w + r["cell_j"]
            if key not in best or r["cell_acc"] > best[key]["cell_acc"]:
                best[key] = r
        snapped = list(best.values())
        snapped.sort(key=lambda r: -r["cell_acc"])

    # 自动分区/手工模式下按最小面积过滤（以 DEM 汇流累积为准，不依赖拓扑里的面积字段）
    if mode in ("junction", "manual") and float(opts.get("min_area_km2") or 0) > 0:
        row_area_pre = row_cell_area_km2(meta)
        mean_cell_km2 = float(row_area_pre.mean())
        keep, dropped = [], 0
        for r in snapped:
            area_km2 = float(r["cell_acc"]) * mean_cell_km2
            if r.get("source") == "dem_outlet" or area_km2 >= float(opts["min_area_km2"]):
                keep.append(r)
            else:
                dropped += 1
        if dropped:
            warnings.append(f"按最小面积 {float(opts['min_area_km2']):.0f} km² 过滤掉 {dropped} 个过小的控制断面。")
        snapped = keep
        if not snapped:
            return {"ok": False, "error": "所有候选控制断面的控制面积都小于最小面积阈值。"}

    # ---------------------------------------------------------- 2. 标签传播
    seed_flat = [r["cell_i"] * w + r["cell_j"] for r in snapped]
    lab = _propagate_labels(acc, down_flat, seed_flat)
    lab_flat = lab.ravel().tolist()
    n_sub = len(snapped)

    # ---------------------------------------------------------- 3. 父级关系
    parents = [_trace_parent(lab_flat, down_flat, seed_flat[k]) for k in range(n_sub)]

    # ---------------------------------------------------------- 4. 统计
    row_area = row_cell_area_km2(meta)
    flat_area = np.repeat(row_area, w)
    lab_flat_arr = lab.ravel()
    valid_lab = lab_flat_arr >= 0
    counts = np.bincount(lab_flat_arr[valid_lab], minlength=n_sub)
    areas = np.bincount(lab_flat_arr[valid_lab], weights=flat_area[valid_lab], minlength=n_sub)
    fill_flat = filled.ravel()
    valid_filled = np.isfinite(fill_flat) & (np.abs(fill_flat - nodata) > 1e-6)
    sel_elev = valid_lab & valid_filled
    elev_sum = np.bincount(lab_flat_arr[sel_elev], weights=fill_flat[sel_elev], minlength=n_sub)
    elev_min = np.full(n_sub, np.nan)
    elev_max = np.full(n_sub, np.nan)
    for k in range(n_sub):
        sel = (lab_flat_arr == k) & valid_filled
        if sel.any():
            v = fill_flat[sel]
            elev_min[k], elev_max[k] = float(v.min()), float(v.max())

    # 河段归属：按河段下游端点所在子流域判定
    node_pos = {n.get("id"): (n.get("x"), n.get("y")) for n in topology.get("nodes") or []}
    edge_ids: list[list[str]] = [[] for _ in range(n_sub)]
    edge_len: list[float] = [0.0 for _ in range(n_sub)]
    edge_sub_idx: dict[str, int] = {}  # 河段 id → 标签下标（编号在算出 rank 后统一换算）
    for e in topology.get("edges") or []:
        xy = node_pos.get(e.get("to_node")) or node_pos.get(e.get("from_node"))
        if not xy or xy[0] is None:
            continue
        i, j = _row_col_of(meta, float(xy[0]), float(xy[1]))
        if not (0 <= i < h and 0 <= j < w):
            continue
        k = int(lab[i, j])
        if k < 0:
            # 端点落在子流域外（如流域边界修正），退化为取河段中点
            cs = e.get("coords") or []
            if cs:
                mid = cs[len(cs) // 2]
                i2, j2 = _row_col_of(meta, float(mid[0]), float(mid[1]))
                if 0 <= i2 < h and 0 <= j2 < w:
                    k = int(lab[i2, j2])
        if k < 0:
            continue
        edge_ids[k].append(e.get("id"))
        edge_len[k] += float(e.get("length_m") or 0.0)
        edge_sub_idx[e.get("id")] = k

    # 站点归属
    hydro_by_sub: list[list[dict]] = [[] for _ in range(n_sub)]
    for s in topology.get("stations") or []:
        if s.get("x") is None:
            continue
        i, j = _row_col_of(meta, float(s["x"]), float(s["y"]))
        if not (0 <= i < h and 0 <= j < w):
            continue
        k = int(lab[i, j])
        if k >= 0:
            hydro_by_sub[k].append({"id": s.get("id"), "name": s.get("name"), "type": s.get("type"), "attached": s.get("attached")})

    rain_list = [s for s in (topology.get("stations") or []) if s.get("type") == "rain" and s.get("x") is not None]
    rain_weights = _rain_weights(meta, lab, rain_list, n_sub) if opts.get("compute_rain_weights") else {}

    # ---------------------------------------------------------- 5. 出口站识别
    mean_cell_km2_pre = float(row_area.mean()) if row_area.size else 0.0
    outlet_station: list[Optional[dict]] = [None] * n_sub
    for k, r in enumerate(snapped):
        # 以站点为控制断面时直接对应；其他来源（汇流节点/DEM 出口）只认贴在一起的站点
        if r.get("source") == "station" and r.get("id"):
            outlet_station[k] = {
                "id": r.get("id"),
                "name": r.get("name"),
                "distance_m": round(float(r.get("offset_m") or 0.0), 1),
                "matched": "control",
            }
            continue
        best, bd = None, 1e9
        for s in topology.get("stations") or []:
            if s.get("type") != "hydro" or s.get("x") is None:
                continue
            d = planar_distance_m(meta, r["cell_lon"], r["cell_lat"], float(s["x"]), float(s["y"]))
            if d < bd:
                best, bd = s, d
        if best is not None and bd <= 1000.0:
            outlet_station[k] = {
                "id": best.get("id"),
                "name": best.get("name"),
                "distance_m": round(bd, 1),
                "matched": "nearby",
            }

    # ---------------------------------------------------------- 6. 排水序（上游 → 下游）
    depth = [0] * n_sub
    for k in range(n_sub):
        d, cur, seen = 0, k, set()
        while parents[cur] >= 0 and cur not in seen and d < n_sub + 2:
            seen.add(cur)
            cur = parents[cur]
            d += 1
        depth[k] = d
    order = sorted(range(n_sub), key=lambda k: (-depth[k], -areas[k]))
    rank = {k: idx for idx, k in enumerate(order)}
    children: list[list[int]] = [[] for _ in range(n_sub)]
    for k, p in enumerate(parents):
        if p >= 0 and p != k:
            children[p].append(k)
    for k in range(n_sub):
        children[k].sort(key=lambda x: rank[x])

    # 河段 → 单元编号：编号按排水序 rank 生成，不能用标签下标（两者不等价）
    edge_sub: dict[str, str] = {eid: f"S{rank[k] + 1:02d}" for eid, k in edge_sub_idx.items()}

    # ---------------------------------------------------------- 7. 组装
    # 累计控制面积：单元面积 + 全部上游子流域面积（逐级向上汇总）
    cum_area = [float(a) for a in areas]
    for k in sorted(range(n_sub), key=lambda x: depth[x]):
        p = parents[k]
        if p >= 0 and p != k:
            cum_area[p] += cum_area[k]
    mean_cell_km2 = float(row_area.mean()) if row_area.size else 0.0

    subbasins = []
    for k in order:
        base = snapped[k]
        code = f"S{rank[k] + 1:02d}"
        up = sorted(
            [f"S{rank[c] + 1:02d}" for c in range(n_sub) if parents[c] == k], key=lambda s: s
        )
        subbasins.append(
            {
                "code": code,
                "name": f"{opts['name_prefix']}{rank[k] + 1}",
                "area_km2": round(float(areas[k]), 2),
                "upstream_area_km2": round(float(cum_area[k]), 2),
                "cell_count": int(counts[k]),
                "outlet": {
                    "lon": round(base["cell_lon"], 7),
                    "lat": round(base["cell_lat"], 7),
                    "source": base.get("source"),
                    "control": base.get("name"),
                    "control_id": base.get("id"),
                    "offset_m": base.get("offset_m"),
                    "accum_cells": int(base["cell_acc"]),
                    "accum_area_km2": round(float(base["cell_acc"]) * mean_cell_km2, 2),
                },
                "outlet_station": outlet_station[k],
                "stations": hydro_by_sub[k],
                "parent": f"S{rank[parents[k]] + 1:02d}" if parents[k] >= 0 else None,
                "children": [f"S{rank[c] + 1:02d}" for c in children[k]],
                "upstream_subbasins": up,
                "order_index": rank[k] + 1,
                "chain_depth": depth[k],
                "river_length_km": round(edge_len[k] / 1000.0, 2),
                "edge_count": len(edge_ids[k]),
                "edge_ids": edge_ids[k],
                "elev_min_m": None if math.isnan(elev_min[k]) else round(float(elev_min[k]), 1),
                "elev_max_m": None if math.isnan(elev_max[k]) else round(float(elev_max[k]), 1),
                "elev_mean_m": None
                if counts[k] == 0
                else round(float(elev_sum[k] / max(1, int(counts[k]))), 1),
                "relief_m": None
                if math.isnan(elev_min[k])
                else round(float(elev_max[k] - elev_min[k]), 1),
                "rain_stations": rain_weights.get(k, []),
                "boundary": None,  # 下面填充
            }
        )

    # 边界多边形
    for i, sb in enumerate(subbasins):
        k = order[i]
        mask = lab == k
        poly = _mask_to_polygon(mask, meta, float(opts["simplify_deg"]))
        sb["boundary"] = poly
        if poly is None:
            warnings.append(f"{sb['code']} 边界提取失败（可能是零散格网）。")

    unassigned_cells = int((lab < 0).sum())
    unassigned_km2 = float(flat_area[lab.ravel() < 0].sum())
    total_area = float(areas.sum())
    stats = {
        "subbasin_count": n_sub,
        "total_area_km2": round(total_area, 2),
        "basin_area_km2": round(total_area + unassigned_km2, 2),
        "unassigned_area_km2": round(unassigned_km2, 2),
        "unassigned_cells": unassigned_cells,
        "max_order": max([sb["order_index"] for sb in subbasins], default=0),
        "mode": mode,
        "elapsed_s": round(time.time() - t0, 2),
    }
    return {
        "ok": True,
        "options": opts,
        "subbasins": subbasins,
        "stats": stats,
        "edge_subbasin": edge_sub,
        "warnings": warnings,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


# ================================================================ 导出
def subbasin_to_geojson(result: dict, topology: Optional[dict] = None) -> dict:
    """子流域结果 → GeoJSON（面 + 出口点），供地图渲染与 SHP 导出。"""
    feats: list[dict] = []
    for sb in result.get("subbasins") or []:
        ring = sb.get("boundary")
        if ring:
            feats.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [ring]},
                    "properties": {
                        "kind": "subbasin",
                        "code": sb["code"],
                        "name": sb["name"],
                        "area_km2": sb["area_km2"],
                        "order_index": sb["order_index"],
                        "parent": sb["parent"],
                        "river_km": sb["river_length_km"],
                        "outlet_station": (sb.get("outlet_station") or {}).get("name"),
                        "elev_min_m": sb["elev_min_m"],
                        "elev_max_m": sb["elev_max_m"],
                        "属性": f"{sb['code']} {sb['name']} {sb['area_km2']}km²",
                    },
                }
            )
        feats.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [sb["outlet"]["lon"], sb["outlet"]["lat"]]},
                "properties": {
                    "kind": "subbasin_outlet",
                    "code": sb["code"],
                    "name": (sb.get("outlet_station") or {}).get("name") or sb["name"],
                    "area_km2": sb["area_km2"],
                    "control": sb["outlet"].get("control"),
                    "属性": f"{sb['code']} 出口",
                },
            }
        )
    return {"type": "FeatureCollection", "features": feats}


def report_rows(result: dict) -> list[dict]:
    """报表行（CSV / 前端表格）。"""
    rows = []
    for sb in result.get("subbasins") or []:
        rows.append(
            {
                "编号": sb["code"],
                "名称": sb["name"],
                "面积km2": sb["area_km2"],
                "出口控制断面": (sb.get("outlet") or {}).get("control"),
                "出口水文站": (sb.get("outlet_station") or {}).get("name") or "",
                "上级子流域": sb["parent"] or "",
                "上游子流域": " ".join(sb["upstream_subbasins"]),
                "河道长度km": sb["river_length_km"],
                "河段数": sb["edge_count"],
                "最低高程m": sb["elev_min_m"],
                "最高高程m": sb["elev_max_m"],
                "平均高程m": sb["elev_mean_m"],
                "汇流格数": sb["outlet"]["accum_cells"],
            }
        )
    return rows
