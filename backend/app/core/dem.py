"""DEM 水文分析流水线。

流程：读栅格 → 填洼（Priority-Flood，带抬升梯度）→ D8 流向 → 汇流累积
     → 阈值提取河网 → 栅格转矢量 → Strahler 分级 → 晕渲底图

纯 numpy + scipy 实现，无需 GDAL。
"""
from __future__ import annotations

import heapq
import math
import struct
import zlib
from pathlib import Path
from typing import Optional

import numpy as np
from scipy import ndimage
from shapely.geometry import LineString

from .shp_io import length_m_planar

# D8 编码（与 ArcGIS 一致）：1=E 2=SE 4=S 8=SW 16=W 32=NW 64=N 128=NE
D8 = [
    (0, 1, 1.0, 1),
    (1, 1, math.sqrt(2), 2),
    (1, 0, 1.0, 4),
    (1, -1, math.sqrt(2), 8),
    (0, -1, 1.0, 16),
    (-1, -1, math.sqrt(2), 32),
    (-1, 0, 1.0, 64),
    (-1, 1, math.sqrt(2), 128),
]


# ---------------------------------------------------------------- 栅格读写
def read_ascii_grid(path: Path) -> tuple[np.ndarray, dict]:
    """读取 ESRI ASCII Grid。"""
    with path.open("r", encoding="utf-8", errors="replace") as f:
        head = {}
        for _ in range(6):
            line = f.readline()
            if not line:
                break
            parts = line.split()
            if len(parts) >= 2:
                head[parts[0].lower()] = float(parts[1])
            else:
                break
        rest = f.read()
    ncols = int(head["ncols"])
    nrows = int(head["nrows"])
    nodata = head.get("nodata_value", -9999.0)
    data = np.array(rest.split(), dtype=np.float64)
    if data.size != nrows * ncols:
        data = data.reshape(-1)[: nrows * ncols]
    data = data.reshape(nrows, ncols)
    meta = {
        "ncols": ncols,
        "nrows": nrows,
        "xll": head.get("xllcorner", head.get("xllcenter", 0.0)),
        "yll": head.get("yllcorner", head.get("yllcenter", 0.0)),
        "cellsize": head.get("cellsize", 1.0),
        "nodata": nodata,
    }
    return data.astype(np.float64), meta


def write_ascii_grid(path: Path, dem: np.ndarray, meta: dict) -> None:
    h, w = dem.shape
    nodata = meta.get("nodata", -9999.0)
    out = np.where(np.isfinite(dem), dem, nodata)
    with path.open("w", encoding="utf-8") as f:
        f.write(f"ncols {w}\nnrows {h}\n")
        f.write(f"xllcorner {meta['xll']:.8f}\nyllcorner {meta['yll']:.8f}\n")
        f.write(f"cellsize {meta['cellsize']:.8f}\nNODATA_value {nodata}\n")
        # 精度必须足以保留填洼的 eps 抬升梯度（默认 1e-4 m），
        # 早期用 "%.2f" 会把梯度抹平，读回后平地上处处是「假洼地」，流向断裂。
        fmt = "%.6f"
        f.write("\n".join(" ".join(fmt % v for v in row) for row in out))
        f.write("\n")


def read_dem(path: Path) -> tuple[np.ndarray, dict]:
    """读取 DEM：支持 .asc/.txt（内置），.tif/.tiff（需 rasterio）。"""
    suffix = path.suffix.lower()
    if suffix in (".asc", ".txt", ".grd"):
        return read_ascii_grid(path)
    if suffix in (".tif", ".tiff", ".img"):
        try:
            import rasterio  # type: ignore
        except ImportError as e:  # noqa: BLE001
            raise RuntimeError(
                "读取 GeoTIFF 需要 rasterio。请安装：pip install rasterio，"
                "或先用 GIS 工具把 DEM 另存为 ESRI ASCII Grid(.asc)"
            ) from e
        with rasterio.open(path) as ds:
            band = ds.read(1).astype(np.float64)
            nodata = ds.nodata
            t = ds.transform
            # 常规北朝上栅格 t.e < 0：左上角 y=t.f，南边界 = t.f + nrows*t.e
            yll = t.f + ds.height * t.e if t.e < 0 else t.f
            meta = {
                "ncols": ds.width,
                "nrows": ds.height,
                "xll": t.c,
                "yll": yll,
                "cellsize": abs(t.a),
                "nodata": nodata if nodata is not None else -9999.0,
                "crs": str(ds.crs),
            }
        return band, meta
    raise RuntimeError(f"不支持的 DEM 格式: {suffix}")


def write_raster_cache(path: Path, arr: np.ndarray) -> None:
    """无损栅格缓存（.npy）。ASCII Grid 的定点精度会破坏填洼梯度，勿用于中间量。"""
    np.save(path, np.asarray(arr, dtype=np.float64))


def read_raster_cache(path: Path) -> Optional[np.ndarray]:
    if not path.exists():
        return None
    try:
        return np.asarray(np.load(path), dtype=np.float64)
    except Exception:  # noqa: BLE001
        return None


def cell_size_m(meta: dict) -> tuple[float, float]:
    """栅格单元在经/纬方向的米数（以中心纬度计）。"""
    cs = meta["cellsize"]
    if abs(cs) > 0.5:  # 已是投影坐标（米）
        return cs, cs
    lat = meta["yll"] + meta["nrows"] * cs / 2
    return cs * 111320.0 * math.cos(math.radians(lat)), cs * 110540.0


def cell_xy(meta: dict, i: int, j: int) -> tuple[float, float]:
    """行列号 → 坐标（格心，北向上）。"""
    cs = meta["cellsize"]
    x = meta["xll"] + (j + 0.5) * cs
    y = meta["yll"] + (meta["nrows"] - i - 0.5) * cs
    return x, y


# ---------------------------------------------------------------- 填洼
_D8_IJ = [(di, dj) for di, dj, _d, _c in D8]


def fill_depressions(dem: np.ndarray, nodata: float = -9999.0, eps: float = 1e-4) -> np.ndarray:
    """Priority-Flood 填洼（Barnes et al. 2014），带抬升梯度以消除平底。

    热循环使用 Python 扁平列表（比 numpy 标量索引快一个数量级）。
    """
    h, w = dem.shape
    n = h * w
    src = dem.ravel()
    valid_np = np.isfinite(src) & (np.abs(src - nodata) > 1e-6)
    flat = np.where(valid_np, src, np.inf).tolist()
    valid = valid_np.tolist()

    processed = bytearray(n)
    pq: list[tuple[float, int]] = []

    for i in range(h):
        for j in (0, w - 1):
            idx = i * w + j
            if valid[idx]:
                pq.append((flat[idx], idx))
    for j in range(w):
        for i in (0, h - 1):
            idx = i * w + j
            if valid[idx]:
                pq.append((flat[idx], idx))
    heapq.heapify(pq)

    push = heapq.heappush
    pop = heapq.heappop
    while pq:
        zz, idx = pop(pq)
        if processed[idx]:
            continue
        # 堆中可能残留过期（偏小）的键，以当前高程为准重新入堆，保证处理顺序严格递增
        zc = flat[idx]
        if zz < zc:
            push(pq, (zc, idx))
            continue
        processed[idx] = 1
        i, j = divmod(idx, w)
        for di, dj in _D8_IJ:
            ni = i + di
            nj = j + dj
            if 0 <= ni < h and 0 <= nj < w:
                nidx = ni * w + nj
                if valid[nidx] and not processed[nidx]:
                    nz = flat[nidx]
                    if nz <= zc:
                        nz = zc + eps
                        flat[nidx] = nz
                    push(pq, (nz, nidx))

    out = np.array(flat, dtype=np.float64).reshape(h, w)
    out[~valid_np.reshape(h, w)] = nodata
    return out


# ---------------------------------------------------------------- 流向 / 累积
def _shift(a: np.ndarray, di: int, dj: int, fill: float) -> np.ndarray:
    out = np.full(a.shape, fill, dtype=np.float64)
    h, w = a.shape
    ti = slice(max(0, -di), h - max(0, di))
    tj = slice(max(0, -dj), w - max(0, dj))
    si = slice(max(0, -di) + di, h - max(0, di) + di)
    sj = slice(max(0, -dj) + dj, w - max(0, dj) + dj)
    out[ti, tj] = a[si, sj]
    return out


def d8_directions(dem: np.ndarray, nodata: float = -9999.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """计算 D8 流向。

    返回 (fdir, down_i, down_j)；fdir=0 表示无下游（出口/无效格），
    down_i/down_j 为下游行列号，-1 表示无下游。
    """
    h, w = dem.shape
    valid = np.isfinite(dem) & (np.abs(dem - nodata) > 1e-6)
    best = np.full(dem.shape, -np.inf)
    fdir = np.zeros(dem.shape, dtype=np.int16)

    for di, dj, dist, code in D8:
        nb = _shift(dem, di, dj, np.inf)
        nb_valid = _shift(valid.astype(np.float64), di, dj, 0.0) > 0.5
        slope = (dem - nb) / dist
        slope = np.where(nb_valid & valid, slope, -np.inf)
        better = slope > best
        best = np.where(better, slope, best)
        fdir = np.where(better, code, fdir)

    fdir = np.where((best > 0) & valid, fdir, 0).astype(np.int16)

    down_i = np.full(dem.shape, -1, dtype=np.int32)
    down_j = np.full(dem.shape, -1, dtype=np.int32)
    for di, dj, _dist, code in D8:
        m = fdir == code
        if not m.any():
            continue
        ii, jj = np.nonzero(m)
        ti, tj = ii + di, jj + dj
        ok = (ti >= 0) & (ti < h) & (tj >= 0) & (tj < w)
        down_i[ii[ok], jj[ok]] = ti[ok]
        down_j[ii[ok], jj[ok]] = tj[ok]
    return fdir, down_i, down_j


def resolve_flat_directions(
    dem_filled: np.ndarray,
    down_i: np.ndarray,
    down_j: np.ndarray,
    nodata: float = -9999.0,
    tol: float = 0.05,
) -> tuple[np.ndarray, np.ndarray]:
    """解析平地（flat）上的流向。

    D8 要求严格下降方向，填洼后的平地与截断 DEM 边缘会出现「无下游」的假汇，
    使汇流累积在流域内部断裂（曾实测到 1457 个高累积假汇）。这里用一次
    多源 BFS 把流向"接"回已有排水网络：从所有已知下游的格网出发，向四周
    不高于当前格网（含 `tol` 容差）的邻居扩散，把它们指向当前格网。

    返回修补后的 (down_i, down_j)；仍无下游的格网即真正的流域出口 / 边缘。
    """
    from collections import deque

    h, w = dem_filled.shape
    src = dem_filled.ravel()
    valid = (np.isfinite(src) & (np.abs(src - nodata) > 1e-6)).tolist()
    z = src.tolist()
    fd = np.where(down_i >= 0, down_i * w + down_j, -1).ravel().tolist()
    resolved = [v >= 0 for v in fd]
    dq: deque[int] = deque(i for i, v in enumerate(resolved) if v)

    while dq:
        idx = dq.popleft()
        i, j = divmod(idx, w)
        zi = z[idx]
        for di, dj in _D8_IJ:
            ni, nj = i + di, j + dj
            if ni < 0 or ni >= h or nj < 0 or nj >= w:
                continue
            nidx = ni * w + nj
            if resolved[nidx] or not valid[nidx]:
                continue
            if z[nidx] >= zi - tol:  # 邻居可以流向当前格网（不低于，含容差）
                fd[nidx] = idx
                resolved[nidx] = True
                dq.append(nidx)

    out = np.array(fd, dtype=np.int64)
    oi = np.where(out >= 0, out // w, -1).astype(np.int32).reshape(h, w)
    oj = np.where(out >= 0, out % w, -1).astype(np.int32).reshape(h, w)
    return oi, oj


def d8_directions_resolved(
    dem_filled: np.ndarray, nodata: float = -9999.0, tol: float = 0.05
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """严格 D8 + 平地流向解析，得到处处连通（除真实出口）的流向场。"""
    fdir, di, dj = d8_directions(dem_filled, nodata=nodata)
    ri, rj = resolve_flat_directions(dem_filled, di, dj, nodata=nodata, tol=tol)
    fixed = int(((di < 0) & (ri >= 0)).sum())
    return fdir, ri, rj


def flow_accumulation_topo(
    down_i: np.ndarray, down_j: np.ndarray, nodata_cells: Optional[np.ndarray] = None
) -> np.ndarray:
    """按**拓扑序**计算汇流累积（上游格数，含自身）。

    相比按高程降序的版本，这里用入度 + 队列处理，对填洼后的大片等值平地也正确
    （高程相同时排序不定会导致上游贡献漏加，截断 DEM 上尤其明显）。
    """
    from collections import deque

    h, w = down_i.shape
    n = h * w
    flat_down = np.where(down_i >= 0, down_i * w + down_j, -1).ravel()
    acc = np.ones(n, dtype=np.float64)
    indeg = np.bincount(flat_down[flat_down >= 0], minlength=n)
    dq: deque[int] = deque(int(i) for i in np.flatnonzero(indeg == 0))
    fd = flat_down.tolist()
    inc = indeg.tolist()
    a = acc.tolist()
    while dq:
        idx = dq.popleft()
        d = fd[idx]
        if d >= 0:
            a[d] += a[idx]
            inc[d] -= 1
            if inc[d] == 0:
                dq.append(d)
    return np.array(a, dtype=np.float64).reshape(h, w)


def flow_accumulation(dem_filled: np.ndarray, down_i: np.ndarray, down_j: np.ndarray) -> np.ndarray:
    """汇流累积量（单位：上游格数，含自身）。"""
    h, w = dem_filled.shape
    flat_down = np.where(down_i >= 0, down_i * w + down_j, -1).ravel().tolist()
    order = np.argsort(-dem_filled.ravel(), kind="stable").tolist()
    acc = [1.0] * (h * w)
    for idx in order:
        d = flat_down[idx]
        if d >= 0:
            acc[d] += acc[idx]
    return np.array(acc, dtype=np.float64).reshape(h, w)


def sample_accumulation(acc: np.ndarray, meta: dict, lon: float, lat: float, win: int = 3) -> float:
    """按坐标取汇流累积值（窗口取最大，避免落在河道旁）。"""
    cs = meta["cellsize"]
    j = int(round((lon - meta["xll"]) / cs - 0.5))
    i = int(round((meta["nrows"] - 0.5) - (lat - meta["yll"]) / cs))
    h, w = acc.shape
    if not (0 <= i < h and 0 <= j < w):
        return 0.0
    i0, i1 = max(0, i - win), min(h, i + win + 1)
    j0, j1 = max(0, j - win), min(w, j + win + 1)
    return float(acc[i0:i1, j0:j1].max())


# ---------------------------------------------------------------- 河网提取与矢量化
def extract_stream_mask(acc: np.ndarray, threshold: float, dem: np.ndarray, nodata: float = -9999.0) -> np.ndarray:
    valid = np.isfinite(dem) & (np.abs(dem - nodata) > 1e-6)
    return (acc >= threshold) & valid


def _chaikin(coords: list, n: int = 2, keep_ends: bool = True) -> list:
    """Chaikin 拐角切割，使河网更接近自然形态。"""
    if len(coords) < 3:
        return coords
    pts = [np.asarray(c, dtype=float) for c in coords]
    for _ in range(n):
        new = [pts[0]] if keep_ends else []
        for a, b in zip(pts[:-1], pts[1:]):
            new.append(a * 0.75 + b * 0.25)
            new.append(a * 0.25 + b * 0.75)
        if keep_ends:
            new.append(pts[-1])
        pts = new
    return [[float(p[0]), float(p[1])] for p in pts]


def vectorize_streams(
    mask: np.ndarray,
    dem_filled: np.ndarray,
    acc: np.ndarray,
    down_i: np.ndarray,
    down_j: np.ndarray,
    meta: dict,
    simplify_m: float = 120.0,
    smooth: bool = True,
    min_length_m: float = 0.0,
) -> list[dict]:
    """把河网栅格转成"节点-弧段"式的河段矢量。

    注意：min_length_m 默认为 0 —— 任何河段被丢弃都会切断上下游连通性，
    因此除非明确需要，不要过滤短河段。

    返回 [{coords, order, length_m, upstream_area_km2, start_xy, end_xy, mean_elev, ncell}]
    """
    h, w = mask.shape
    flat_down = np.where(down_i >= 0, down_i * w + down_j, -1).ravel()
    stream_flat = np.flatnonzero(mask.ravel())
    if stream_flat.size == 0:
        return []

    in_stream = np.zeros(h * w, dtype=bool)
    in_stream[stream_flat] = True

    ds = flat_down[stream_flat]
    ds_valid = np.where((ds >= 0) & in_stream[np.maximum(ds, 0)], ds, -1)
    indeg = np.bincount(ds_valid[ds_valid >= 0], minlength=h * w)

    # 起点：河源（无上游）、汇流点（≥2 上游）、栅格边界出口
    si, sj = np.divmod(stream_flat, w)
    edge = (si == 0) | (si == h - 1) | (sj == 0) | (sj == w - 1)
    is_start = (indeg[stream_flat] == 0) | (indeg[stream_flat] >= 2) | edge | (ds_valid < 0)
    starts = stream_flat[is_start]

    interior = np.zeros(h * w, dtype=bool)
    is_start_arr = np.zeros(h * w, dtype=bool)
    is_start_arr[starts] = True

    segments: list[list[int]] = []
    for s in starts:
        if interior[s]:
            continue
        path = [int(s)]
        cur = int(s)
        for _ in range(h * w):
            d = int(flat_down[cur])
            if d < 0 or not in_stream[d]:
                break
            path.append(d)
            if is_start_arr[d]:
                break
            interior[d] = True
            cur = d
        if len(path) >= 2:
            segments.append(path)

    if not segments:
        return []

    # ---- 拓扑排序后用 Strahler 法分级
    mean_elev = np.array([float(dem_filled.ravel()[p].mean()) for p in segments])
    ends_at: dict[int, list[int]] = {}
    for k, p in enumerate(segments):
        ends_at.setdefault(p[-1], []).append(k)

    order = np.zeros(len(segments), dtype=int)
    for k in np.argsort(-mean_elev):
        kids = ends_at.get(segments[k][0], [])
        if not kids:
            order[k] = 1
        else:
            ko = [int(order[c]) for c in kids]
            order[k] = ko[0] + 1 if len(set(ko)) == 1 else max(ko)

    # ---- 生成坐标
    mdx, mdy = cell_size_m(meta)
    cell_area_km2 = abs(mdx * mdy) / 1e6
    out: list[dict] = []
    for k, p in enumerate(segments):
        pts = [cell_xy(meta, *divmod(int(c), w)) for c in p]
        line = LineString(pts)
        if simplify_m and simplify_m > 0 and len(pts) > 4:
            tol = simplify_m if abs(meta["cellsize"]) > 0.5 else simplify_m / 111320.0
            line = line.simplify(tol, preserve_topology=False)
            pts = [list(c) for c in line.coords]
        if smooth and len(pts) >= 3:
            pts = _chaikin(pts, 2)
        length = length_m_planar(pts)
        if length < min_length_m:
            continue
        out.append(
            {
                "coords": pts,
                "order": int(order[k]),
                "length_m": round(length, 1),
                "upstream_area_km2": round(float(acc.ravel()[p[-1]]) * cell_area_km2, 3),
                "mean_elev": round(float(dem_filled.ravel()[p].mean()), 1),
                "start_xy": list(pts[0]),
                "end_xy": list(pts[-1]),
                "end_cell": [int(p[-1] // w), int(p[-1] % w)],
                "ncell": len(p),
            }
        )
    return out


def extract_network(
    dem: np.ndarray,
    meta: dict,
    threshold: float = 1500,
    simplify_m: float = 120.0,
    smooth: bool = True,
    nodata: Optional[float] = None,
) -> dict:
    """完整流水线，返回河段矢量与中间统计量。"""
    nodata = meta.get("nodata", -9999.0) if nodata is None else nodata
    filled = fill_depressions(dem, nodata=nodata)
    fdir, di, dj = d8_directions_resolved(filled, nodata=nodata)
    acc = flow_accumulation_topo(di, dj)
    mask = extract_stream_mask(acc, threshold, dem, nodata=nodata)
    segs = vectorize_streams(mask, filled, acc, di, dj, meta, simplify_m=simplify_m, smooth=smooth)
    return {
        "filled": filled,
        "fdir": fdir,
        "down_i": di,
        "down_j": dj,
        "acc": acc,
        "mask": mask,
        "segments": segs,
        "threshold": threshold,
    }


# ---------------------------------------------------------------- 晕渲底图
def hillshade(dem: np.ndarray, az: float = 315.0, alt: float = 45.0, z_factor: float = 1.0) -> np.ndarray:
    """标准山体阴影，返回 0~1。"""
    dy, dx = np.gradient(np.where(np.isfinite(dem), dem, np.nan))
    dx = np.nan_to_num(dx)
    dy = np.nan_to_num(dy)
    slope = np.pi / 2 - np.arctan(np.hypot(dx, dy) * z_factor)
    aspect = np.arctan2(-dx, dy)
    azr, altr = math.radians(360 - az + 90), math.radians(alt)
    hs = np.sin(altr) * np.sin(slope) + np.cos(altr) * np.cos(slope) * np.cos(azr - aspect)
    return np.clip(hs, 0, 1)


def hypsometric(rel: np.ndarray) -> np.ndarray:
    """高程配色（青→绿→黄→棕→白），输入 0~1，返回 HxWx3 uint8。"""
    stops = np.array(
        [
            [56, 116, 92],
            [110, 158, 96],
            [178, 192, 108],
            [216, 196, 130],
            [196, 158, 112],
            [230, 230, 230],
        ],
        dtype=float,
    ) / 255.0
    n = len(stops) - 1
    x = np.clip(rel, 0, 1) * n
    i0 = np.clip(np.floor(x).astype(int), 0, n - 1)
    t = (x - i0)[..., None]
    rgb = stops[i0] * (1 - t) + stops[i0 + 1] * t
    return (np.clip(rgb, 0, 1) * 255).astype(np.uint8)


def write_png(path: Path, rgb: np.ndarray) -> None:
    """极简 PNG 写出（无第三方依赖）。"""
    h, w, _ = rgb.shape
    data = rgb.astype(np.uint8)
    raw = bytearray()
    for i in range(h):
        raw.append(0)
        raw.extend(data[i].tobytes())

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 6))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


def make_relief(dem: np.ndarray, meta: dict, out_png: Path, max_size: int = 1400) -> dict:
    """生成带山体阴影的高程配色底图。"""
    h, w = dem.shape
    step = max(1, int(math.ceil(max(h, w) / max_size)))
    sub = dem[::step, ::step]
    valid = np.isfinite(sub) & (np.abs(sub - meta.get("nodata", -9999)) > 1e-6)
    if not valid.any():
        return {}
    vmin, vmax = float(sub[valid].min()), float(sub[valid].max())
    rel = np.zeros_like(sub)
    rel[valid] = (sub[valid] - vmin) / max(1e-6, vmax - vmin)
    rgb = hypsometric(rel).astype(np.float64)
    hs = hillshade(np.where(valid, sub, np.nan))
    shade = 0.55 + 0.65 * hs
    rgb = rgb * shade[..., None]
    rgb[~valid] = [255, 255, 255]
    write_png(out_png, np.clip(rgb, 0, 255).astype(np.uint8))

    cs = meta["cellsize"] * step
    bounds = [
        meta["xll"],
        meta["yll"],
        meta["xll"] + meta["ncols"] * meta["cellsize"],
        meta["yll"] + meta["nrows"] * meta["cellsize"],
    ]
    return {
        "bounds": [round(b, 7) for b in bounds],
        "elev_min": round(vmin, 1),
        "elev_max": round(vmax, 1),
        "width": int(sub.shape[1]),
        "height": int(sub.shape[0]),
        "cellsize": cs,
        "step": step,
    }
