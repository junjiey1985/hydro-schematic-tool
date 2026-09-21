"""合成样例数据：分形地形 → DEM 河网提取 → 拓扑 → 河流/站点/湖泊 SHP。

这样生成的数据自带真实的空间拓扑关系，可以直接用来验证整条流水线。
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

import numpy as np

from .dem import extract_network, make_relief, sample_accumulation, cell_size_m, write_ascii_grid
from .shp_io import write_shapefile
from .topology import build_topology, sample_dem

# 研究区：长江中游某支流流域（示意）
LON0, LAT0 = 110.20, 30.00
NCOLS, NROWS, CELL = 480, 420, 0.0025

HYDRO_NAMES = ["龙湾", "石门", "双河口", "白水", "竹溪", "梅山", "沙河", "青坪"]
RAIN_NAMES = ["云岭", "大坪", "松林", "石板", "长冲", "黄柏", "高桥", "桃园",
              "冷水", "李家湾", "三岔", "岩口", "枫树坪", "新田"]
TRIB_NAMES = ["白水河", "龙泉溪", "沙河", "梅溪", "竹溪", "石桥河", "双溪", "大沟",
              "冷水河", "板桥河", "青溪", "桐树沟", "杨树河", "黄泥溪", "枫林溪",
              "横溪", "九曲河", "石羊河", "兰溪", "桃花溪"]
MAIN_NAME = "清江"


# ---------------------------------------------------------------- 地形
def _fractal_noise(shape: tuple[int, int], octaves: int, rng: np.random.Generator) -> np.ndarray:
    """多尺度分形噪声：粗网格随机值 + 双线性插值上采样（比大核高斯滤波快得多）。"""
    from scipy.ndimage import map_coordinates

    out = np.zeros(shape)
    amp, total = 1.0, 0.0
    for k in range(octaves):
        step = 2 ** (octaves - k)
        ch = max(2, shape[0] // step + 1)
        cw = max(2, shape[1] // step + 1)
        coarse = rng.standard_normal((ch, cw))
        zi = np.linspace(0, ch - 1, shape[0])
        zj = np.linspace(0, cw - 1, shape[1])
        ii, jj = np.meshgrid(zi, zj, indexing="ij")
        layer = map_coordinates(coarse, [ii, jj], order=1, mode="reflect")
        layer = (layer - layer.mean()) / max(1e-6, layer.std())
        out += layer * amp
        total += amp
        amp *= 0.55
    return out / total


def make_dem(seed: int = 20240918) -> tuple[np.ndarray, dict]:
    """构造一个向单一出口汇流的闭合流域：径向坡降 + 分形起伏 + 边界围堰。

    - 径向坡降：保证全域水流汇聚到唯一出口，形成完整流域；
    - 分形起伏：保证河网呈自然树枝状；
    - 边界围堰：靠近研究区边界抬升且不加噪声（否则水会从边界四周流出，
      把流域切成一堆互不相连的小流域）。
    """
    from scipy import ndimage

    rng = np.random.default_rng(seed)
    u = np.linspace(0, 1, NCOLS)[None, :]
    v = np.linspace(0, 1, NROWS)[:, None]

    # 出口位置（研究区偏东南）
    u0, v0 = 0.86, 0.82
    ax, ay = 1.18, 0.94
    dx = (u - u0) / ax
    dy = (v - v0) / ay
    d = np.sqrt(dx**2 + dy**2)
    d = d / d.max()

    # 边界围堰：边缘 3.5% 范围内线性抬升，且该范围内不加噪声
    db = np.minimum(np.minimum(u, 1 - u), np.minimum(v, 1 - v))
    band = np.clip((0.035 - db) / 0.035, 0, 1)

    noise = _fractal_noise((NROWS, NCOLS), 7, rng)
    z = 160.0 + 1450.0 * d**1.18 + noise * (70.0 + 140.0 * d) * (1.0 - band)
    z += 780.0 * band

    # 几处山脊，打破对称、增加支流分汊
    z += 210.0 * np.exp(-(((u - 0.24) ** 2 + (v - 0.18) ** 2)) / 0.030)
    z += 165.0 * np.exp(-(((u - 0.12) ** 2 + (v - 0.72) ** 2)) / 0.024)
    z -= 90.0 * np.exp(-(((u - 0.45) ** 2 + (v - 0.45) ** 2)) / 0.020)

    z = ndimage.gaussian_filter(z, sigma=1.1, mode="reflect")
    z = np.clip(z, 25.0, None)

    meta = {
        "ncols": NCOLS,
        "nrows": NROWS,
        "xll": LON0,
        "yll": LAT0,
        "cellsize": CELL,
        "nodata": -9999.0,
    }
    return z, meta


# ---------------------------------------------------------------- 主流程
def _log(msg: str) -> None:
    print(msg, flush=True)


def generate_samples(out_dir: Path, threshold: int = 600, seed: int = 20240918, log=_log) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    random.seed(seed)

    log("① 生成 DEM …")
    dem, meta = make_dem(seed)
    write_ascii_grid(out_dir / "dem.asc", dem, meta)
    relief_png = out_dir / "dem_relief.png"
    relief_meta = make_relief(dem, meta, relief_png)

    log(f"② 填洼/D8/汇流累积/河网提取（阈值 {threshold} 格）…")
    net = extract_network(dem, meta, threshold=threshold, simplify_m=110.0, smooth=True)
    segs = net["segments"]
    if not segs:
        raise RuntimeError("河网提取为空，请调低汇流阈值")
    log(f"   提取到 {len(segs)} 个河段")

    dem_ctx = {"filled": net["filled"], "meta": meta, "acc": net["acc"]}

    # ---------------------------------------------------------- 构造河流要素
    def seg_feature(s: dict, name: str = "") -> dict:
        z0 = sample_dem(net["filled"], meta, *s["start_xy"])
        z1 = sample_dem(net["filled"], meta, *s["end_xy"])
        return {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[round(c[0], 7), round(c[1], 7)] for c in s["coords"]]},
            "properties": {
                "河名": name,
                "级别": int(s["order"]),
                "长度_KM": round(s["length_m"] / 1000.0, 3),
                "上游面积": s["upstream_area_km2"],
                "起点高程": round(z0, 1) if z0 is not None else None,
                "终点高程": round(z1, 1) if z1 is not None else None,
            },
        }

    river_fc = {
        "type": "FeatureCollection",
        "features": [seg_feature(s) for s in segs],
    }

    log("③ 构建拓扑关系 …")
    topo = build_topology(river_fc["features"], [], [], [], dem_ctx=dem_ctx, options={"snap_tolerance_m": 20})
    if not topo.get("ok"):
        raise RuntimeError(f"拓扑构建失败: {topo.get('error')}")
    log(f"   节点 {topo['stats']['node_count']} / 河段 {topo['stats']['edge_count']} / 最大级别 {topo['stats']['max_order']}")

    # ---------------------------------------------------------- 干流识别与命名
    edges = {e["id"]: e for e in topo["edges"]}
    nodes = {n["id"]: n for n in topo["nodes"]}
    outlets = [n for n in nodes.values() if n["out_degree"] == 0] or list(nodes.values())
    outlet = max(outlets, key=lambda n: n["upstream_length_m"])

    upstream_of: dict[str, list[str]] = {}
    for e in edges.values():
        upstream_of.setdefault(e["to_node"], []).append(e["id"])

    main_ids: list[str] = []
    cur_node = outlet["id"]
    guard = 0
    while guard < 500:
        guard += 1
        ups = upstream_of.get(cur_node, [])
        if not ups:
            break
        eid = max(ups, key=lambda i: edges[i]["upstream_length_m"] + edges[i]["length_m"])
        main_ids.append(eid)
        cur_node = edges[eid]["from_node"]
    main_set = set(main_ids)

    # 支流按上游规模排序命名
    others = sorted(
        [e for e in edges.values() if e["id"] not in main_set],
        key=lambda e: -(e["upstream_length_m"] + e["length_m"]),
    )
    name_map: dict[str, str] = {eid: MAIN_NAME for eid in main_set}
    for i, e in enumerate(others):
        name_map[e["id"]] = TRIB_NAMES[i] if i < len(TRIB_NAMES) else f"{MAIN_NAME}支流{i + 1}"

    river_fc["features"] = []
    order_by_id = {e["id"]: e for e in topo["edges"]}
    for s in segs:
        # 用几何中点匹配回拓扑河段，拿到名称
        best, bd = None, 1e18
        mid = s["coords"][len(s["coords"]) // 2]
        for e in topo["edges"]:
            c = e["coords"]
            d = (c[len(c) // 2][0] - mid[0]) ** 2 + (c[len(c) // 2][1] - mid[1]) ** 2
            if d < bd:
                bd, best = d, e["id"]
        river_fc["features"].append(seg_feature(s, name_map.get(best, "")))
    log(f"   干流 {MAIN_NAME} 由 {len(main_set)} 个河段组成，命名支流 {len(others)} 条")

    # ---------------------------------------------------------- 水文站
    log("④ 生成水文站 / 雨量站 / 湖泊 …")
    hydro_feats: list[dict] = []
    main_edges_sorted = [edges[i] for i in main_ids]

    # 沿干流按上游距离布站
    def point_along(eid: str, ratio: float) -> tuple[float, float]:
        c = edges[eid]["coords"]
        idx = min(len(c) - 1, max(0, int(round(ratio * (len(c) - 1)))))
        x, y = c[idx]
        jitter_lon = random.uniform(-0.0016, 0.0016)
        jitter_lat = random.uniform(-0.0016, 0.0016)
        return round(x + jitter_lon, 7), round(y + jitter_lat, 7)

    for i, name in enumerate(HYDRO_NAMES[:6]):
        k = int(round(i * (len(main_edges_sorted) - 1) / 5.0))
        eid = main_edges_sorted[max(0, len(main_edges_sorted) - 1 - k)]["id"]
        x, y = point_along(eid, 0.5)
        area = edges[eid]["upstream_length_m"]
        hydro_feats.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [x, y]},
                "properties": {
                    "站名": f"{name}水文站",
                    "站码": f"60{random.randint(10000, 99999)}",
                    "河流": MAIN_NAME,
                },
            }
        )
    for i, name in enumerate(HYDRO_NAMES[6:]):
        pool = [e for e in others if e["upstream_length_m"] > 40000] or others
        if not pool:
            break
        e = pool[i % len(pool)]
        x, y = point_along(e["id"], random.uniform(0.4, 0.8))
        hydro_feats.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [x, y]},
                "properties": {"站名": f"{name}水文站", "站码": f"60{random.randint(10000, 99999)}", "河流": name_map.get(e["id"], "")},
            }
        )

    # 雨量站：面上均匀撒点
    rain_feats = []
    for name in RAIN_NAMES:
        x = round(LON0 + random.uniform(0.04, NCOLS * CELL - 0.04), 7)
        y = round(LAT0 + random.uniform(0.04, NROWS * CELL - 0.04), 7)
        rain_feats.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [x, y]},
                "properties": {"站名": f"{name}雨量站", "站码": f"R{random.randint(1000, 9999)}", "类型": "雨量站"},
            }
        )

    # 湖泊 / 水库：贴着干流下游生成
    lake_specs = [("南湖", 2.2, 0.10), ("镜湖", 1.1, 0.32), ("龙潭水库", 3.6, 0.02)]
    lake_feats = []
    for name, area_km2, ratio in lake_specs:
        k = int(round(ratio * (len(main_edges_sorted) - 1)))
        eid = main_edges_sorted[max(0, len(main_edges_sorted) - 1 - k)]["id"]
        c = edges[eid]["coords"]
        x0, y0 = c[len(c) // 2]
        r_km = math.sqrt(area_km2 / math.pi)
        dr_lon = r_km / (111.32 * math.cos(math.radians(y0)))
        dr_lat = r_km / 110.54
        pts = []
        n = 14
        for i in range(n):
            a = 2 * math.pi * i / n
            rr = 1.0 + 0.22 * math.sin(3 * a) + 0.12 * math.cos(5 * a)
            pts.append(
                [
                    round(x0 + dr_lon * 1.35 * rr * math.cos(a) + 0.004, 7),
                    round(y0 + dr_lat * rr * math.sin(a) - 0.003, 7),
                ]
            )
        pts.append(pts[0])
        lake_feats.append(
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [pts]},
                "properties": {"名称": name, "面积_KM2": round(area_km2, 2), "类型": "湖泊" if "水库" not in name else "水库"},
            }
        )

    # 研究区边界
    boundary = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [LON0, LAT0],
                        [LON0 + NCOLS * CELL, LAT0],
                        [LON0 + NCOLS * CELL, LAT0 + NROWS * CELL],
                        [LON0, LAT0 + NROWS * CELL],
                        [LON0, LAT0],
                    ]],
                },
                "properties": {"名称": "示例流域范围"},
            }
        ],
    }

    # ---------------------------------------------------------- 写出 SHP
    log("⑤ 写出 SHP …")
    write_shapefile(out_dir / "河流.shp", river_fc)
    write_shapefile(out_dir / "水文站.shp", {"type": "FeatureCollection", "features": hydro_feats})
    write_shapefile(out_dir / "雨量站.shp", {"type": "FeatureCollection", "features": rain_feats})
    write_shapefile(out_dir / "湖泊.shp", {"type": "FeatureCollection", "features": lake_feats})
    write_shapefile(out_dir / "流域边界.shp", boundary)

    info = {
        "name": "示例流域（合成数据）",
        "dem": {
            "file": "dem.asc",
            "relief": "dem_relief.png",
            "bounds": relief_meta.get("bounds"),
            "elev_min": relief_meta.get("elev_min"),
            "elev_max": relief_meta.get("elev_max"),
            "cellsize": meta["cellsize"],
            "ncols": meta["ncols"],
            "nrows": meta["nrows"],
            "crs": "EPSG:4326",
            "accum_threshold": threshold,
        },
        "layers": [
            {"file": "河流.shp", "name": "河流水系", "type": "river"},
            {"file": "水文站.shp", "name": "水文站", "type": "hydro_station"},
            {"file": "雨量站.shp", "name": "雨量站", "type": "rain_station"},
            {"file": "湖泊.shp", "name": "湖泊水库", "type": "lake"},
            {"file": "流域边界.shp", "name": "流域边界", "type": "boundary"},
        ],
        "stats": {
            "river_segments": len(river_fc["features"]),
            "hydro_stations": len(hydro_feats),
            "rain_stations": len(rain_feats),
            "lakes": len(lake_feats),
            "main_river": MAIN_NAME,
            "topology": topo["stats"],
        },
    }
    (out_dir / "samples.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    log("完成 ✓")
    return info


if __name__ == "__main__":
    from ..config import SAMPLES_DIR

    generate_samples(SAMPLES_DIR)
