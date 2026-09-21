import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.dem import (  # noqa: E402
    d8_directions,
    extract_stream_mask,
    fill_depressions,
    flow_accumulation,
    vectorize_streams,
)
from app.core.synth import cell_size_m, make_dem  # noqa: E402

dem, meta = make_dem()
nodata = meta["nodata"]
filled = fill_depressions(dem, nodata=nodata)
di, dj = d8_directions(filled, nodata=nodata)[1:]
acc = flow_accumulation(filled, di, dj)
mdx, mdy = cell_size_m(meta)
cell_km2 = abs(mdx * mdy) / 1e6
print(f"单元面积 {cell_km2:.4f} km²，研究区 {meta['ncols']*meta['nrows']*cell_km2:.0f} km²")

for t in (450, 600, 700):
    mask = extract_stream_mask(acc, t, dem, nodata=nodata)
    segs = vectorize_streams(mask, filled, acc, di, dj, meta, simplify_m=110.0, smooth=True)
    order = max(s["order"] for s in segs)
    total = sum(s["length_m"] for s in segs) / 1000
    print(f"阈值 {t:5d} (≈{t*cell_km2:6.1f} km²): 栅格 {int(mask.sum()):6d}  河段 {len(segs):4d}  最大级别 {order}  总长 {total:7.0f} km")
