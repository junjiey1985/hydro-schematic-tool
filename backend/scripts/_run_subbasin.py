"""子流域划分自测：直接在真实项目上跑算法并打印结果。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import storage as st  # noqa: E402
from app.core.subbasin import delineate_subbasins, report_rows, subbasin_to_geojson  # noqa: E402
from app.routers.deps import dem_context  # noqa: E402


def main(pid: str, mode: str = "station") -> None:
    topo = st.read_topology(pid)
    if not topo:
        print("没有拓扑，请先构建拓扑")
        return
    ctx = dem_context(pid)
    if not ctx:
        print("没有 DEM")
        return
    print(f"DEM {ctx['meta']['ncols']}x{ctx['meta']['nrows']}")
    print("水文站：", [(s.get("name"), s.get("attached")) for s in topo.get("stations", []) if s.get("type") == "hydro"])
    res = delineate_subbasins(
        ctx, topo, options={"mode": mode, "min_area_km2": 30, "snap_max_m": 12000, "include_outlet": True}
    )
    if not res.get("ok"):
        print("失败：", res.get("error"))
        return
    print("统计：", json.dumps(res["stats"], ensure_ascii=False))
    for row in report_rows(res):
        print(json.dumps(row, ensure_ascii=False))
    for sb in res["subbasins"]:
        print(
            f"  {sb['code']} 面积{sb['area_km2']} 河长{sb['river_length_km']} 父{sb['parent']} "
            f"上游{sb['upstream_subbasins']} 站{(sb.get('outlet_station') or {}).get('name')} "
            f"边界点{len(sb['boundary'] or [])} 雨量站{len(sb['rain_stations'])}"
        )
    gj = subbasin_to_geojson(res)
    print("GeoJSON 要素：", len(gj["features"]))
    for w in res["warnings"]:
        print("警告：", w)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "p_dee39fceeab6", sys.argv[2] if len(sys.argv) > 2 else "station")
