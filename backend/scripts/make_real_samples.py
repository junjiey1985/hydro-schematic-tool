# -*- coding: utf-8 -*-
"""制备堵河流域真实数据样例。

数据来源：
- DEM：AWS Terrain Tiles（开源 SRTM/ETOPO 混合地形，terrarium 编码），z11 瓦片拼接
- 河网：由真实 DEM 经填洼/D8/汇流累积提取
- 水文站/雨量站：真实站名与位置（黄龙滩 110°33'E 32°41'N、竹山等），镇点坐标优先取 OSM
- 湖泊水库：黄龙滩/潘口等水库真实轮廓（OSM）
- 流域边界：由 DEM 对黄龙滩出口做汇水区划生成的真实边界

输出到 data/samples（samples.json 清单与 /api/projects/samples/import 对接）。
"""
import json
import math
import sys
import time
import urllib.request
import urllib.parse
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import dem as demmod  # noqa: E402
from app.core.shp_io import write_shapefile  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "data" / "samples"

# ---------------------------------------------------------------- 堵河流域范围
LON0, LON1 = 109.45, 111.05
LAT0, LAT1 = 31.40, 32.85
Z = 11

# 出口：黄龙滩水文站（110°33'E, 32°41'N，控制面积 10668 km²）
OUTLET_LON, OUTLET_LAT = 110.550, 32.6833

HYDRO_STATIONS = [
    {"name": "黄龙滩站", "lon": OUTLET_LON, "lat": OUTLET_LAT, "area_km2": 10668, "note": "堵河控制站/黄龙滩水库出库站"},
    {"name": "竹山站", "lon": 110.227, "lat": 32.224, "area_km2": None, "note": "两河口下游干流站"},
]

# 雨量站：流域内真实城镇/报汛点（坐标优先从 OSM place 节点取）
RAIN_NAMES = [
    ("大九湖", 110.000, 31.470),
    ("竹溪", 109.717, 32.317),
    ("泉溪", 109.900, 31.980),
    ("向坝", 109.950, 31.700),
    ("丰溪", 109.850, 31.760),
    ("得胜", 110.050, 32.430),
    ("宝丰", 110.150, 32.350),
    ("官渡", 110.100, 31.800),
    ("上庸", 110.170, 32.170),
    ("上龛", 110.300, 31.620),
    ("门古", 110.100, 31.930),
    ("楼台", 110.330, 32.350),
]

LAKE_NAMES = ["黄龙滩水库", "潘口水库", "龙背湾水库", "小漩水库", "霍河水库", "鄂坪水库", "周家垭水库"]

BBOX_OSM = (31.35, 109.40, 32.90, 111.10)


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ================================================================ DEM
def lat_of_row(row: float, n: int) -> float:
    """Web Mercator 行号（北起 0）-> 纬度。"""
    y = 1 - (row / n) * 2
    return np.degrees(np.arctan(np.sinh(np.pi * y)))


def row_of_lat(lat: float, n: int) -> float:
    """纬度 -> Web Mercator 行号。"""
    rad = np.radians(lat)
    return (1 - np.log(np.tan(rad) + 1 / np.cos(rad)) / np.pi) / 2 * n


TILE_CACHE = ROOT / ".tmp" / "tiles"
TILE_CACHE.mkdir(parents=True, exist_ok=True)


def download_tiles() -> tuple[np.ndarray, float, float]:
    """下载并拼接 terrarium z11 瓦片（带本地缓存），返回 (dem[行,列], lon0, lat_top)。"""
    import io

    import rasterio

    n = 2**Z * 256
    deg_px = 360.0 / n
    c0 = int((LON0 + 180) / 360 * (2**Z))
    c1 = int((LON1 + 180) / 360 * (2**Z))
    r0 = int(row_of_lat(LAT1, 2**Z))  # 瓦片行号单位
    r1 = int(row_of_lat(LAT0, 2**Z))

    cols = (c1 - c0 + 1) * 256
    rows = (r1 - r0 + 1) * 256
    mosaic = np.zeros((rows, cols), dtype=np.float32)
    done = 0
    total = (c1 - c0 + 1) * (r1 - r0 + 1)
    for r in range(r0, r1 + 1):
        for c in range(c0, c1 + 1):
            cache = TILE_CACHE / f"{Z}_{c}_{r}.png"
            if cache.exists():
                data = cache.read_bytes()
            else:
                url = f"https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{Z}/{c}/{r}.png"
                data = None
                for attempt in range(3):
                    try:
                        with urllib.request.urlopen(url, timeout=60) as resp:
                            data = resp.read()
                        break
                    except Exception as e:  # noqa: BLE001
                        if attempt == 2:
                            raise RuntimeError(f"瓦片下载失败 {url}: {e}")
                        time.sleep(2)
                cache.write_bytes(data)
            with rasterio.open(io.BytesIO(data)) as src:
                rgb = np.stack([src.read(i + 1) for i in range(3)]).astype(np.float32)
            elev = rgb[0] * 256 + rgb[1] + rgb[2] / 256 - 32768
            mosaic[(r - r0) * 256 : (r - r0 + 1) * 256, (c - c0) * 256 : (c - c0 + 1) * 256] = elev
            done += 1
            if done % 20 == 0 or done == total:
                log(f"  瓦片 {done}/{total}")
    lon0 = -180 + c0 * 256 * deg_px
    return mosaic, lon0


def build_dem() -> tuple[np.ndarray, dict]:
    log("下载真实 DEM 瓦片（AWS Terrain Tiles, z11）…")
    mosaic, lon0 = download_tiles()
    log(f"拼接完成 {mosaic.shape[1]}x{mosaic.shape[0]}")

    n = 2**Z * 256
    deg_px = 360.0 / n
    r_tile0 = int(row_of_lat(LAT1, 2**Z))  # 顶边所在瓦片行号

    # 裁到 bbox（像素边界，行/列数取偶以便 2 倍降采样）
    row0 = int(round(row_of_lat(LAT1, n))) - r_tile0 * 256
    row1 = int(round(row_of_lat(LAT0, n))) - r_tile0 * 256
    j0 = int(round((LON0 - lon0) / deg_px))
    j1 = int(round((LON1 - lon0) / deg_px))
    if (row1 - row0) % 2:
        row1 -= 1
    if (j1 - j0) % 2:
        j1 -= 1
    sub = mosaic[row0:row1, j0:j1]
    # 子块每行中心纬度（降序，首行为北）
    lat_rows = lat_of_row(np.arange(row0, row1) + 0.5 + r_tile0 * 256, n)

    # 列/行方向各 2 倍均值降采样控制规模
    sub2 = sub.reshape(sub.shape[0] // 2, 2, sub.shape[1] // 2, 2).mean(axis=(1, 3))
    # 行方向插值到与经向相同的均匀步长（流水线假设格网在度上均匀）
    lon_step = deg_px * 2
    top = float(lat_rows[0])
    bottom = float(lat_rows[-1])
    nlat = int((top - bottom) / lon_step) + 1
    lat_new = top - np.arange(nlat) * lon_step  # 降序（北在前）

    out = np.empty((nlat, sub2.shape[1]), dtype=np.float32)
    lat_rows2 = lat_rows[1::2]  # 每个降采样块中心行的纬度，与 sub2 行数一致
    for j in range(sub2.shape[1]):
        # np.interp 要求 x 升序，故用反转序列
        out[:, j] = np.interp(lat_new[::-1], lat_rows2[::-1], sub2[::-1, j])[::-1]

    lon_new0 = lon0 + j0 * deg_px
    lon_m = lon_step * 111.32 * np.cos(np.radians((LAT0 + LAT1) / 2))
    lat_m = lon_step * 111.32
    meta = {
        "ncols": int(out.shape[1]),
        "nrows": int(out.shape[0]),
        "xll": round(lon_new0, 7),
        "yll": round(float(lat_new[-1]) - lon_step / 2, 7),
        "cellsize": round(float(lon_step), 9),
        "nodata": -9999.0,
        "crs": "EPSG:4326",
    }
    log(
        f"DEM 就绪 {meta['ncols']}x{meta['nrows']}，格网 ≈{lon_m * 1000:.0f}m×{lat_m * 1000:.0f}m，"
        f"高程 {out.min():.0f}~{out.max():.0f}m，范围 [{meta['xll']:.4f},{meta['yll']:.4f}]~"
        f"[{meta['xll'] + meta['ncols'] * meta['cellsize']:.4f},{meta['yll'] + meta['nrows'] * meta['cellsize']:.4f}]"
    )
    return out.astype(np.float64), meta


# ================================================================ OSM 要素
MIRRORS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]


def overpass(query: str, timeout: int = 90) -> dict:
    # 磁盘缓存：同一查询不重复请求（Overpass 时常超时）
    import hashlib

    cache = Path("../.tmp") / f"osm_{hashlib.md5(query.encode()).hexdigest()[:12]}.json"
    cache.parent.mkdir(exist_ok=True)
    if cache.exists():
        log("  命中 OSM 缓存")
        return json.loads(cache.read_text(encoding="utf-8"))
    data = urllib.parse.urlencode({"data": query}).encode()
    for url in MIRRORS:
        for attempt in range(2):
            try:
                req = urllib.request.Request(url, data=data, headers={"User-Agent": "hydro-schematic-tool/0.1"})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    r = json.loads(resp.read().decode("utf-8"))
                cache.write_text(json.dumps(r), encoding="utf-8")
                return r
            except Exception as e:  # noqa: BLE001
                log(f"  Overpass {url.split('/')[2]} 第{attempt + 1}次失败: {e}")
                time.sleep(3)
    return {"elements": []}


def fetch_osm() -> dict:
    b = BBOX_OSM
    # 两个轻量查询：主干河流名 + 水库轮廓；城镇点单独查
    q_river = f"""[out:json][timeout:50];
way["waterway"="river"]["name"]({b[0]},{b[1]},{b[2]},{b[3]});
out geom;"""
    q_lake = f"""[out:json][timeout:50];
(
  way["natural"="water"]["name"~"水库"]({b[0]},{b[1]},{b[2]},{b[3]});
  relation["natural"="water"]["name"~"水库"]({b[0]},{b[1]},{b[2]},{b[3]});
);
out geom;"""
    q_place = f"""[out:json][timeout:50];
node["place"~"^(town|village|county|city)$"]["name"]({b[0]},{b[1]},{b[2]},{b[3]});
out;"""
    log("从 OSM 获取河流名/水库轮廓/城镇点…")
    rivers: dict[str, list] = {}
    lakes: dict[str, list] = {}
    places: dict[str, tuple[float, float]] = {}
    for el in overpass(q_river).get("elements", []):
        tags = el.get("tags") or {}
        name = tags.get("name") or ""
        if el["type"] == "way" and el.get("geometry"):
            rivers.setdefault(name, []).append([(g["lon"], g["lat"]) for g in el["geometry"]])
    log(f"  OSM 河流名 {len(rivers)} 条")
    for el in overpass(q_lake).get("elements", []):
        tags = el.get("tags") or {}
        name = tags.get("name") or ""
        if el["type"] == "way" and el.get("geometry"):
            lakes.setdefault(name, []).append([(g["lon"], g["lat"]) for g in el["geometry"]])
        elif el["type"] == "relation":
            for m in el.get("members", []):
                if m.get("role") == "outer" and m.get("geometry"):
                    lakes.setdefault(name, []).append([(g["lon"], g["lat"]) for g in m["geometry"]])
    log(f"  OSM 水库 {len(lakes)} 个")
    for el in overpass(q_place).get("elements", []):
        tags = el.get("tags") or {}
        name = tags.get("name") or ""
        if el.get("lat"):
            places.setdefault(name, (el["lon"], el["lat"]))
    log(f"  OSM 城镇点 {len(places)} 个")
    return {"rivers": rivers, "lakes": lakes, "places": places}


# ================================================================ 湖泊多边形
def lakes_to_polygons(osm_lakes: dict) -> list[dict]:
    from shapely.geometry import LineString, Polygon
    from shapely.ops import linemerge, polygonize, unary_union

    out = []
    for name in LAKE_NAMES:
        parts = osm_lakes.get(name)
        if not parts:
            continue
        try:
            lines = [LineString(p) if len(p) >= 2 else None for p in parts]
            lines = [l for l in lines if l]
            merged = linemerge(unary_union(lines)) if len(lines) > 1 else lines[0]
            polys = list(polygonize(merged)) if merged.geom_type != "Polygon" else [merged]
            if not polys:
                # 退而求其次：直接对首尾接近闭合的环构建 Polygon
                for p in parts:
                    if len(p) >= 4 and p[0] == p[-1]:
                        poly = Polygon(p)
                        if poly.is_valid and poly.area > 1e-6:
                            polys = [poly]
                            break
            if not polys:
                continue
            poly = max(polys, key=lambda g: g.area)
            poly = poly.simplify(0.0005)
            if poly.area < 1e-6 or not poly.is_valid:
                continue
            coords = [[round(x, 7), round(y, 7)] for x, y in poly.exterior.coords]
            out.append({"name": name, "polygon": coords, "area_km2": round(poly.area * 111 * 111 * np.cos(np.radians(poly.centroid.y)) * np.cos(np.radians(poly.centroid.y)), 2)})
        except Exception as e:  # noqa: BLE001
            log(f"  湖泊 {name} 解析失败: {e}")
    return out


# ================================================================ 河名匹配
def match_river_names(segments: list[dict], osm_rivers: dict) -> None:
    """把 OSM 河名匹配到提取的河段（按中点距离 + 河名沿程投票）。"""
    from shapely.geometry import LineString, Point

    named = []
    for name, parts in osm_rivers.items():
        for pts in parts:
            if len(pts) >= 2:
                try:
                    ls = LineString(pts)
                    if ls.length > 0.01:
                        named.append((name, ls, ls.bounds))
                except Exception:  # noqa: BLE001
                    pass
    if not named:
        log("  无 OSM 河名可匹配")
        return

    for seg in segments:
        coords = seg["coords"]
        try:
            ls = LineString(coords)
        except Exception:  # noqa: BLE001
            continue
        bx = ls.bounds
        # bbox 预筛后再逐点测距
        cands = [
            (nm, ol)
            for nm, ol, b in named
            if not (b[2] < bx[0] - 0.01 or b[0] > bx[2] + 0.01 or b[3] < bx[1] - 0.01 or b[1] > bx[3] + 0.01)
        ]
        if not cands:
            continue
        votes: dict[str, float] = {}
        n = max(2, int(ls.length / 0.005))
        for name, ols in cands:
            hit = 0
            for i in range(n + 1):
                pt = ls.interpolate(i / n, normalized=True)
                if ols.distance(pt) < 0.003:  # ~300m
                    hit += 1
            frac = hit / (n + 1)
            if frac > 0.3:
                votes[name] = frac
        if votes:
            seg["名称"] = max(votes.items(), key=lambda kv: kv[1])[0]


def name_main_stem(segments: list[dict]) -> None:
    """给无名的干流段补上「堵河」：按上游面积最大的链传播。"""
    if not any(s.get("名称") for s in segments):
        return
    # 没有匹配到名字的河段中，若与已命名「堵河」段共端点则顺延
    byname = [s for s in segments if s.get("名称") == "堵河"]
    if not byname:
        return
    eps = 2e-4
    changed = True
    while changed:
        changed = False
        ends = []
        for s in byname:
            c = s["coords"]
            ends.append((c[0], c[-1]))
        for s in segments:
            if s.get("名称"):
                continue
            c = s["coords"]
            for (ax, ay), (bx, by) in ends:
                for p, q in ((c[0], (ax, ay)), (c[-1], (ax, ay)), (c[0], (bx, by)), (c[-1], (bx, by))):
                    if abs(p[0] - q[0]) < eps and abs(p[1] - q[1]) < eps and s.get("upstream_area_km2", 0) > 300:
                        s["名称"] = "堵河"
                        byname.append(s)
                        changed = True
                        break


# ================================================================ 流域边界
def delineate_basin(acc, di, dj, meta, outlet_ij) -> tuple[np.ndarray, float]:
    """出口汇水区掩膜 + 面积。"""
    h, w = acc.shape
    oi, oj = outlet_ij
    rev: dict[int, list[int]] = {}
    flat_down = np.where(di >= 0, di * w + dj, -1).ravel()
    for idx in range(h * w):
        d = flat_down[idx]
        if d >= 0:
            rev.setdefault(int(d), []).append(idx)
    target = oi * w + oj
    stack = [target]
    mask_flat = np.zeros(h * w, dtype=bool)
    mask_flat[target] = True
    while stack:
        cur = stack.pop()
        for up in rev.get(cur, ()):  # noqa: B007
            if not mask_flat[up]:
                mask_flat[up] = True
                stack.append(up)
    mask = mask_flat.reshape(h, w)
    cell_km2 = math.cos(math.radians(32.1)) * (meta["cellsize"] * 111.32) ** 2
    return mask, float(mask.sum() * cell_km2)


def mask_to_polygon(mask: np.ndarray, meta: dict, simplify_deg: float = 0.002) -> list:
    """掩膜边界 -> 最大环多边形坐标（行号北在上，有向边顺时针首尾相接）。"""
    h, w = mask.shape
    cs = meta["cellsize"]
    # 填充一圈 False，省去边界判断
    m = np.zeros((h + 2, w + 2), dtype=bool)
    m[1:-1, 1:-1] = mask

    segs: dict[tuple, list] = {}

    def add(a, b):
        key = (round(a[0], 9), round(a[1], 9))
        segs.setdefault(key, []).append(b)

    for i, j in np.argwhere(mask):
        yn = meta["yll"] + (h - i) * cs        # 该行北边（上）缘纬度
        ys = meta["yll"] + (h - 1 - i) * cs    # 南边（下）缘纬度
        x0 = meta["xll"] + j * cs
        x1 = x0 + cs
        # 有向边：北 (x0,yn)->(x1,yn)，东 (x1,yn)->(x1,ys)，南 (x1,ys)->(x0,ys)，西 (x0,ys)->(x0,yn)
        if not m[i, j + 1]:        # 北邻（原 i-1 行）
            add((x0, yn), (x1, yn))
        if not m[i + 2, j + 1]:    # 南邻（原 i+1 行）
            add((x1, ys), (x0, ys))
        if not m[i + 1, j]:        # 西邻
            add((x0, ys), (x0, yn))
        if not m[i + 1, j + 2]:    # 东邻
            add((x1, yn), (x1, ys))

    # 串环
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
    if not rings:
        return []
    from shapely.geometry import Polygon

    polys = [Polygon(r) for r in rings if len(r) >= 4]
    polys = [p.buffer(0) for p in polys if p.buffer(0).is_valid and p.area > 1e-4]
    if not polys:
        return []
    poly = max(polys, key=lambda g: g.area).simplify(simplify_deg)
    return [[round(x, 7), round(y, 7)] for x, y in poly.exterior.coords]


# ================================================================ 主流程
def main(threshold: int = 1200) -> None:
    SAMPLES.mkdir(parents=True, exist_ok=True)

    z, meta = build_dem()

    # 出口格网
    oj = int((OUTLET_LON - meta["xll"]) / meta["cellsize"])
    oj = max(0, min(meta["ncols"] - 1, oj))
    # 找出口附近汇流累积最大的格子（对齐河道）
    win = 30
    best = None

    # 先跑一遍流水线
    log("运行水文流水线：填洼 / D8 / 汇流累积 …")
    t = time.time()
    filled = demmod.fill_depressions(z, nodata=meta["nodata"])
    log(f"  填洼 {time.time() - t:.0f}s")
    t = time.time()
    fdir, di, dj = demmod.d8_directions(filled, nodata=meta["nodata"])
    acc = demmod.flow_accumulation(filled, di, dj)
    log(f"  流向/汇流 {time.time() - t:.0f}s")

    h, w = acc.shape
    # 行号自北向南：i = nrows-1 - (lat-yll)/cellsize
    oi0 = meta["nrows"] - 1 - int((OUTLET_LAT - meta["yll"]) / meta["cellsize"])
    for ii in range(max(0, oi0 - win), min(h, oi0 + win + 1)):
        for jj in range(max(0, oj - win), min(w, oj + win + 1)):
            if acc[ii, jj] > 0 and (best is None or acc[ii, jj] > best[0]):
                best = (acc[ii, jj], ii, jj)
    if best is None:
        raise RuntimeError("未找到出口格网")
    _, oi, oj = best
    cell_km2_out = math.cos(math.radians(32.1)) * (meta["cellsize"] * 111.32) ** 2
    log(f"出口格网 ({oi},{oj})，汇流 {acc[oi, oj]:.0f} 格 ≈ {acc[oi, oj] * cell_km2_out:.0f} km²（官方黄龙滩以上 10668 km²）")

    # 矢量化
    t = time.time()
    mask = demmod.extract_stream_mask(acc, threshold, z, nodata=meta["nodata"])
    segs = demmod.vectorize_streams(mask, filled, acc, di, dj, meta, simplify_m=150, smooth=True)
    log(f"矢量化 {time.time() - t:.0f}s，{len(segs)} 个河段（阈值 {threshold} 格）")

    # 汇水面积
    cell_km2 = math.cos(math.radians(32.1)) * (meta["cellsize"] * 111.32) ** 2
    for s in segs:
        s["upstream_area_km2"] = round(s["ncell"] * cell_km2, 1)

    # 流域内过滤：从河段下游端沿 D8 追踪，只保留能汇入黄龙滩出口的河段
    # （bbox 内还包含汉江干流与周边小流域，须剔除）
    log("按下游追踪过滤流域外河段…")
    t = time.time()
    kept: list[dict] = []
    for s in segs:
        ci, cj = s["end_cell"]
        ok = False
        for _ in range(h * w):
            if ci == oi and cj == oj:
                ok = True
                break
            ni, nj = int(di[ci, cj]), int(dj[ci, cj])
            if ni < 0:
                break
            ci, cj = ni, nj
        if ok:
            kept.append(s)
    log(f"  流域内河段 {len(kept)}/{len(segs)}（{time.time() - t:.0f}s）")
    segs = kept

    # OSM 数据
    osm = fetch_osm()
    match_river_names(segs, osm["rivers"])
    named = sum(1 for s in segs if s.get("名称"))
    log(f"河名匹配：{named}/{len(segs)} 段")
    name_main_stem(segs)

    lakes = lakes_to_polygons(osm["lakes"])
    log(f"水库多边形：{[l['name'] for l in lakes]}")

    places = osm["places"]
    rain_stations = []
    for name, flon, flat in RAIN_NAMES:
        lon, lat = places.get(name, (flon, flat))
        rain_stations.append({"name": name, "lon": lon, "lat": lat})

    # 流域边界
    log("圈画黄龙滩以上流域边界…")
    basin_mask, basin_km2 = delineate_basin(acc, di, dj, meta, (oi, oj))
    boundary = mask_to_polygon(basin_mask, meta)
    log(f"  流域面积 {basin_km2:.0f} km²（官方黄龙滩以上 10668 km²），边界点 {len(boundary)}")

    # -------------------------------------------------- 写出
    log("写出 SHP / DEM / 晕渲图 …")
    # DEM 用 GeoTIFF（压缩），rasterio 读取
    import rasterio
    from rasterio.transform import from_origin

    tif = SAMPLES / "dem.tif"
    tr = from_origin(meta["xll"], meta["yll"] + meta["nrows"] * meta["cellsize"], meta["cellsize"], meta["cellsize"])
    with rasterio.open(
        tif,
        "w",
        driver="GTiff",
        height=meta["nrows"],
        width=meta["ncols"],
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=tr,
        compress="deflate",
    ) as dst:
        dst.write(z.astype(np.float32), 1)
    demmod.make_relief(z, meta, SAMPLES / "dem_relief.png")

    # 河流
    river_fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[round(x, 7), round(y, 7)] for x, y in s["coords"]]},
                "properties": {
                    "名称": s.get("名称", ""),
                    "级别": int(s["order"]),
                    "长度km": round(s["length_m"] / 1000, 2),
                    "汇流面积": s["upstream_area_km2"],
                },
            }
            for s in segs
        ],
    }
    write_shapefile(SAMPLES / "河流.shp", river_fc, "polyline")

    # 水文站
    hydro_fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [hst["lon"], hst["lat"]]},
                "properties": {
                    "名称": hst["name"],
                    "站别": "水文站",
                    "集水面积": hst["area_km2"] or "",
                    "备注": hst["note"],
                },
            }
            for hst in HYDRO_STATIONS
        ],
    }
    write_shapefile(SAMPLES / "水文站.shp", hydro_fc, "point")

    # 雨量站
    rain_fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                "properties": {"名称": r["name"], "站别": "雨量站"},
            }
            for r in rain_stations
        ],
    }
    write_shapefile(SAMPLES / "雨量站.shp", rain_fc, "point")

    # 湖泊
    if lakes:
        lake_fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [l["polygon"]]},
                    "properties": {"名称": l["name"], "面积": l["area_km2"]},
                }
                for l in lakes
            ],
        }
        write_shapefile(SAMPLES / "湖泊.shp", lake_fc, "polygon")

    # 边界
    if boundary:
        b_fc = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [boundary]},
                    "properties": {"名称": "堵河流域（黄龙滩以上）", "面积": round(basin_km2, 0)},
                }
            ],
        }
        write_shapefile(SAMPLES / "流域边界.shp", b_fc, "polygon")

    info = {
        "name": "堵河流域（真实DEM）",
        # 样例自带推荐拓扑参数：真实雨量站在镇区，距 DEM 提取河段可达数公里
        "topo_options": {"station_snap_max_m": 5000.0},
        "dem": {
            "file": "dem.tif",
            "relief": "dem_relief.png",
            "bounds": [meta["xll"], meta["yll"], meta["xll"] + meta["ncols"] * meta["cellsize"], meta["yll"] + meta["nrows"] * meta["cellsize"]],
            "elev_min": round(float(z.min()), 1),
            "elev_max": round(float(z.max()), 1),
            "cellsize": meta["cellsize"],
            "ncols": meta["ncols"],
            "nrows": meta["nrows"],
            "crs": "EPSG:4326",
            "accum_threshold": threshold,
        },
        "layers": [
            {"file": "河流.shp", "name": "堵河水系", "type": "river"},
            {"file": "水文站.shp", "name": "水文站", "type": "hydro_station"},
            {"file": "雨量站.shp", "name": "雨量站", "type": "rain_station"},
            {"file": "湖泊.shp", "name": "湖泊水库", "type": "lake"},
            {"file": "流域边界.shp", "name": "流域边界", "type": "boundary"},
        ],
    }
    (SAMPLES / "samples.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    log("完成。samples.json 已更新。")


if __name__ == "__main__":
    thr = int(sys.argv[1]) if len(sys.argv) > 1 else 1200
    main(thr)
