"""河网拓扑构建：节点-弧段模型 + 流向判定 + 站点挂接。

核心流程::

    线要素 → 打断（noding）→ 端点吸附成节点 → 假节点合并
           → 流向判定（DEM 高程 / 高程属性 / 数字化方向）
           → 连通分量遍历定向 → Strahler 分级 → 站点挂接 → 输出拓扑图

输出结构（供前端与概化图模块消费）::

    {
      ok, method, stats, warnings,
      nodes:     [{id, x, y, kind, in_degree, out_degree, elevation,
                   upstream_length_m, upstream_area_km2, station_ids, upstream_edges}],
      edges:     [{id, from_node, to_node, name, order, length_m,
                   upstream_length_m, coords, props}],
      stations:  [{id, name, type, x, y, edge_id, node_id, ratio, snap_m, attached}],
      lakes:     [{id, name, area_km2, centroid, edge_id, ratio, outline}]
    }
"""
from __future__ import annotations

import math
import time
from collections import defaultdict, deque
from typing import Any, Iterable, Optional

import numpy as np
from shapely.geometry import LineString, Point, shape
from shapely.ops import split as shp_split
from shapely.ops import transform as shp_transform
from shapely.ops import unary_union
from shapely.strtree import STRtree

from .shp_io import length_m_planar

NAME_FIELDS = [
    "名称", "NAME", "name", "河名", "河流名称", "NAME_CN", "NAME_CH",
    "水系名称", "站名", "STNM", "NAME_1", "NAME_2",
]
ELEV_FIELDS = ["高程", "ELEV", "elev", "altitude", "ALT", "起点高程", "终点高程", "Z", "DEM"]

DEFAULT_TOPOLOGY_OPTIONS = {
    "snap_tolerance_m": 30.0,
    "dissolve_pseudo_nodes": True,
    "flow_direction": "auto",  # auto | dem | attribute | digitized
    "station_snap_max_m": 800.0,
    "split_at_hydro_station": True,
}


# ================================================================= 基础工具
class _UF:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, a: int) -> int:
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def _coslat(lat: float) -> float:
    return max(0.05, math.cos(math.radians(lat)))


def _pick(props: dict, candidates: Iterable[str]) -> Any:
    upper = {str(k).strip().upper(): v for k, v in props.items()}
    for c in candidates:
        v = upper.get(c.upper())
        if v not in (None, ""):
            return v
    return None


def _feat_name(props: dict) -> str:
    return str(_pick(props, NAME_FIELDS) or "")


def _clean_props(props: dict) -> dict:
    return {k: v for k, v in (props or {}).items() if not str(k).startswith("_")}


def _distance_m(pt: Point, geom) -> float:
    """点位到几何的近似实地距离（米）。"""
    kx = 111320.0 * _coslat(pt.y)
    ky = 110540.0

    def tf(x, y, z=None):
        return x * kx, y * ky

    p2 = Point(pt.x * kx, pt.y * ky)
    return float(shp_transform(tf, geom).distance(p2))


def _extract(geojson_features: list[dict], kind: str) -> list[tuple[Any, dict]]:
    """按几何类型提取要素（线自动拆成单段）。"""
    out: list[tuple[Any, dict]] = []
    for f in geojson_features:
        g = f.get("geometry")
        if not g:
            continue
        try:
            geom = shape(g)
        except Exception:
            continue
        if geom.is_empty:
            continue
        props = f.get("properties") or {}
        gt = geom.geom_type
        if kind == "line":
            if gt == "LineString":
                out.append((geom, props))
            elif gt == "MultiLineString":
                out.extend((p, props) for p in geom.geoms)
            elif gt == "LinearRing":
                out.append((LineString(geom.coords), props))
        elif kind == "point":
            if gt == "Point":
                out.append((geom, props))
            elif gt == "MultiPoint":
                out.extend((p, props) for p in geom.geoms)
        elif kind == "polygon":
            if gt in ("Polygon", "MultiPolygon"):
                out.append((geom, props))
    return out


def _cluster(points: list[tuple[float, float]], tol_deg: float) -> tuple[list[int], list[tuple[float, float]]]:
    """端点按容差聚簇，返回 (每点所属簇号, 簇中心坐标)。"""
    n = len(points)
    if n == 0:
        return [], []
    if tol_deg <= 0:
        buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
        for i, (x, y) in enumerate(points):
            buckets[(round(x * 1e7), round(y * 1e7))].append(i)
        labels = [0] * n
        centers: list[tuple[float, float]] = []
        for members in buckets.values():
            cid = len(centers)
            centers.append(
                (sum(points[m][0] for m in members) / len(members),
                 sum(points[m][1] for m in members) / len(members))
            )
            for m in members:
                labels[m] = cid
        return labels, centers

    grid = max(tol_deg, 1e-12)
    buckets = defaultdict(list)
    bidx: list[tuple[int, int]] = []
    for x, y in points:
        b = (int(math.floor(x / grid)), int(math.floor(y / grid)))
        buckets[b].append(len(bidx))
        bidx.append(b)

    uf = _UF(n)
    tol2 = tol_deg * tol_deg * 1.000001
    for i, (bx, by) in enumerate(bidx):
        xi, yi = points[i]
        k = _coslat(yi)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in buckets.get((bx + dx, by + dy), ()):
                    if j <= i:
                        continue
                    xj, yj = points[j]
                    if ((xi - xj) * k) ** 2 + (yi - yj) ** 2 < tol2:
                        uf.union(i, j)

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        groups[uf.find(i)].append(i)
    labels = [0] * n
    centers = []
    for root, members in groups.items():
        cid = len(centers)
        centers.append(
            (sum(points[m][0] for m in members) / len(members),
             sum(points[m][1] for m in members) / len(members))
        )
        for m in members:
            labels[m] = cid
    return labels, centers


def sample_dem(dem: np.ndarray, meta: dict, lon: float, lat: float, win: int = 2) -> Optional[float]:
    """取 DEM 值（窗口内最小值，河道点更稳）。"""
    cs = meta["cellsize"]
    j = int(round((lon - meta["xll"]) / cs - 0.5))
    i = int(round((meta["nrows"] - 0.5) - (lat - meta["yll"]) / cs))
    h, w = dem.shape
    if not (0 <= i < h and 0 <= j < w):
        return None
    i0, i1 = max(0, i - win), min(h, i + win + 1)
    j0, j1 = max(0, j - win), min(w, j + win + 1)
    block = dem[i0:i1, j0:j1]
    nodata = meta.get("nodata", -9999)
    block = block[np.isfinite(block) & (np.abs(block - nodata) > 1e-6)]
    return float(block.min()) if block.size else None


# ================================================================= 主流程
def build_topology(
    rivers: list[dict],
    hydro_stations: Optional[list[dict]] = None,
    rain_stations: Optional[list[dict]] = None,
    lakes: Optional[list[dict]] = None,
    dem_ctx: Optional[dict] = None,
    options: Optional[dict] = None,
) -> dict:
    opts = dict(DEFAULT_TOPOLOGY_OPTIONS)
    opts.update(options or {})
    hydro_stations = hydro_stations or []
    rain_stations = rain_stations or []
    lakes = lakes or []
    warnings: list[str] = []

    raw = _extract(rivers, "line")
    if not raw:
        return {"ok": False, "error": "未找到河流线要素，请先导入或绘制河流数据"}

    # ---------------------------------------------------------- 1. 打断线要素
    geoms = [g for g, _ in raw]
    try:
        merged = unary_union(geoms)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"线要素打断失败：{e}"}

    if merged.geom_type == "LineString":
        pieces = [merged]
    elif merged.geom_type == "MultiLineString":
        pieces = list(merged.geoms)
    elif merged.geom_type == "GeometryCollection":
        pieces = [g for g in merged.geoms if g.geom_type == "LineString"]
    else:
        pieces = []
    pieces = [p for p in pieces if p.length > 0]
    if not pieces:
        return {"ok": False, "error": "打断后无有效河段"}

    tree = STRtree(geoms)
    edges: dict[int, dict] = {}
    for k, p in enumerate(pieces):
        oi = int(tree.nearest(p))
        orig, props = raw[oi]
        digitized = orig.project(Point(p.coords[0])) < orig.project(Point(p.coords[-1]))
        coords = [[round(c[0], 7), round(c[1], 7)] for c in p.coords]
        if len(coords) < 2:
            continue
        edges[k] = {
            "id": k,
            "coords": coords,
            "name": _feat_name(props),
            "props": _clean_props(props),
            "digitized": digitized,
            "length_m": round(length_m_planar(coords), 2),
            "from": None,
            "to": None,
        }

    # ---------------------------------------------------------- 2. 端点聚簇成节点
    pts: list[tuple[float, float]] = []
    order_of_pt: list[int] = []
    for eid, e in edges.items():
        pts.append((e["coords"][0][0], e["coords"][0][1]))
        order_of_pt.append(eid)
        pts.append((e["coords"][-1][0], e["coords"][-1][1]))
        order_of_pt.append(eid)

    tol_deg = opts["snap_tolerance_m"] / 111320.0 if opts["snap_tolerance_m"] > 0 else 0.0
    labels, centers = _cluster(pts, tol_deg)
    for idx, eid in enumerate(order_of_pt):
        e = edges[eid]
        if e["from"] is None:
            e["from"] = labels[idx]
        else:
            e["to"] = labels[idx]

    # 去掉自环与重复段
    drop = []
    for eid, e in edges.items():
        if e["from"] == e["to"]:
            drop.append(eid)
            continue
        a, b = e["coords"][0], e["coords"][-1]
        key = (min(e["from"], e["to"]), max(e["from"], e["to"]), round(e["length_m"], 1))
        for oid, oe in edges.items():
            if oid >= eid or oid in drop:
                continue
            c, d = oe["coords"][0], oe["coords"][-1]
            if key == (min(oe["from"], oe["to"]), max(oe["from"], oe["to"]), round(oe["length_m"], 1)):
                drop.append(eid)
                warnings.append(f"河段 E{eid} 与 E{oid} 重复，已忽略")
                break
    for eid in set(drop):
        edges.pop(eid, None)
    if not edges:
        return {"ok": False, "error": "去重后无有效河段"}

    # ---------------------------------------------------------- 3. 水文站打断
    station_records: list[dict] = []
    station_by_node: dict[int, list[dict]] = defaultdict(list)

    def _attach_stations(station_list: list[dict], kind: str) -> None:
        sp = _extract(station_list, "point")
        if not sp:
            return
        for pt, props in sp:
            rec = {
                "id": f"{'H' if kind == 'hydro' else 'R'}{len(station_records) + 1}",
                "name": _feat_name(props) or f"{'水文站' if kind == 'hydro' else '雨量站'}{len(station_records) + 1}",
                "type": kind,
                "x": round(pt.x, 7),
                "y": round(pt.y, 7),
                "props": _clean_props(props),
                "edge_id": None,
                "node_id": None,
                "ratio": None,
                "snap_m": None,
                "attached": False,
            }
            best = (None, 1e18, 0.0)
            for eid, e in edges.items():
                ln = LineString(e["coords"])
                d = _distance_m(pt, ln)
                if d < best[1]:
                    best = (eid, d, ln.project(pt) / max(ln.length, 1e-12))
            if best[0] is not None:
                eid, dist_m, ratio = best
                # 始终记录"最近河段"，attached 表示是否在容许距离内（图上用虚线区分）
                rec["edge_id"] = f"E{eid}"
                rec["ratio"] = round(float(ratio), 6)
                rec["snap_m"] = round(float(dist_m), 1)
                rec["attached"] = dist_m <= opts["station_snap_max_m"]

                if (
                    kind == "hydro"
                    and opts["split_at_hydro_station"]
                    and rec["attached"]
                    and 0.02 < ratio < 0.98
                ):
                    e = edges.pop(eid)
                    ln = LineString(e["coords"])
                    cut = ln.interpolate(ratio * ln.length)
                    parts = [g for g in shp_split(ln, cut).geoms if g.geom_type == "LineString" and g.length > 0]
                    if len(parts) == 2:
                        parts.sort(key=lambda g: ln.project(Point(g.coords[0])))
                        nid = len(centers)
                        centers.append((cut.x, cut.y))
                        chain = [e["from"], nid, e["to"]]
                        new_ids = []
                        for pi, g in enumerate(parts):
                            ne = max(edges.keys()) + 1 if edges else 0
                            edges[ne] = {
                                "id": ne,
                                "from": chain[pi],
                                "to": chain[pi + 1],
                                "coords": [[round(c[0], 7), round(c[1], 7)] for c in g.coords],
                                "name": e["name"],
                                "props": dict(e["props"]),
                                "digitized": e["digitized"],
                                "length_m": round(length_m_planar(list(g.coords)), 2),
                            }
                            new_ids.append(ne)
                        rec["node_id"] = nid
                        rec["edge_id"] = f"E{new_ids[-1]}"
                        station_by_node[nid].append(rec)
                    else:
                        edges[eid] = e
            station_records.append(rec)

    _attach_stations(hydro_stations, "hydro")
    _attach_stations(rain_stations, "rain")
    if not edges:
        return {"ok": False, "error": "水文站打断后无有效河段"}

    # ---------------------------------------------------------- 4. 假节点合并
    protected = {n for n in station_by_node}
    dissolved = 0
    if opts["dissolve_pseudo_nodes"]:
        for _ in range(len(edges) + 5):
            adj0: dict[int, list[int]] = defaultdict(list)
            for eid, e in edges.items():
                adj0[e["from"]].append(eid)
                adj0[e["to"]].append(eid)
            target = None
            for nid, eids in adj0.items():
                if len(eids) == 2 and nid not in protected and eids[0] != eids[1]:
                    target = (nid, eids[0], eids[1])
                    break
            if target is None:
                break
            nid, e1, e2 = target
            a, b = edges[e1], edges[e2]
            if a["to"] != nid:
                a["coords"] = a["coords"][::-1]
                a["from"], a["to"] = a["to"], a["from"]
                a["digitized"] = not a["digitized"]
            if b["from"] != nid:
                b["coords"] = b["coords"][::-1]
                b["from"], b["to"] = b["to"], b["from"]
                b["digitized"] = not b["digitized"]
            if a["from"] == b["to"]:
                edges.pop(e1, None)
                edges.pop(e2, None)
                dissolved += 1
                continue
            a["coords"] = a["coords"][:-1] + b["coords"]
            a["to"] = b["to"]
            a["length_m"] = round(length_m_planar(a["coords"]), 2)
            if not a["name"]:
                a["name"] = b["name"]
            edges.pop(e2, None)
            dissolved += 1
        if not edges:
            return {"ok": False, "error": "合并假节点后无有效河段"}

    # ---------------------------------------------------------- 5. 节点编号重映射
    adj: dict[int, list[int]] = defaultdict(list)
    for eid, e in edges.items():
        adj[e["from"]].append(eid)
        adj[e["to"]].append(eid)
    node_ids = sorted(adj.keys())
    A = {n: i for i, n in enumerate(node_ids)}
    K = len(node_ids)

    und_adj: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for eid, e in edges.items():
        e["from_i"], e["to_i"] = A[e["from"]], A[e["to"]]
        und_adj[e["from_i"]].append((eid, e["to_i"]))
        und_adj[e["to_i"]].append((eid, e["from_i"]))

    # ---------------------------------------------------------- 6. 流向判定
    elev: list[Optional[float]] = [None] * K
    if dem_ctx is not None:
        dem, meta = dem_ctx["filled"], dem_ctx["meta"]
        for n, i in A.items():
            x, y = centers[n]
            elev[i] = sample_dem(dem, meta, x, y)
    else:
        for eid, e in edges.items():
            v = _pick(e["props"], ELEV_FIELDS)
            if v is None:
                continue
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if elev[e["from_i"]] is None:
                elev[e["from_i"]] = fv
            if elev[e["to_i"]] is None:
                elev[e["to_i"]] = fv

    method = opts["flow_direction"]
    if method == "auto":
        if dem_ctx is not None and any(v is not None for v in elev):
            method = "dem"
        elif any(v is not None for v in elev):
            method = "elevation_attribute"
        else:
            method = "digitized"

    # 连通分量
    seen: set[int] = set()
    components: list[set[int]] = []
    for i in range(K):
        if i in seen:
            continue
        comp = {i}
        dq = deque([i])
        while dq:
            cur = dq.popleft()
            for _eid, other in und_adj[cur]:
                if other not in comp:
                    comp.add(other)
                    dq.append(other)
        seen |= comp
        components.append(comp)

    def _upstream_len_directed(node: int, out_d: dict, in_d: dict) -> float:
        """从 node 逆流而上累计河长（带记忆化，避免重复遍历）。"""
        memo: dict[int, float] = {}
        stack = [(node, False)]
        order: list[int] = []
        visited: set[int] = set()
        while stack:
            cur, done = stack.pop()
            if done:
                order.append(cur)
                continue
            if cur in visited:
                continue
            visited.add(cur)
            stack.append((cur, True))
            for eid in in_d.get(cur, ()):
                up = edges[eid]["from_i"] if edges[eid]["to_i"] == cur else edges[eid]["to_i"]
                if up not in visited:
                    stack.append((up, False))
        for cur in order:
            tot = 0.0
            for eid in in_d.get(cur, ()):
                e = edges[eid]
                up = e["from_i"] if e["to_i"] == cur else e["to_i"]
                tot += e["length_m"] + memo.get(up, 0.0)
            memo[cur] = tot
        return memo.get(node, 0.0)

    def pick_root(comp: set[int]) -> int:
        if len(comp) == 1:
            return next(iter(comp))
        terminals = [i for i in comp if len(und_adj[i]) == 1] or list(comp)
        if method in ("dem", "elevation_attribute"):
            known = [(elev[i], i) for i in terminals if elev[i] is not None]
            if known:
                return min(known)[1]
        out_d: dict[int, list[int]] = defaultdict(list)
        in_d: dict[int, list[int]] = defaultdict(list)
        for eid, e in edges.items():
            if e["from_i"] not in comp:
                continue
            a, b = (e["from_i"], e["to_i"]) if e["digitized"] else (e["to_i"], e["from_i"])
            out_d[a].append(eid)
            in_d[b].append(eid)
        sinks = [i for i in terminals if not out_d.get(i)]
        if not sinks:
            sinks = terminals
        return max(sinks, key=lambda n: _upstream_len_directed(n, out_d, in_d))

    # ---------------------------------------------------------- 7. 定向（由上游指向下游）
    # BFS 自出口向上游扩展；每条河段统一定向为「远离出口的节点 → 靠近出口的节点」
    flip_count = 0
    for comp in components:
        root = pick_root(comp)
        visited = {root}
        dq = deque([root])
        while dq:
            cur = dq.popleft()
            for eid, other in und_adj[cur]:
                if other in visited:
                    continue
                e = edges[eid]
                if e["from_i"] == other and e["to_i"] == cur:
                    pass  # 已满足 other → cur
                elif e["from_i"] == cur and e["to_i"] == other:
                    e["coords"] = e["coords"][::-1]
                    e["from_i"], e["to_i"] = e["to_i"], e["from_i"]
                    e["digitized"] = not e["digitized"]
                    flip_count += 1
                else:
                    continue
                visited.add(other)
                dq.append(other)

    conflict = sum(1 for e in edges.values() if not e["digitized"])
    if method == "digitized" and conflict:
        warnings.append(f"有 {conflict} 个河段的原始数字化方向与推断流向不一致，已按拓扑连通性纠正")

    # ---------------------------------------------------------- 8. 图结构指标
    in_deg = [0] * K
    out_deg = [0] * K
    up_edges: dict[int, list[int]] = defaultdict(list)
    down_edges: dict[int, list[int]] = defaultdict(list)
    for eid, e in edges.items():
        in_deg[e["to_i"]] += 1
        out_deg[e["from_i"]] += 1
        up_edges[e["to_i"]].append(eid)
        down_edges[e["from_i"]].append(eid)

    indeg_tmp = list(in_deg)
    topo: list[int] = []
    q = deque([i for i in range(K) if indeg_tmp[i] == 0])
    while q:
        cur = q.popleft()
        topo.append(cur)
        for eid in down_edges.get(cur, ()):
            v = edges[eid]["to_i"]
            indeg_tmp[v] -= 1
            if indeg_tmp[v] == 0:
                q.append(v)
    for i in range(K):
        if i not in topo:
            topo.append(i)

    for i in topo:
        for eid in down_edges.get(i, ()):
            e = edges[eid]
            ups = up_edges.get(i, [])
            if not ups:
                e["order"] = 1
                e["upstream_length_m"] = 0.0
                e["upstream_edge_count"] = 0
            else:
                os_ = [edges[u].get("order", 1) for u in ups]
                e["order"] = int(os_[0] + 1 if len(set(os_)) == 1 else max(os_))
                e["upstream_length_m"] = round(
                    sum(edges[u]["length_m"] + edges[u].get("upstream_length_m", 0.0) for u in ups), 1
                )
                e["upstream_edge_count"] = len(ups) + sum(int(edges[u].get("upstream_edge_count", 0)) for u in ups)

    up_all: dict[int, set[int]] = {}
    for i in topo:
        s: set[int] = set()
        for eid in up_edges.get(i, ()):
            s.add(eid)
            s |= up_all.get(edges[eid]["from_i"], set())
        up_all[i] = s

    # 上游集水面积（有 DEM 时）
    node_area: dict[int, Optional[float]] = {}
    if dem_ctx is not None:
        acc_grid, meta = dem_ctx["acc"], dem_ctx["meta"]
        cs = meta["cellsize"]
        if abs(cs) > 0.5:
            area_km2 = cs * cs / 1e6
        else:
            lat = meta["yll"] + meta["nrows"] * cs / 2
            area_km2 = (cs * 111320 * _coslat(lat)) * (cs * 110540) / 1e6
        h, w = acc_grid.shape
        for n, i in A.items():
            x, y = centers[n]
            j = int(round((x - meta["xll"]) / cs - 0.5))
            ii = int(round((meta["nrows"] - 0.5) - (y - meta["yll"]) / cs))
            if 0 <= ii < h and 0 <= j < w:
                i0, i1 = max(0, ii - 2), min(h, ii + 3)
                j0, j1 = max(0, j - 2), min(w, j + 3)
                node_area[i] = round(float(acc_grid[i0:i1, j0:j1].max()) * area_km2, 3)
            else:
                node_area[i] = None

        # 站点以上集水面积
        for rec in station_records:
            v = None
            j = int(round((rec["x"] - meta["xll"]) / cs - 0.5))
            ii = int(round((meta["nrows"] - 0.5) - (rec["y"] - meta["yll"]) / cs))
            if 0 <= ii < h and 0 <= j < w:
                i0, i1 = max(0, ii - 3), min(h, ii + 4)
                j0, j1 = max(0, j - 3), min(w, j + 4)
                v = float(acc_grid[i0:i1, j0:j1].max())
            rec["upstream_area_km2"] = round(v * area_km2, 3) if v is not None else None

    # ---------------------------------------------------------- 9. 湖泊挂接
    lake_recs = []
    for geom, props in _extract(lakes, "polygon"):
        c = geom.centroid
        best = (None, 1e18, 0.0)
        for eid, e in edges.items():
            ln = LineString(e["coords"])
            d = ln.distance(c)
            if d < best[1]:
                best = (eid, d, ln.project(c) / max(ln.length, 1e-12))
        lake_recs.append(
            {
                "id": f"L{len(lake_recs) + 1}",
                "name": _feat_name(props) or f"湖泊{len(lake_recs) + 1}",
                "area_km2": round(_polygon_area_km2(geom), 3),
                "centroid": [round(c.x, 7), round(c.y, 7)],
                "edge_id": f"E{best[0]}" if best[0] is not None else None,
                "ratio": round(float(best[2]), 6) if best[0] is not None else None,
                "outline": _simplify_outline(geom),
                "props": _clean_props(props),
            }
        )

    # ---------------------------------------------------------- 10. 输出
    nodes_out = []
    for n, i in A.items():
        x, y = centers[n]
        if in_deg[i] == 0 and out_deg[i] > 0:
            kind = "source"
        elif out_deg[i] == 0 and in_deg[i] > 0:
            kind = "outlet"
        elif in_deg[i] >= 2:
            kind = "junction"
        else:
            kind = "node"
        nodes_out.append(
            {
                "id": f"N{i}",
                "idx": i,
                "x": round(x, 7),
                "y": round(y, 7),
                "kind": kind,
                "in_degree": in_deg[i],
                "out_degree": out_deg[i],
                "elevation": None if elev[i] is None else round(elev[i], 2),
                "upstream_length_m": round(
                    sum(edges[e]["length_m"] + edges[e].get("upstream_length_m", 0.0) for e in up_edges.get(i, ())), 1
                ),
                "upstream_area_km2": node_area.get(i),
                "station_ids": [r["id"] for r in station_by_node.get(n, [])],
                "upstream_edges": sorted(f"E{e}" for e in up_all.get(i, set())),
            }
        )

    edges_out = []
    for eid, e in edges.items():
        edges_out.append(
            {
                "id": f"E{eid}",
                "from_node": f"N{e['from_i']}",
                "to_node": f"N{e['to_i']}",
                "name": e["name"],
                "order": int(e.get("order", 1)),
                "length_m": e["length_m"],
                "upstream_length_m": e.get("upstream_length_m", 0.0),
                "upstream_edge_count": int(e.get("upstream_edge_count", 0)),
                "coords": e["coords"],
                "props": e["props"],
            }
        )

    unattached = [s["name"] for s in station_records if not s["attached"]]
    if unattached:
        warnings.append(
            f"{len(unattached)} 个站点超出挂接距离（{opts['station_snap_max_m']:.0f} m），未挂接到河段："
            + "、".join(unattached[:8])
            + ("…" if len(unattached) > 8 else "")
        )

    stats = {
        "node_count": len(nodes_out),
        "edge_count": len(edges_out),
        "source_count": sum(1 for n in nodes_out if n["kind"] == "source"),
        "junction_count": sum(1 for n in nodes_out if n["kind"] == "junction"),
        "outlet_count": sum(1 for n in nodes_out if n["kind"] == "outlet"),
        "dissolved_pseudo_nodes": dissolved,
        "flipped_for_flow": flip_count,
        "total_length_km": round(sum(e["length_m"] for e in edges_out) / 1000.0, 2),
        "max_order": max([e["order"] for e in edges_out], default=0),
        "station_count": len(station_records),
        "station_attached": sum(1 for s in station_records if s["attached"]),
        "lake_count": len(lake_recs),
    }

    return {
        "ok": True,
        "method": method,
        "stats": stats,
        "nodes": nodes_out,
        "edges": edges_out,
        "stations": station_records,
        "lakes": lake_recs,
        "warnings": warnings,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


# ================================================================= 命名辅助
def identify_mainstem(topo: dict) -> list[str]:
    """从出口逆流而上，逐级选择上游规模最大的河段，返回干流河段序列。"""
    edges = {e["id"]: e for e in topo.get("edges", [])}
    nodes = {n["id"]: n for n in topo.get("nodes", [])}
    if not edges:
        return []
    outlets = [n for n in nodes.values() if n["out_degree"] == 0] or list(nodes.values())
    outlet = max(outlets, key=lambda n: n["upstream_length_m"])
    upstream_of: dict[str, list[str]] = defaultdict(list)
    for e in edges.values():
        upstream_of[e["to_node"]].append(e["id"])
    ids: list[str] = []
    cur = outlet["id"]
    for _ in range(len(edges) + 2):
        ups = upstream_of.get(cur, [])
        if not ups:
            break
        eid = max(ups, key=lambda i: edges[i]["upstream_length_m"] + edges[i]["length_m"])
        ids.append(eid)
        cur = edges[eid]["from_node"]
    return ids


def name_rivers(
    topo: dict,
    main_name: str = "干流",
    trib_names: Optional[Iterable[str]] = None,
    fallback: str = "{main}支流{n}",
) -> dict[str, str]:
    """给拓扑中的河段起名：干流用主名，支流按上游规模依次命名。"""
    edges = topo.get("edges", [])
    main = set(identify_mainstem(topo))
    names: dict[str, str] = {eid: main_name for eid in main}
    others = sorted(
        [e for e in edges if e["id"] not in main],
        key=lambda e: -(e.get("upstream_length_m", 0) + e.get("length_m", 0)),
    )
    pool = list(trib_names or [])
    for i, e in enumerate(others):
        names[e["id"]] = pool[i] if i < len(pool) else fallback.format(main=main_name, n=i + 1)
    return names


# ================================================================= 辅助
def _polygon_area_km2(geom) -> float:
    try:
        c = geom.centroid
    except Exception:
        return 0.0
    return float(geom.area * (111.32**2) * _coslat(c.y))


def _simplify_outline(geom, max_pts: int = 40) -> list:
    """湖泊轮廓抽稀成示意多边形（给概化图用）。"""
    try:
        g = geom.simplify(0.0008, preserve_topology=True)
        if g.geom_type == "MultiPolygon":
            g = max(g.geoms, key=lambda p: p.area)
        if g.geom_type != "Polygon" or g.is_empty:
            return []
        coords = list(g.exterior.coords)
        if len(coords) > max_pts:
            step = max(1, len(coords) // max_pts)
            coords = coords[::step]
            if coords[0] != coords[-1]:
                coords.append(coords[0])
        return [[round(c[0], 7), round(c[1], 7)] for c in coords]
    except Exception:
        return []


def topology_to_geojson(topo: dict) -> dict:
    """把拓扑结果转成可视化图层（河段带方向、节点、拓扑连线）。"""
    feats = []
    for e in topo.get("edges", []):
        feats.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": e["coords"]},
                "properties": {
                    "kind": "river_edge",
                    "id": e["id"],
                    "name": e["name"],
                    "order": e["order"],
                    "length_km": round(e["length_m"] / 1000, 2),
                    "from_node": e["from_node"],
                    "to_node": e["to_node"],
                    "upstream_km": round(e["upstream_length_m"] / 1000, 2),
                },
            }
        )
    for n in topo.get("nodes", []):
        feats.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [n["x"], n["y"]]},
                "properties": {
                    "kind": "node",
                    "id": n["id"],
                    "node_kind": n["kind"],
                    "in_degree": n["in_degree"],
                    "out_degree": n["out_degree"],
                    "elevation": n["elevation"],
                    "upstream_km": round(n["upstream_length_m"] / 1000, 2),
                    "upstream_area_km2": n["upstream_area_km2"],
                },
            }
        )
    for s in topo.get("stations", []):
        feats.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [s["x"], s["y"]]},
                "properties": {
                    "kind": "station_node",
                    "id": s["id"],
                    "name": s["name"],
                    "station_type": s["type"],
                    "edge_id": s["edge_id"],
                    "snap_m": s["snap_m"],
                    "attached": s["attached"],
                    "upstream_area_km2": s.get("upstream_area_km2"),
                },
            }
        )
    return {"type": "FeatureCollection", "features": feats}
