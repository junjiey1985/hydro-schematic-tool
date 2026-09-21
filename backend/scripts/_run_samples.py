import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import dem as demmod  # noqa: E402
from app.core.synth import generate_samples  # noqa: E402

_orig_extract = demmod.extract_network


def timed_extract(dem, meta, **kw):
    from app.core.dem import (
        fill_depressions,
        d8_directions,
        flow_accumulation,
        extract_stream_mask,
        vectorize_streams,
    )

    t0 = time.time()
    nodata = meta.get("nodata", -9999.0)
    filled = fill_depressions(dem, nodata=nodata)
    t1 = time.time()
    fdir, di, dj = d8_directions(filled, nodata=nodata)
    t2 = time.time()
    acc = flow_accumulation(filled, di, dj)
    t3 = time.time()
    mask = extract_stream_mask(acc, kw.get("threshold", 1100), dem, nodata=nodata)
    t4 = time.time()
    segs = vectorize_streams(mask, filled, acc, di, dj, meta,
                             simplify_m=kw.get("simplify_m", 110.0), smooth=kw.get("smooth", True))
    t5 = time.time()
    print(f"   [timing] fill={t1-t0:.1f}s d8={t2-t1:.1f}s acc={t3-t2:.1f}s mask={t4-t3:.1f}s vec={t5-t4:.1f}s", flush=True)
    return {"filled": filled, "fdir": fdir, "down_i": di, "down_j": dj, "acc": acc,
            "mask": mask, "segments": segs, "threshold": kw.get("threshold", 1100)}


demmod.extract_network = timed_extract
import app.core.synth as synth_mod  # noqa: E402

synth_mod.extract_network = timed_extract

t = time.time()
info = generate_samples(Path("../data/samples"), threshold=600)
print("elapsed %.1fs" % (time.time() - t))
print(info["stats"])
