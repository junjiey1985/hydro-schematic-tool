import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shapely.geometry import LineString, Point, shape  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

from app.core.shp_io import read_shapefile  # noqa: E402

gj = read_shapefile(Path("../data/samples/河流.shp"))
print("features:", len(gj["features"]))
lines = [shape(f["geometry"]) for f in gj["features"]]

merged = unary_union(lines)
print("merged:", merged.geom_type, "pieces:", len(list(merged.geoms)) if merged.geom_type != "LineString" else 1)

pieces = list(merged.geoms) if merged.geom_type != "LineString" else [merged]
print("piece count:", len(pieces))

# 端点重合度
def key(p):
    return (round(p[0], 6), round(p[1], 6))

ends = Counter()
for p in pieces:
    c = list(p.coords)
    ends[key(c[0])] += 1
    ends[key(c[-1])] += 1

deg = Counter(ends.values())
print("端点重合分布 (重合次数->端点数):", dict(deg))
print("仅出现1次的端点(悬挂):", sum(1 for v in ends.values() if v == 1))
print("出现3+次的端点(汇流点):", sum(1 for v in ends.values() if v >= 3))

# 检查是否存在坐标略有差异但很近的端点
pts = []
for p in pieces:
    c = list(p.coords)
    pts.append(c[0])
    pts.append(c[-1])
near = 0
for i in range(len(pts)):
    for j in range(i + 1, len(pts)):
        d = (pts[i][0] - pts[j][0]) ** 2 + (pts[i][1] - pts[j][1]) ** 2
        if 0 < d < (2e-4) ** 2:
            near += 1
print("距离很近但不完全重合的端点对数:", near)

# 连通性（用节点簇）
tol = 20 / 111320.0
nodes = []
labels = []
for x, y in pts:
    found = None
    for i, (nx, ny) in enumerate(nodes):
        if ((x - nx) * 0.86) ** 2 + (y - ny) ** 2 < tol * tol:
            found = i
            break
    if found is None:
        nodes.append((x, y))
        labels.append(len(nodes) - 1)
    else:
        labels.append(found)
print("聚簇后节点数:", len(nodes))

import networkx as nx  # noqa: E402

G = nx.Graph()
for i in range(len(nodes)):
    G.add_node(i)
for k, p in enumerate(pieces):
    a, b = labels[2 * k], labels[2 * k + 1]
    if a != b:
        G.add_edge(a, b)
print("分量数:", nx.number_connected_components(G))
sizes = sorted((len(c) for c in nx.connected_components(G)), reverse=True)
print("分量规模:", sizes[:15])
