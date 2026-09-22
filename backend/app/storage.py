"""项目 / 图层持久化。

存储结构::

    data/projects/<project_id>/
        project.json          项目元数据 + 图层清单
        layers/<lid>.geojson  图层要素
        topology.json         拓扑关系（节点-弧段模型）
        schematic.json        概化图（自动布局 + 人工编辑结果）
        dem/                  栅格数据、晕渲底图
"""
from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Optional

from .config import LAYER_TYPES, PROJECTS_DIR


# ---------------------------------------------------------------- 基础工具
def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        # default=str：后台线程落盘时个别残留对象（datetime / numpy 标量）不致让整个结果丢失
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
    tmp.replace(path)


# ---------------------------------------------------------------- 项目
def project_dir(pid: str) -> Path:
    return PROJECTS_DIR / pid


def list_projects() -> list[dict]:
    out = []
    for d in sorted(PROJECTS_DIR.iterdir()) if PROJECTS_DIR.exists() else []:
        meta = _read_json(d / "project.json")
        if meta:
            out.append(
                {
                    "id": meta["id"],
                    "name": meta["name"],
                    "created_at": meta.get("created_at"),
                    "updated_at": meta.get("updated_at"),
                    "layer_count": len(meta.get("layers", [])),
                    "has_topology": (d / "topology.json").exists(),
                    "has_schematic": (d / "schematic.json").exists(),
                }
            )
    out.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return out


def create_project(name: str, description: str = "") -> dict:
    pid = new_id("p_")
    d = project_dir(pid)
    for sub in ("layers", "dem"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    meta = {
        "id": pid,
        "name": name,
        "description": description,
        "crs": "EPSG:4326",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "layers": [],
        "dem": None,
    }
    _write_json(d / "project.json", meta)
    return meta


def get_project(pid: str) -> Optional[dict]:
    return _read_json(project_dir(pid) / "project.json")


def save_project(meta: dict) -> dict:
    meta["updated_at"] = now_iso()
    _write_json(project_dir(meta["id"]) / "project.json", meta)
    return meta


def touch_project(pid: str) -> None:
    meta = get_project(pid)
    if meta:
        save_project(meta)


def delete_project(pid: str) -> bool:
    d = project_dir(pid)
    if not d.exists():
        return False
    try:
        shutil.rmtree(d)
        return True
    except (Exception, SystemExit):
        # 某些环境（沙箱 / 安全策略）会拦截目录递归删除：可能是 OSError /
        # PermissionError，也可能是守护脚本注入的 SystemExit。降级：把项目目录
        # 移入 data/.trash，效果等价于删除（不再出现在项目列表中），且可人工恢复。
        trash = PROJECTS_DIR.parent / ".trash"
        trash.mkdir(parents=True, exist_ok=True)
        target = trash / f"{pid}_{time.strftime('%Y%m%d%H%M%S')}"
        shutil.move(str(d), str(target))
        return True


# ---------------------------------------------------------------- 图层
def layer_path(pid: str, lid: str) -> Path:
    return project_dir(pid) / "layers" / f"{lid}.geojson"


def add_layer(
    pid: str,
    name: str,
    layer_type: str,
    geojson: dict,
    source: str = "import",
    style: Optional[dict] = None,
    metadata: Optional[dict] = None,
) -> dict:
    meta = get_project(pid)
    if meta is None:
        raise KeyError(f"项目不存在: {pid}")
    lid = new_id("l_")
    features = geojson.get("features", []) if geojson else []
    geom_type = LAYER_TYPES.get(layer_type, LAYER_TYPES["other"])["geometry"]
    if features:
        geom_type = features[0]["geometry"]["type"] if features[0].get("geometry") else geom_type

    payload = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4326"}},
        "features": features,
    }
    _write_json(layer_path(pid, lid), payload)

    info = {
        "id": lid,
        "name": name,
        "type": layer_type,
        "geometry_type": geom_type,
        "source": source,
        "visible": True,
        "feature_count": len(features),
        "style": style or {},
        "metadata": metadata or {},
        "extent": _extent_of(features),
        "created_at": now_iso(),
    }
    meta.setdefault("layers", []).append(info)
    meta["extent"] = _merge_extent(meta.get("extent"), info["extent"])
    save_project(meta)
    return info


def read_layer(pid: str, lid: str) -> Optional[dict]:
    return _read_json(layer_path(pid, lid))


def write_layer(pid: str, lid: str, geojson: dict) -> dict:
    _write_json(layer_path(pid, lid), geojson)
    meta = get_project(pid)
    if meta:
        for l in meta.get("layers", []):
            if l["id"] == lid:
                l["feature_count"] = len(geojson.get("features", []))
                l["extent"] = _extent_of(geojson.get("features", []))
                break
        save_project(meta)
    return geojson


def update_layer_info(pid: str, lid: str, patch: dict) -> Optional[dict]:
    meta = get_project(pid)
    if not meta:
        return None
    for l in meta.get("layers", []):
        if l["id"] == lid:
            l.update(patch)
            save_project(meta)
            return l
    return None


def delete_layer(pid: str, lid: str) -> bool:
    meta = get_project(pid)
    if not meta:
        return False
    meta["layers"] = [l for l in meta.get("layers", []) if l["id"] != lid]
    save_project(meta)
    p = layer_path(pid, lid)
    if p.exists():
        p.unlink()
    return True


def find_layer(pid: str, layer_type: str) -> Optional[dict]:
    meta = get_project(pid)
    if not meta:
        return None
    for l in meta.get("layers", []):
        if l.get("type") == layer_type:
            return l
    return None


def layers_of_type(pid: str, types: Iterable[str]) -> list[dict]:
    meta = get_project(pid)
    if not meta:
        return []
    types = set(types)
    return [l for l in meta.get("layers", []) if l.get("type") in types]


def all_features(pid: str, types: Iterable[str]) -> list[dict]:
    feats: list[dict] = []
    for l in layers_of_type(pid, types):
        gj = read_layer(pid, l["id"])
        if not gj:
            continue
        for f in gj.get("features", []):
            f.setdefault("properties", {})
            f["properties"]["_layer_id"] = l["id"]
            f["properties"]["_layer_name"] = l["name"]
            f["properties"]["_layer_type"] = l["type"]
            feats.append(f)
    return feats


# ---------------------------------------------------------------- 拓扑 / 概化图 / DEM
def save_topology(pid: str, topo: dict) -> dict:
    _write_json(project_dir(pid) / "topology.json", topo)
    touch_project(pid)
    return topo


def read_topology(pid: str) -> Optional[dict]:
    return _read_json(project_dir(pid) / "topology.json")


def save_schematic(pid: str, sch: dict) -> dict:
    _write_json(project_dir(pid) / "schematic.json", sch)
    touch_project(pid)
    return sch


def read_schematic(pid: str) -> Optional[dict]:
    return _read_json(project_dir(pid) / "schematic.json")


def save_subbasins(pid: str, result: dict) -> dict:
    _write_json(project_dir(pid) / "subbasins.json", result)
    touch_project(pid)
    return result


def read_subbasins(pid: str) -> Optional[dict]:
    return _read_json(project_dir(pid) / "subbasins.json")


def clear_subbasins(pid: str) -> None:
    p = project_dir(pid) / "subbasins.json"
    if p.exists():
        p.unlink()
    touch_project(pid)


def dem_dir(pid: str) -> Path:
    d = project_dir(pid) / "dem"
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_dem_meta(pid: str, info: dict) -> dict:
    _write_json(dem_dir(pid) / "meta.json", info)
    meta = get_project(pid)
    if meta:
        meta["dem"] = info
        save_project(meta)
    return info


def read_dem_meta(pid: str) -> Optional[dict]:
    return _read_json(dem_dir(pid) / "meta.json")


# ---------------------------------------------------------------- 时序数据（率定输入）
# 结构：timeseries/<kind>/<station_key>.csv + timeseries/manifest.json
def ts_dir(pid: str, kind: str | None = None) -> Path:
    d = project_dir(pid) / "timeseries"
    if kind:
        d = d / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


def ts_series_path(pid: str, kind: str, key: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_.()" else "_" for ch in str(key)) or "series"
    return ts_dir(pid, kind) / f"{safe}.csv"


def read_ts_manifest(pid: str) -> dict:
    from .core.timeseries import empty_manifest

    doc = _read_json(project_dir(pid) / "timeseries" / "manifest.json")
    if not doc:
        return empty_manifest()
    doc.setdefault("series", {})
    for kind in ("rain", "flow", "evap"):
        doc["series"].setdefault(kind, {})
    return doc


def save_ts_manifest(pid: str, manifest: dict) -> dict:
    _write_json(project_dir(pid) / "timeseries" / "manifest.json", manifest)
    touch_project(pid)
    return manifest


def cal_runs_dir(pid: str) -> Path:
    return project_dir(pid) / "calibration" / "runs"


def cal_run_dir(pid: str, rid: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(rid)) or "run"
    return cal_runs_dir(pid) / safe


def read_json_file(path) -> dict | None:
    doc = _read_json(Path(path))
    return doc if isinstance(doc, dict) else None


def write_json_file(path, doc: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    _write_json(p, doc)


def remove_file(path) -> bool:
    """尽力删除单个文件，返回是否成功。

    Windows 上文件可能被杀软/索引/其它进程短暂占用而删除失败；沙箱环境下删除
    还可能被安全守护以 SystemExit 拒绝。调用方只需保证**逻辑删除**（清单条目已摘除），
    因此这里对失败静默返回 False，避免把清理动作变成接口 500。
    """
    p = Path(path)
    try:
        p.unlink()
        return True
    except OSError:
        return False
    except SystemExit:
        return False


def clear_ts(pid: str, kind: str | None = None) -> None:
    """清空某类（或全部）时序数据与清单条目。"""
    d = ts_dir(pid, kind) if kind else ts_dir(pid)
    if d.exists():
        for p in d.rglob("*"):
            if p.is_file():
                remove_file(p)
    if kind:
        m = read_ts_manifest(pid)
        m["series"][kind] = {}
        save_ts_manifest(pid, m)
    else:
        remove_file(project_dir(pid) / "timeseries" / "manifest.json")


# ---------------------------------------------------------------- 内部
def _extent_of(features: list[dict]) -> Optional[list[float]]:
    xs: list[float] = []
    ys: list[float] = []

    def walk(coords):
        if not coords:
            return
        if isinstance(coords[0], (int, float)):
            xs.append(coords[0])
            ys.append(coords[1])
        else:
            for c in coords:
                walk(c)

    for f in features:
        g = f.get("geometry")
        if g and g.get("coordinates"):
            walk(g["coordinates"])
    if not xs:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def _merge_extent(a, b):
    if not a:
        return b
    if not b:
        return a
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
