"""水系概化图（拓扑关系示意图）自动生成。

思路：以拓扑图为基础，把"按地理坐标绘制"的河网转换为"按拓扑层级绘制"的
示意图 —— 下游在底、上游在顶，干支流按层级分叉，再在此基础上人工微调。

    depth：节点到出口的层级数（出口 = 0，向上递增）
    row  ：max_depth - depth，使出口落在最下方
    x    ：按 DFS 顺序做的 tidy-tree 紧凑布局，保证分支不交叉

生成结果是一份「可直接编辑的坐标稿」：每个节点 / 河段 / 站点 / 湖泊都有
明确的示意坐标，人工拖动后回写即可，不需要每次重算。
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from typing import Optional

DEFAULT_SCHEMATIC_OPTIONS = {
    "orientation": "vertical",  # vertical（上游在上）| horizontal（上游在左）
    "level_gap": 110.0,
    "leaf_gap": 64.0,
    "max_width": 4200.0,      # 总宽超过该值时自动压缩叶节点间距
    "min_leaf_gap": 22.0,
    "max_height": 4600.0,     # 总高超过该值时自动压缩层级间距
    "min_level_gap": 34.0,
    "station_offset": 34.0,
    "lake_scale": 3.2,
    "padding": 90.0,
    "show_nodes": True,
}


def build_schematic(topo: dict, options: Optional[dict] = None, previous: Optional[dict] = None) -> dict:
    """由拓扑生成概化图初始布局。

    previous: 已有的概化图（若提供，保留人工编辑过的元素位置）
    """
    opts = dict(DEFAULT_SCHEMATIC_OPTIONS)
    opts.update(options or {})
    if not topo or not topo.get("ok"):
        return {"ok": False, "error": "尚未构建拓扑，无法生成概化图"}

    nodes = {n["id"]: dict(n) for n in topo.get("nodes", [])}
    edges = {e["id"]: dict(e) for e in topo.get("edges", [])}
    stations = [dict(s) for s in topo.get("stations", [])]
    lakes = [dict(l) for l in topo.get("lakes", [])]
    if not nodes or not edges:
        return {"ok": False, "error": "拓扑数据为空"}

    # ---------------------------------------------------------- 1. 找出口做根
    outlets = [n for n in nodes.values() if n["out_degree"] == 0]
    if not outlets:
        outlets = [max(nodes.values(), key=lambda n: n["upstream_length_m"])]
    root = max(outlets, key=lambda n: n["upstream_length_m"])["id"]

    adj: dict[str, list[str]] = defaultdict(list)  # node -> 上游节点
    for e in edges.values():
        adj[e["to_node"]].append(e["from_node"])

    # ---------------------------------------------------------- 2. 层级 depth
    depth: dict[str, int] = {root: 0}
    dq = deque([root])
    while dq:
        cur = dq.popleft()
        for up in adj.get(cur, ()):
            nd = depth[cur] + 1
            if up not in depth or nd > depth[up]:
                depth[up] = nd
                dq.append(up)
    for nid in nodes:
        depth.setdefault(nid, 0)
    max_depth = max(depth.values())

    # ---------------------------------------------------------- 3. 树化（每个节点保留主父节点）
    children: dict[str, list[str]] = defaultdict(list)  # 下游节点 -> 上游节点
    edge_of: dict[tuple[str, str], str] = {}
    extra_links: list[str] = []
    for eid, e in edges.items():
        key = (e["to_node"], e["from_node"])
        edge_of[key] = eid
        children[e["to_node"]].append(e["from_node"])

    # 同一上游节点被多个下游节点共享（网河/分汊）时，只保留主连接，其余作为附加连线
    candidates: dict[str, list[tuple[float, str, str]]] = defaultdict(list)
    for nid in nodes:
        for u in children.get(nid, []):
            if u in nodes and u != nid:
                candidates[u].append((_edge_weight(edges, edge_of, nid, u), nid, edge_of[(nid, u)]))
    parent_of: dict[str, str] = {}
    for u, lst in candidates.items():
        lst.sort(key=lambda t: (-t[0], t[1]))
        parent_of[u] = lst[0][1]
        for _w, _down, eid in lst[1:]:
            extra_links.append(eid)

    # 主树 children（去掉指向自身的边，避免环导致死循环）
    tree_children: dict[str, list[str]] = defaultdict(list)
    for up, down in parent_of.items():
        if up != down:
            tree_children[down].append(up)

    # ---------------------------------------------------------- 4. tidy 布局
    level_gap = float(opts["level_gap"])
    pad = float(opts["padding"])
    leaf_gap = float(opts["leaf_gap"])
    leaf_count = max(1, sum(1 for nid in nodes if not tree_children.get(nid)))
    # 叶节点太多时自动压缩间距，避免概化图宽得没法看
    if leaf_count * leaf_gap > float(opts["max_width"]):
        leaf_gap = max(float(opts["min_leaf_gap"]), float(opts["max_width"]) / leaf_count)
    # 层级太深时同样压缩层间距
    if (max_depth + 1) * level_gap > float(opts["max_height"]):
        level_gap = max(float(opts["min_level_gap"]), float(opts["max_height"]) / (max_depth + 1))

    xs: dict[str, float] = {}
    next_slot = [0.0]
    size_memo: dict[str, int] = {}

    def _subtree_size(nid: str) -> int:
        if nid in size_memo:
            return size_memo[nid]
        size_memo[nid] = 1  # 占位，防环
        kids = [k for k in tree_children.get(nid, []) if k != nid]
        total = 1 if not kids else sum(_subtree_size(k) for k in kids)
        size_memo[nid] = total
        return total

    def _layout_from(seed: str) -> None:
        """迭代式后序：叶子先分槽位，内部节点取子节点 x 均值。带环保护。"""
        stack: list[tuple[str, bool]] = [(seed, False)]
        on_stack = {seed}
        while stack:
            nid, done = stack.pop()
            if done:
                on_stack.discard(nid)
                kids = [k for k in tree_children.get(nid, []) if k in xs and k != nid]
                if not kids:
                    if nid not in xs:
                        xs[nid] = next_slot[0]
                        next_slot[0] += leaf_gap
                else:
                    xs[nid] = sum(xs[k] for k in kids) / len(kids)
                continue
            if nid in xs:
                continue
            kids = sorted(
                [k for k in tree_children.get(nid, []) if k not in on_stack and k != nid],
                key=lambda k: (-_subtree_size(k), -depth.get(k, 0), k),
            )
            stack.append((nid, True))
            for k in reversed(kids):
                if k not in xs and k not in on_stack:
                    on_stack.add(k)
                    stack.append((k, False))

    _layout_from(root)
    for nid in nodes:  # 未连到主树的孤立部分
        if nid not in xs:
            _layout_from(nid)
    for nid in nodes:
        xs.setdefault(nid, 0.0)

    def _pos(nid: str) -> tuple[float, float]:
        row = max_depth - depth.get(nid, 0)
        x = pad + xs.get(nid, 0.0)
        y = pad + row * level_gap
        if opts["orientation"] == "horizontal":
            return y, x
        return x, y

    node_pos = {nid: _pos(nid) for nid in nodes}

    # ---------------------------------------------------------- 5. 河段折线
    sch_edges: list[dict] = []
    edge_points: dict[str, list[list[float]]] = {}
    for eid, e in edges.items():
        p0 = node_pos.get(e["from_node"])
        p1 = node_pos.get(e["to_node"])
        if p0 is None or p1 is None:
            continue
        pts = [list(p0), list(p1)]
        edge_points[eid] = pts
        sch_edges.append(
            {
                "id": eid,
                "from_node": e["from_node"],
                "to_node": e["to_node"],
                "name": e.get("name") or "",
                "order": int(e.get("order", 1)),
                "length_km": round(e.get("length_m", 0) / 1000.0, 2),
                "upstream_km": round(e.get("upstream_length_m", 0) / 1000.0, 2),
                "points": pts,
                "kind": "extra" if eid in extra_links else "main",
                "geo": _edge_midpoint_geo(e),
            }
        )

    # ---------------------------------------------------------- 6. 站点摆放
    sch_stations: list[dict] = []
    for s in stations:
        pts = edge_points.get(s.get("edge_id") or "")
        if pts:
            x, y, tx, ty = _point_on_polyline(pts, float(s.get("ratio") or 0.5))
            side = _side_of(s["x"], s["y"], pts, s.get("edge_id") or "", edges)
        else:
            x, y = node_pos.get(s.get("node_id") or "", (pad, pad))
            tx, ty, side = 1.0, 0.0, 1
        off = float(opts["station_offset"])
        off = off * (1.0 if s["type"] == "hydro" else 0.72)
        nx, ny = _perp(tx, ty)
        sch_stations.append(
            {
                "id": s["id"],
                "name": s["name"],
                "type": s["type"],
                "edge_id": s.get("edge_id"),
                "node_id": s.get("node_id"),
                "ratio": s.get("ratio"),
                "on_edge": [round(x, 2), round(y, 2)],
                "x": round(x + nx * off * side, 2),
                "y": round(y + ny * off * side, 2),
                "offset": round(off * side, 2),
                "attached": bool(s.get("attached")),
                "upstream_area_km2": s.get("upstream_area_km2"),
                "geo": [s["x"], s["y"]],
                "props": s.get("props") or {},
            }
        )

    # ---------------------------------------------------------- 7. 湖泊摆放
    sch_lakes: list[dict] = []
    for i, lk in enumerate(lakes):
        pts = edge_points.get(lk.get("edge_id") or "")
        if pts:
            x, y, tx, ty = _point_on_polyline(pts, float(lk.get("ratio") or 0.5))
        else:
            x, y, tx, ty = pad, pad
        nx, ny = _perp(tx, ty)
        off = 44.0
        cx, cy = x + nx * off, y + ny * off
        poly = _scale_outline(lk.get("outline") or [], lk.get("area_km2") or 1.0, float(opts["lake_scale"]))
        if poly:
            gx = sum(p[0] for p in poly) / len(poly)
            gy = sum(p[1] for p in poly) / len(poly)
            poly = [[round(cx + p[0] - gx, 2), round(cy + p[1] - gy, 2)] for p in poly]
        else:
            r = 14.0
            poly = [[cx, cy - r], [cx + r, cy], [cx, cy + r], [cx - r, cy]]
        sch_lakes.append(
            {
                "id": lk["id"],
                "name": lk["name"],
                "area_km2": lk.get("area_km2"),
                "edge_id": lk.get("edge_id"),
                "x": round(cx, 2),
                "y": round(cy, 2),
                "points": poly,
                "geo": lk.get("centroid"),
                "props": lk.get("props") or {},
            }
        )

    # ---------------------------------------------------------- 8. 保留人工编辑
    manual: dict[str, dict] = {}
    if previous:
        manual = previous.get("manual") or {}
        _apply_manual(manual, node_pos, edge_points, sch_edges, sch_stations, sch_lakes)

    # 重新贴合：拖动节点后，河段端点跟随
    _snap_edges_to_nodes(sch_edges, node_pos)

    xs_all = [p[0] for p in node_pos.values()] + [s["x"] for s in sch_stations] + [l["x"] for l in sch_lakes]
    ys_all = [p[1] for p in node_pos.values()] + [s["y"] for s in sch_stations] + [l["y"] for l in sch_lakes]
    for e in sch_edges:
        for p in e["points"]:
            xs_all.append(p[0])
            ys_all.append(p[1])

    bbox = [min(xs_all), min(ys_all), max(xs_all), max(ys_all)] if xs_all else [0, 0, 100, 100]

    sch_nodes = []
    for nid, n in nodes.items():
        x, y = node_pos[nid]
        sch_nodes.append(
            {
                "id": nid,
                "x": round(x, 2),
                "y": round(y, 2),
                "kind": n["kind"],
                "level": depth.get(nid, 0),
                "elevation": n.get("elevation"),
                "upstream_km": round(n.get("upstream_length_m", 0) / 1000.0, 2),
                "upstream_area_km2": n.get("upstream_area_km2"),
                "station_ids": n.get("station_ids") or [],
                "edge_ids": sorted(
                    [e["id"] for e in edges.values() if e["from_node"] == nid or e["to_node"] == nid]
                ),
                "geo": [n["x"], n["y"]],
            }
        )

    return {
        "ok": True,
        "root": root,
        "options": opts,
        "bbox": [round(v, 2) for v in bbox],
        "max_level": max_depth,
        "nodes": sch_nodes,
        "edges": sch_edges,
        "stations": sch_stations,
        "lakes": sch_lakes,
        "extra_links": extra_links,
        "manual": manual,
        "generated_at": _now(),
    }


# ================================================================= 工具
def _now() -> str:
    import time

    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _edge_weight(edges: dict, edge_of: dict, down: str, up: str) -> float:
    e = edges.get(edge_of.get((down, up), ""))
    if not e:
        return 0.0
    return float(e.get("upstream_length_m", 0.0)) + float(e.get("length_m", 0.0))


def _edge_midpoint_geo(e: dict) -> Optional[list[float]]:
    c = e.get("coords") or []
    if not c:
        return None
    m = c[len(c) // 2]
    return [round(m[0], 7), round(m[1], 7)]


def _perp(tx: float, ty: float) -> tuple[float, float]:
    n = math.hypot(tx, ty) or 1.0
    return -ty / n, tx / n


def _point_on_polyline(pts: list[list[float]], ratio: float) -> tuple[float, float, float, float]:
    """沿折线按比例取点，返回 (x, y, 切线x, 切线y)。"""
    if len(pts) < 2:
        p = pts[0] if pts else [0.0, 0.0]
        return p[0], p[1], 1.0, 0.0
    segs = []
    total = 0.0
    for i in range(len(pts) - 1):
        d = math.dist(pts[i], pts[i + 1])
        segs.append(d)
        total += d
    if total <= 0:
        return pts[0][0], pts[0][1], 1.0, 0.0
    target = max(0.0, min(1.0, ratio)) * total
    acc = 0.0
    for i, d in enumerate(segs):
        if acc + d >= target or i == len(segs) - 1:
            t = (target - acc) / d if d > 0 else 0.0
            t = max(0.0, min(1.0, t))
            x0, y0 = pts[i]
            x1, y1 = pts[i + 1]
            return x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, x1 - x0, y1 - y0
        acc += d
    return pts[-1][0], pts[-1][1], 1.0, 0.0


def _side_of(sx: float, sy: float, pts: list[list[float]], eid: str, edges: dict) -> int:
    """判断站点在地理上位于河段的哪一侧，用于决定示意图中的偏移方向。"""
    e = edges.get(eid)
    if not e or not e.get("coords"):
        return 1
    c = e["coords"]
    i = max(0, min(len(c) - 2, len(c) // 2 - 1))
    ax, ay = c[i]
    bx, by = c[i + 1]
    cross = (bx - ax) * (sy - ay) - (by - ay) * (sx - ax)
    return 1 if cross >= 0 else -1


def _scale_outline(outline: list, area_km2: float, scale: float) -> list[list[float]]:
    """把地理轮廓缩放成示意尺寸（北向上）。"""
    if not outline or len(outline) < 4:
        return []
    xs = [p[0] for p in outline]
    ys = [p[1] for p in outline]
    w = max(xs) - min(xs)
    h = max(ys) - min(ys)
    if w <= 0 or h <= 0:
        return []
    target = max(18.0, min(96.0, math.sqrt(max(area_km2, 1.0)) * scale * 2.4))
    k = target / max(w, h)
    cx = (max(xs) + min(xs)) / 2
    cy = (max(ys) + min(ys)) / 2
    return [[(p[0] - cx) * k, -(p[1] - cy) * k] for p in outline]  # y 轴翻转（屏幕坐标向下）


def _snap_edges_to_nodes(sch_edges: list[dict], node_pos: dict[str, tuple[float, float]]) -> None:
    """让河段首尾端点始终粘在节点上（拖动节点后自动跟随）。"""
    for e in sch_edges:
        p0 = node_pos.get(e["from_node"])
        p1 = node_pos.get(e["to_node"])
        if not p0 or not p1 or len(e["points"]) < 2:
            continue
        e["points"][0] = [p0[0], p0[1]]
        e["points"][-1] = [p1[0], p1[1]]


def _apply_manual(
    manual: dict,
    node_pos: dict,
    edge_points: dict,
    sch_edges: list[dict],
    sch_stations: list[dict],
    sch_lakes: list[dict],
) -> None:
    """把人工编辑结果覆盖到自动布局之上。"""
    for nid, patch in (manual.get("nodes") or {}).items():
        if nid in node_pos and "x" in patch and "y" in patch:
            node_pos[nid] = (float(patch["x"]), float(patch["y"]))
    for eid, patch in (manual.get("edges") or {}).items():
        pts = patch.get("points")
        if pts:
            edge_points[eid] = [[float(p[0]), float(p[1])] for p in pts]
    for e in sch_edges:
        pts = edge_points.get(e["id"])
        if pts:
            e["points"] = pts
    for s in sch_stations:
        patch = (manual.get("stations") or {}).get(s["id"])
        if patch and "x" in patch and "y" in patch:
            s["x"] = float(patch["x"])
            s["y"] = float(patch["y"])
    for lk in sch_lakes:
        patch = (manual.get("lakes") or {}).get(lk["id"])
        if patch:
            if "points" in patch and patch["points"]:
                lk["points"] = [[float(p[0]), float(p[1])] for p in patch["points"]]
            if "x" in patch and "y" in patch:
                dx, dy = float(patch["x"]) - lk["x"], float(patch["y"]) - lk["y"]
                lk["x"], lk["y"] = float(patch["x"]), float(patch["y"])
                lk["points"] = [[p[0] + dx, p[1] + dy] for p in lk["points"]]


def apply_manual_edits(schematic: dict, edits: dict) -> dict:
    """把前端拖拽结果合并进概化图（增量、可反复调用）。"""
    manual = schematic.setdefault("manual", {})
    for group in ("nodes", "edges", "stations", "lakes"):
        manual.setdefault(group, {})
        for k, v in (edits.get(group) or {}).items():
            manual[group].setdefault(k, {}).update(v)

    nodes = {n["id"]: n for n in schematic["nodes"]}
    for nid, patch in (edits.get("nodes") or {}).items():
        if nid in nodes:
            nodes[nid]["x"] = round(float(patch.get("x", nodes[nid]["x"])), 2)
            nodes[nid]["y"] = round(float(patch.get("y", nodes[nid]["y"])), 2)

    for e in schematic["edges"]:
        patch = (edits.get("edges") or {}).get(e["id"])
        if patch and patch.get("points"):
            e["points"] = [[round(float(p[0]), 2), round(float(p[1]), 2)] for p in patch["points"]]
        p0 = nodes.get(e["from_node"])
        p1 = nodes.get(e["to_node"])
        if p0 and p1 and len(e["points"]) >= 2:
            e["points"][0] = [p0["x"], p0["y"]]
            e["points"][-1] = [p1["x"], p1["y"]]

    for s in schematic["stations"]:
        patch = (edits.get("stations") or {}).get(s["id"])
        if patch:
            s["x"] = round(float(patch.get("x", s["x"])), 2)
            s["y"] = round(float(patch.get("y", s["y"])), 2)
            if "name" in patch:
                s["name"] = patch["name"]
            if "type" in patch:
                s["type"] = patch["type"]

    for lk in schematic["lakes"]:
        patch = (edits.get("lakes") or {}).get(lk["id"])
        if patch:
            if patch.get("points"):
                lk["points"] = [[round(float(p[0]), 2), round(float(p[1]), 2)] for p in patch["points"]]
                if "x" not in patch:
                    lk["x"] = round(sum(p[0] for p in lk["points"]) / len(lk["points"]), 2)
                    lk["y"] = round(sum(p[1] for p in lk["points"]) / len(lk["points"]), 2)
            if "name" in patch:
                lk["name"] = patch["name"]

    if edits.get("remove"):
        for group, ids in (edits["remove"] or {}).items():
            if group in ("nodes", "edges", "stations", "lakes"):
                idlist = set(ids)
                schematic[group] = [x for x in schematic[group] if x["id"] not in idlist]
                for i in idlist:
                    schematic.get("manual", {}).get(group, {}).pop(i, None)

    if edits.get("add"):
        for group, items in (edits["add"] or {}).items():
            if group in ("stations", "lakes", "nodes", "edges"):
                schematic.setdefault(group, []).extend(items)

    _recompute_bbox(schematic)
    return schematic


def _recompute_bbox(schematic: dict) -> None:
    xs, ys = [], []
    for n in schematic.get("nodes", []):
        xs.append(n["x"])
        ys.append(n["y"])
    for e in schematic.get("edges", []):
        for p in e.get("points", []):
            xs.append(p[0])
            ys.append(p[1])
    for s in schematic.get("stations", []):
        xs.append(s["x"])
        ys.append(s["y"])
    for lk in schematic.get("lakes", []):
        xs.append(lk["x"])
        ys.append(lk["y"])
        for p in lk.get("points", []):
            xs.append(p[0])
            ys.append(p[1])
    if xs:
        schematic["bbox"] = [round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2)]
