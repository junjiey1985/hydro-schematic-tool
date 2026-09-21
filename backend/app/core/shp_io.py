"""SHP 读写与坐标转换。"""
from __future__ import annotations

import io
import math
import zipfile
from pathlib import Path
from typing import Iterable, Optional

import shapefile  # pyshp
from pyproj import CRS, Transformer
from shapely.geometry import (
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
    mapping,
    shape,
)
from shapely.ops import transform as shp_transform

WGS84 = CRS.from_epsg(4326)

# 常见无 .prj 时的候选坐标系（按优先级尝试）
_FALLBACK_CRS = ["EPSG:4326", "EPSG:4490"]


# ---------------------------------------------------------------- 编码
def _decode_text(b: bytes) -> str:
    if b is None:
        return ""
    if isinstance(b, str):
        return b.strip()
    for enc in ("utf-8", "gbk", "gb18030", "big5", "latin1"):
        try:
            return b.decode(enc).strip()
        except UnicodeDecodeError:
            continue
    return b.decode("utf-8", errors="replace").strip()


def _decode_dbf_text(s) -> str:
    """DBF 字段值解码。

    pyshp 若以 utf-8 + errors='replace' 读取 GBK 的 DBF，会静默产出乱码，
    因此这里统一按 latin1 取回原始字节再逐一尝试常见中文编码。
    """
    if isinstance(s, bytes):
        return _decode_text(s)
    if not isinstance(s, str):
        return s
    try:
        raw = s.encode("latin1")
    except UnicodeEncodeError:
        return s.strip()
    if all(b < 0x80 for b in raw):
        return s.strip()
    for enc in ("gbk", "gb18030", "utf-8", "big5"):
        try:
            return raw.decode(enc).strip()
        except UnicodeDecodeError:
            continue
    return s.strip()


# ---------------------------------------------------------------- 读取
def _shape_to_geom(shp) -> Optional[dict]:
    """把 pyshp 的 Shape 转成 GeoJSON geometry（保持原始坐标）。"""
    st = shp.shapeType
    pts = shp.points
    parts = list(shp.parts) + [len(pts)]

    def ring(i: int) -> list:
        return [list(p) for p in pts[parts[i]: parts[i + 1]]]

    if st in (1, 11, 21):  # Point / PointZ / PointM
        if isinstance(pts, dict):
            return {"type": "Point", "coordinates": [pts["x"][0], pts["y"][0]]}
        return {"type": "Point", "coordinates": list(pts[0])[:2]}

    if st in (8, 18, 28):  # MultiPoint
        if isinstance(pts, dict):
            coords = [[x, y] for x, y in zip(pts["x"], pts["y"])]
        else:
            coords = [list(p)[:2] for p in pts]
        return {"type": "MultiPoint", "coordinates": coords}

    if st in (3, 13, 23):  # PolyLine
        lines = []
        for i in range(len(shp.parts)):
            c = ring(i)
            if len(c) >= 2:
                lines.append(c)
        if not lines:
            return None
        if len(lines) == 1:
            return {"type": "LineString", "coordinates": lines[0]}
        return {"type": "MultiLineString", "coordinates": lines}

    if st in (5, 15, 25):  # Polygon
        polys = []
        for i in range(len(shp.parts)):
            c = ring(i)
            if len(c) >= 4:
                polys.append(c)
        if not polys:
            return None
        return {"type": "Polygon", "coordinates": polys}  # 环方向交由 shapely 归一
    return None


def build_transformer(prj_wkt: Optional[str]) -> tuple[Transformer, str]:
    """根据 .prj 构造到 WGS84 的转换器，返回 (transformer, crs_name)。"""
    src = None
    if prj_wkt:
        try:
            src = CRS.from_wkt(prj_wkt)
        except Exception:
            src = None
    if src is None:
        src = WGS84
    try:
        if src.is_geographic and abs(src.axis_info[0].unit_conversion_factor - 1.0) < 1e-9:
            pass
    except Exception:
        pass
    return Transformer.from_crs(src, WGS84, always_xy=True), src.to_string()


def _round_coords(obj, nd: int = 7):
    """递归降低坐标精度，减小 GeoJSON 体积。"""
    if isinstance(obj, (list, tuple)):
        if obj and isinstance(obj[0], (int, float)):
            return [round(float(v), nd) for v in obj]
        return [_round_coords(o, nd) for o in obj]
    return obj


def read_shapefile(
    shp_path: Path,
    encoding: Optional[str] = None,
    target_epsg: int = 4326,
    precision: int = 7,
) -> dict:
    """读取单个 shp，返回 GeoJSON FeatureCollection（默认转 WGS84）。"""
    prj = shp_path.with_suffix(".prj")
    wkt = None
    if prj.exists():
        wkt = _decode_text(prj.read_bytes())
        wkt = wkt or None

    encodings = [encoding] if encoding else ["latin1"]
    reader = None
    last_err = None
    for enc in encodings:
        if enc is None:
            continue
        try:
            reader = shapefile.Reader(str(shp_path), encoding=enc, encodingErrors="replace")
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
    if reader is None:
        raise RuntimeError(f"无法读取 {shp_path.name}: {last_err}")

    raw_fields = [f[0] for f in reader.fields if f[0] != "DeletionFlag"]
    fields = [f if encoding else _decode_dbf_text(f) for f in raw_fields]
    transformer, src_name = build_transformer(wkt)
    need_tf = not (src_name.upper().startswith("EPSG:4326"))

    def _tf(x, y):
        if not need_tf:
            return x, y
        lon, lat = transformer.transform(x, y)
        return lon, lat

    use_fixed_enc = bool(encoding)
    features = []
    for sr in reader.iterShapeRecords():
        geom = _shape_to_geom(sr.shape)
        if geom is None:
            continue
        try:
            g = shape(geom)
            if need_tf:
                g = shp_transform(_tf, g)
            if not g.is_valid:
                g = g.buffer(0)
            if g.is_empty:
                continue
            geom = mapping(g)
        except Exception:
            continue

        props = {}
        for k, v in zip(fields, list(sr.record)):
            if isinstance(v, str):
                v = v.strip() if use_fixed_enc else _decode_dbf_text(v)
            props[k] = v
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": geom["type"], "coordinates": _round_coords(geom["coordinates"], precision)},
                "properties": props,
            }
        )
    reader.close()

    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": f"EPSG:{target_epsg}"}},
        "features": features,
        "source_crs": src_name,
        "reprojected": bool(need_tf),
    }


def list_shapefiles(root: Path) -> list[Path]:
    return sorted(root.glob("*.shp")) + sorted(root.glob("*.SHP"))


def extract_zip(zip_path: Path, out_dir: Path) -> Path:
    """解压上传的 zip（含中文名需处理编码），返回解压目录。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            name = info.filename
            if not name or info.is_dir():
                continue
            # 处理 zip 中 GBK 文件名被误解码为 cp437 的情况
            try:
                fixed = name.encode("cp437").decode("gbk")
            except Exception:
                fixed = name
            safe = Path(fixed).name
            if not safe:
                continue
            target = out_dir / safe
            with zf.open(info) as src, target.open("wb") as dst:
                dst.write(src.read())
    return out_dir


# ---------------------------------------------------------------- 写出
# DBF 常用 GBK 编码写不出的字符 → 近似替换，避免 UnicodeEncodeError
_ENC_FIX = {"²": "2", "³": "3", "¹": "1", "—": "-", "–": "-", "≈": "~", "≤": "<=", "≥": ">=", "°": "°"}


def _dbf_safe(text, encoding: str = "gbk") -> str:
    """把任意值转成目标编码可写出的字符串。"""
    s = "" if text is None else str(text)
    for k, v in _ENC_FIX.items():
        if k in s:
            s = s.replace(k, v)
    try:
        s.encode(encoding)
        return s
    except UnicodeEncodeError:
        return s.encode(encoding, "ignore").decode(encoding)


def write_shapefile(path: Path, geojson: dict, geom_type: str = "polyline", encoding: str = "gbk") -> None:
    """把 GeoJSON 写成 shp（含 .shp/.shx/.dbf/.prj），默认输出经纬度。"""
    w = shapefile.Writer(str(path.with_suffix("")), encoding=encoding)
    features = geojson.get("features", [])
    props_keys: list[str] = []
    for f in features:
        for k in (f.get("properties") or {}):
            if k not in props_keys and not k.startswith("_"):
                props_keys.append(k)
    for k in props_keys:
        w.field(_dbf_safe(k, encoding)[:10], "C", size=80)
    w.field("_gid", "N", size=10)

    for idx, f in enumerate(features):
        g = shape(f["geometry"])
        p = f.get("properties") or {}
        if g.geom_type in ("LineString", "MultiLineString"):
            lines = [g] if g.geom_type == "LineString" else list(g.geoms)
            w.line([[[round(x, 7), round(y, 7)] for x, y in ln.coords] for ln in lines])
        elif g.geom_type in ("Polygon", "MultiPolygon"):
            polys = [g] if g.geom_type == "Polygon" else list(g.geoms)
            rings = []
            for poly in polys:
                for r in [poly.exterior] + list(poly.interiors):
                    rings.append([list(c) for c in r.coords])
            w.poly(rings)
        elif g.geom_type in ("Point", "MultiPoint"):
            pts = [g] if g.geom_type == "Point" else list(g.geoms)
            w.point(*[round(v, 7) for v in pts[0].coords[0]])
        w.record(*[_dbf_safe(str(p.get(k, ""))[:80], encoding) for k in props_keys], idx)
    w.close()

    (path.with_suffix(".prj")).write_text(
        'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],'
        'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]',
        encoding="utf-8",
    )


# ---------------------------------------------------------------- 几何工具
def haversine_m(x1: float, y1: float, x2: float, y2: float) -> float:
    r = 6371008.8
    p1, p2 = math.radians(y1), math.radians(y2)
    dp = p2 - p1
    dl = math.radians(x2 - x1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def line_length_m(coords: Iterable) -> float:
    pts = list(coords)
    return sum(haversine_m(*pts[i], *pts[i + 1]) for i in range(len(pts) - 1))


def metres_per_degree(lat: float) -> tuple[float, float]:
    """返回该纬度处 1° 经度、1° 纬度对应的米数。"""
    mlat = 111132.92 - 559.82 * math.cos(2 * math.radians(lat)) + 1.175 * math.cos(4 * math.radians(lat))
    mlon = 111412.84 * math.cos(math.radians(lat)) - 93.5 * math.cos(3 * math.radians(lat))
    return mlon, mlat


def length_m_planar(coords: list) -> float:
    """用等距近似计算折线长度（米），比逐段 haversine 快。"""
    if len(coords) < 2:
        return 0.0
    lat0 = sum(c[1] for c in coords) / len(coords)
    mlon, mlat = metres_per_degree(lat0)
    total = 0.0
    for i in range(len(coords) - 1):
        dx = (coords[i + 1][0] - coords[i][0]) * mlon
        dy = (coords[i + 1][1] - coords[i][1]) * mlat
        total += math.hypot(dx, dy)
    return total
