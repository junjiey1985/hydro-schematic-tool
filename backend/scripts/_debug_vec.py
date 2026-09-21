import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.dem import (  # noqa: E402
    d8_directions,
    extract_stream_mask,
    fill_depressions,
    flow_accumulation,
    read_ascii_grid,
    vectorize_streams,
)

dem, meta = read_ascii_grid(Path("../data/samples/dem.asc"))
nodata = meta["nodata"]
filled = fill_depressions(dem, nodata=nodata)
fdir, di, dj = d8_directions(filled, nodata=nodata)
acc = flow_accumulation(filled, di, dj)
mask = extract_stream_mask(acc, 1100, dem, nodata=nodata)
print("河网栅格数:", int(mask.sum()))

for minlen in (0.0, 300.0):
    segs = vectorize_streams(mask, filled, acc, di, dj, meta, simplify_m=110.0, smooth=True, min_length_m=minlen)
    print(f"--- min_length_m={minlen}: {len(segs)} 段")

# 用 cell 直接检查链式关系
h, w = mask.shape
flat_down = np.where(di >= 0, di * w + dj, -1).ravel()
in_stream = np.zeros(h * w, dtype=bool)
in_stream[np.flatnonzero(mask.ravel())] = True
sf = np.flatnonzero(mask.ravel())
ds = flat_down[sf]
ds_valid = np.where((ds >= 0) & in_stream[np.maximum(ds, 0)], ds, -1)
print("下游不在河网内的栅格数（应为出口数）:", int((ds_valid < 0).sum()))
indeg = np.bincount(ds_valid[ds_valid >= 0], minlength=h * w)
print("indeg==0 的栅格数（河源）:", int((indeg[sf] == 0).sum()))
print("indeg>=2 的栅格数（汇流）:", int((indeg[sf] >= 2).sum()))
print("indeg==1 的栅格数:", int((indeg[sf] == 1).sum()))

# 用最小长度 0 的段做连通性检查
segs0 = vectorize_streams(mask, filled, acc, di, dj, meta, simplify_m=110.0, smooth=True, min_length_m=0.0)
pts = []
for s in segs0:
    pts.append(tuple(np.round(s["coords"][0], 7)))
    pts.append(tuple(np.round(s["coords"][-1], 7)))
cnt = Counter(pts)
print("端点重合分布:", dict(Counter(cnt.values())))
