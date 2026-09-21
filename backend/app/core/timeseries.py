"""时序数据（降雨 / 流量 / 蒸发）的解析、质检与管理。

面向模型率定与实时预报的数据准备环节：把外部 CSV（长表 / 宽表、UTF-8 / GBK）
统一成"每站一条等时段序列"，并给出缺测率与单元覆盖率报告。

存储结构::

    data/projects/<pid>/timeseries/
        rain/<station_key>.csv      逐时段降雨（mm）
        flow/<station_key>.csv      实测流量（m³/s）
        evap/default.csv            流域蒸发能力（mm）
        manifest.json               清单：时段 / 起止 / 点数 / 缺测率 / 来源

序列 CSV 统一为两列 ``time,value``（UTC+8，``YYYY-MM-DD HH:MM``），缺测点不写行，
但其位置在 manifest 的 gaps 中体现，率定阶段按缺测剔除。
"""
from __future__ import annotations

import csv
import io
import math
import re
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional

# ---------------------------------------------------------------- 常量
KINDS = ("rain", "flow", "evap")
KIND_LABELS = {"rain": "降雨", "flow": "流量", "evap": "蒸发能力"}

# 观测口径：降雨为累计值、流量与蒸发为瞬时/日均值（重采样时的聚合方式）
KIND_AGG = {"rain": "sum", "flow": "mean", "evap": "mean"}

# 缺测率阈值：> warn 告警，> bad 判定不可用
MISSING_WARN = 0.05
MISSING_BAD = 0.20

ENCODINGS = ("utf-8-sig", "utf-8", "gbk", "gb18030", "big5", "latin1")

TIME_HEADER_HINTS = ("time", "date", "日期", "时间", "时刻", "datetime", "年月日", "年")
STATION_HEADER_HINTS = ("station", "站名", "站码", "站号", "测站", "站点", "stcd", "stid", "name", "id")
VALUE_HEADER_HINTS = ("value", "数值", "流量", "降雨", "雨量", "水位", "蒸发", "q", "pr", "pcp", "precip", "rain", "flow", "evap", "e0")

NULL_TOKENS = {"", "-", "--", "/", "//", "null", "nan", "none", "na", "n/a", "—", "－", "缺测", "缺", "无"}

# 常见时段（秒）→ 标签
_STEP_LABELS = {
    3600: "1h",
    7200: "2h",
    10800: "3h",
    21600: "6h",
    43200: "12h",
    86400: "24h",
    604800: "7d",
    2592000: "1mon",
}


# ---------------------------------------------------------------- 基础解析
class TimeSeriesError(ValueError):
    """时序解析类错误（前端可直接展示给用户）。"""


def decode_text(raw: bytes) -> tuple[str, str]:
    """按容错链解码文本，返回 (文本, 编码)。"""
    if raw[:3] == b"\xef\xbb\xbf":
        return raw.decode("utf-8-sig"), "utf-8-sig"
    for enc in ENCODINGS:
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("latin1", errors="replace"), "latin1"


def _sniff_delimiter(sample: str) -> str:
    """猜测分隔符：逗号 / 制表符 / 分号 / 空白。"""
    head = "\n".join(sample.splitlines()[:5])
    counts = {",": head.count(","), "\t": head.count("\t"), ";": head.count(";")}
    best = max(counts, key=lambda k: counts[k])
    if counts[best] > 0:
        return best
    # Excel 导出的中文逗号
    if head.count("，") > 0:
        return "，"
    return None  # 空白分隔


def _split_line(line: str, delim: Optional[str]) -> list[str]:
    if delim is None:
        return [c for c in re.split(r"\s+", line.strip()) if c]
    if delim == "，":
        return [c.strip() for c in line.split("，")]
    return next(csv.reader([line], delimiter=delim))


def _norm_key(text: str) -> str:
    return re.sub(r"[\s_\-()（）]", "", (text or "")).lower()


def parse_time(tok: str) -> Optional[datetime]:
    """解析多种时间写法：2015-01-01 / 2015/1/1 / 20150101 / 带时刻 / Excel 序列号。"""
    s = (tok or "").strip().strip('"').strip("'")
    if not s:
        return None
    s = s.replace("T", " ").replace("年", "-").replace("月", "-").replace("日", " ")
    s = re.sub(r"\s+", " ", s).strip()
    # Excel 序列号（1900 起算）
    if re.fullmatch(r"\d{5}(\.\d+)?", s):
        try:
            return datetime(1899, 12, 30) + timedelta(days=float(s))
        except (ValueError, OverflowError):
            return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d",
        "%Y%m%d%H%M",
        "%Y%m%d %H:%M",
        "%Y%m%d",
        "%Y-%m",
        "%Y",
    ):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def parse_value(tok: str, scale: float = 1.0) -> Optional[float]:
    """解析数值，缺测标记返回 None。"""
    s = (tok or "").strip().strip('"').strip("'")
    if s.lower() in NULL_TOKENS:
        return None
    s = s.replace("，", "").replace(",", "")
    try:
        v = float(s)
    except ValueError:
        return None
    if not math.isfinite(v) or v <= -9999:  # 常见缺测编码
        return None
    return v * scale


# ---------------------------------------------------------------- 表格结构识别
def _classify_columns(header: list[str]) -> dict:
    """识别表头中"时间列 / 站名列 / 数值列"。"""
    time_col = None
    station_col = None
    value_cols: list[int] = []

    for i, raw in enumerate(header):
        h = _norm_key(raw)
        if time_col is None and any(t in h for t in TIME_HEADER_HINTS):
            time_col = i
            continue
        if station_col is None and any(t in h for t in STATION_HEADER_HINTS):
            station_col = i
            continue
        value_cols.append(i)

    # 表头没有明显时间列时，用"该列大多能解析成时间"来判断
    if time_col is None and header:
        time_col = 0
        value_cols = [i for i in value_cols if i != 0]
    if station_col in value_cols:
        value_cols = [i for i in value_cols if i != station_col]
    return {"time_col": time_col, "station_col": station_col, "value_cols": value_cols}


def _looks_like_header(cells: list[str]) -> bool:
    """判断首行是否为表头：至少一半单元格为非数值文本。"""
    if not cells:
        return False
    texty = 0
    for c in cells:
        s = (c or "").strip()
        if s and parse_value(c) is None and parse_time(s) is None:
            texty += 1
    return texty >= max(1, len(cells) - 1)


def parse_table(text: str, filename: str = "") -> dict:
    """把 CSV 文本解析成 {series_key: {"name", "records":[(dt, val)]}}（未聚合）。"""
    delim = _sniff_delimiter(text)
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        rows.append(_split_line(line, delim))
    if not rows:
        raise TimeSeriesError(f"文件 {filename} 为空")

    header = rows[0] if _looks_like_header(rows[0]) else None
    body = rows[1:] if header else rows

    if header:
        cols = _classify_columns(header)
        names = [c.strip() or f"列{i + 1}" for i, c in enumerate(header)]
    else:
        # 无表头：按行内结构猜（首列时间，其余为数值）
        cols = {"time_col": 0, "station_col": None, "value_cols": list(range(1, len(rows[0])))}
        names = ["time"] + [f"列{i + 1}" for i in range(len(rows[0]) - 1)]

    tcol = cols["time_col"]
    scol = cols["station_col"]
    vcols = cols["value_cols"]
    if tcol is None or not vcols:
        raise TimeSeriesError(f"文件 {filename} 未识别出时间列与数值列，请检查表头")

    series: "OrderedDict[str, dict]" = OrderedDict()
    bad_rows = 0
    for r in body:
        if len(r) <= tcol:
            bad_rows += 1
            continue
        dt = parse_time(r[tcol])
        if dt is None:
            bad_rows += 1
            continue
        if scol is not None:
            # 长表：每行一个站一个值
            if len(r) <= max(scol, vcols[0]):
                bad_rows += 1
                continue
            key = (r[scol] or "").strip() or "未命名站"
            val = parse_value(r[vcols[0]])
            if key not in series:
                series[key] = {"name": key, "raw_key": key, "records": []}
            if val is not None:
                series[key]["records"].append((dt, val))
            continue
        for i in vcols:
            if i >= len(r):
                continue
            key = names[i] if names and i < len(names) else f"列{i + 1}"
            if key not in series:
                series[key] = {"name": key, "raw_key": key, "records": []}
            val = parse_value(r[i])
            if val is not None:
                series[key]["records"].append((dt, val))

    if not series:
        raise TimeSeriesError(f"文件 {filename} 未解析出任何有效序列（可能都是缺测）")
    for s in series.values():
        s["records"].sort(key=lambda x: x[0])
    return {"series": series, "had_header": bool(header), "bad_rows": bad_rows, "delimiter": delim or "空白"}


# ---------------------------------------------------------------- 重采样与质检
def detect_interval(records: list[tuple[datetime, float]]) -> int:
    """用相邻时间差的众数估计时段长度（秒）。"""
    if len(records) < 2:
        return 86400
    diffs: "OrderedDict[int, int]" = OrderedDict()
    for a, b in zip(records, records[1:]):
        d = int(round((b[0] - a[0]).total_seconds()))
        if d > 0:
            diffs[d] = diffs.get(d, 0) + 1
    if not diffs:
        return 86400
    return max(diffs.items(), key=lambda kv: kv[1])[0]


def interval_label(seconds: int) -> str:
    if seconds in _STEP_LABELS:
        return _STEP_LABELS[seconds]
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    return f"{seconds}s"


def resample(records: list[tuple[datetime, float]], step_s: int, agg: str) -> list[tuple[datetime, float]]:
    """按固定时段聚合（降雨求和、流量/蒸发求均值）；空桶留空（视为缺测）。"""
    if not records or step_s <= 0:
        return list(records)
    epoch = datetime(1970, 1, 1)
    buckets: "OrderedDict[int, list[float]]" = OrderedDict()
    for dt, v in records:
        idx = int((dt - epoch).total_seconds() // step_s)
        buckets.setdefault(idx, []).append(v)
    out = []
    for idx in sorted(buckets):
        vals = buckets[idx]
        val = sum(vals) if agg == "sum" else sum(vals) / len(vals)
        out.append((epoch + timedelta(seconds=idx * step_s), round(val, 4)))
    return out


def qc_series(records: list[tuple[datetime, float]], step_s: int) -> dict:
    """统计起止、点数、缺测率与缺测区间（最多列出 20 段）。"""
    if not records:
        return {
            "count": 0,
            "start": None,
            "end": None,
            "expected": 0,
            "missing": 0,
            "missing_rate": 1.0,
            "gaps": [],
            "status": "empty",
        }
    start, end = records[0][0], records[-1][0]
    expected = int(round((end - start).total_seconds() / step_s)) + 1 if step_s > 0 else len(records)
    expected = max(expected, len(records))
    gaps = []
    for a, b in zip(records, records[1:]):
        missing = int(round((b[0] - a[0]).total_seconds() / step_s)) - 1
        if missing > 0:
            gaps.append(
                {
                    "start": (a[0] + timedelta(seconds=step_s)).strftime("%Y-%m-%d %H:%M"),
                    "end": (b[0] - timedelta(seconds=step_s)).strftime("%Y-%m-%d %H:%M"),
                    "steps": missing,
                }
            )
    missing = max(0, expected - len(records))
    rate = missing / expected if expected else 0.0
    status = "ok" if rate <= MISSING_WARN else ("warn" if rate <= MISSING_BAD else "bad")
    return {
        "count": len(records),
        "start": start.strftime("%Y-%m-%d %H:%M"),
        "end": end.strftime("%Y-%m-%d %H:%M"),
        "expected": expected,
        "missing": missing,
        "missing_rate": round(rate, 4),
        "gaps": gaps[:20],
        "gap_total": len(gaps),
        "status": status,
    }


# ---------------------------------------------------------------- 单元覆盖率
def _norm_name(text: str) -> str:
    """站名归一：去空格、去"站"字尾，便于"竹山"≈"竹山站"。"""
    s = re.sub(r"[\s　]+", "", (text or "")).replace("（", "(").replace("）", ")")
    s = re.sub(r"(水文站|雨量站|水位站|站)$", "", s)
    return s.lower()


def match_series_key(candidates: Iterable[str], station_id: str, station_name: str) -> Optional[str]:
    """在已有序列 key 中匹配站点（先 id，再归一化站名，最后包含关系）。"""
    keys = list(candidates)
    if station_id and station_id in keys:
        return station_id
    want = _norm_name(station_name)
    if not want:
        return None
    for k in keys:
        if _norm_name(k) == want:
            return k
    for k in keys:
        nk = _norm_name(k)
        if nk and (nk in want or want in nk):
            return k
    return None


def coverage_report(subbasins_doc: Optional[dict], manifest: dict) -> dict:
    """按预报单元核对数据齐备情况（雨量面权重覆盖率 + 出口站流量）。

    对应技术方案 §6.6 第①步「数据检查」：不满足的单元在率定阶段按"借用参数"处理。
    """
    if not subbasins_doc or not subbasins_doc.get("subbasins"):
        return {"available": False, "reason": "尚未划分子流域", "rows": [], "summary": {}}

    rain_keys = list((manifest.get("series", {}).get("rain") or {}).keys())
    flow_keys = list((manifest.get("series", {}).get("flow") or {}).keys())
    evap_keys = list((manifest.get("series", {}).get("evap") or {}).keys())

    rows = []
    full_rain = 0
    has_flow = 0
    for sb in subbasins_doc["subbasins"]:
        rain_stations = [s for s in (sb.get("rain_stations") or []) if (s.get("weight") or 0) > 0]
        total_w = sum(float(s.get("weight") or 0) for s in rain_stations)
        got_w = 0.0
        missing_stations = []
        for s in rain_stations:
            hit = match_series_key(rain_keys, s.get("id") or "", s.get("name") or "")
            if hit:
                got_w += float(s.get("weight") or 0)
            else:
                missing_stations.append(s.get("name") or s.get("id"))
        cov = (got_w / total_w) if total_w > 0 else 0.0

        outlet = sb.get("outlet_station") or {}
        bid = outlet.get("id") or (sb.get("outlet") or {}).get("control_id") or ""
        bname = outlet.get("name") or (sb.get("outlet") or {}).get("control") or ""
        flow_hit = match_series_key(flow_keys, bid, bname)

        if cov >= 0.999:
            full_rain += 1
        if flow_hit:
            has_flow += 1
        status = "ok" if (cov >= 0.999 and flow_hit) else ("partial" if cov > 0 else "none")
        rows.append(
            {
                "code": sb.get("code"),
                "name": sb.get("name"),
                "area_km2": sb.get("area_km2"),
                "upstream_area_km2": sb.get("upstream_area_km2"),
                "rain_coverage": round(cov, 4),
                "rain_station_total": len(rain_stations),
                "rain_missing": missing_stations,
                "outlet_station": bname or bid or "—",
                "flow_available": bool(flow_hit),
                "evap_available": bool(evap_keys),
                "status": status,
            }
        )

    n = len(rows)
    return {
        "available": True,
        "rows": rows,
        "summary": {
            "subbasin_count": n,
            "rain_full": full_rain,
            "flow_covered": has_flow,
            "evap_available": bool(evap_keys),
            "calibratable": len([r for r in rows if r["status"] == "ok"]),
            "borrow": len([r for r in rows if r["status"] != "ok"]),
        },
    }


# ---------------------------------------------------------------- manifest
def empty_manifest() -> dict:
    return {"updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "series": {k: {} for k in KINDS}}


def series_entry(kind: str, key: str, name: str, station_id: str, file: str, interval_s: int, records, source: str) -> dict:
    qc = qc_series(records, interval_s)
    return {
        "key": key,
        "name": name or key,
        "station_id": station_id or "",
        "kind": kind,
        "file": file,
        "interval": interval_label(interval_s),
        "interval_s": interval_s,
        "source": source,
        **qc,
    }


def records_to_csv(records: Iterable[tuple[datetime, float]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["time", "value"])
    for dt, v in records:
        w.writerow([dt.strftime("%Y-%m-%d %H:%M"), f"{v:.4f}".rstrip("0").rstrip(".")])
    return buf.getvalue()


def csv_to_records(text: str) -> list[tuple[datetime, float]]:
    """读回项目内标准格式（time,value）。"""
    out = []
    for i, line in enumerate(text.splitlines()):
        if not line.strip():
            continue
        cells = next(csv.reader([line]))
        if i == 0 and _norm_key(cells[0]) in ("time", "时间"):
            continue
        if len(cells) < 2:
            continue
        dt = parse_time(cells[0])
        val = parse_value(cells[1])
        if dt is not None and val is not None:
            out.append((dt, val))
    return out


def summarize(manifest: dict) -> dict:
    """清单概览：各类型序列数、总点数、公共起止时间。"""
    out = {"counts": {}, "points": {}, "span": {}}
    starts, ends = [], []
    for kind in KINDS:
        items = list((manifest.get("series", {}).get(kind) or {}).values())
        out["counts"][kind] = len(items)
        out["points"][kind] = sum(int(i.get("count") or 0) for i in items)
        for i in items:
            if i.get("start"):
                starts.append(i["start"])
            if i.get("end"):
                ends.append(i["end"])
    if starts and ends:
        out["span"] = {"start": min(starts), "end": max(ends)}
    return out


# ---------------------------------------------------------------- 演示数据生成
def demo_series(
    subbasins_doc: Optional[dict],
    days: int = 1095,
    seed: int = 20260921,
    start: str = "2015-01-01",
) -> dict:
    """生成合成率定演示数据（降雨 / 流量 / 蒸发）。

    用降水 → 两层线性水库（快/慢）产汇流 + 观测噪声造"实测"流量，物理量纲自洽：
    1 mm/d 均匀降在 1 km² 上 = 1000/86400 ≈ 0.0116 m³/s，即 ``Q = mm/d · km² / 86.4``。
    有真值参数，便于后续 P2/P3 用"已知真值"回收验证率定结果。
    """
    import random

    rnd = random.Random(seed)
    t0 = parse_time(start) or datetime(2015, 1, 1)
    days = max(30, int(days))

    sbs = (subbasins_doc or {}).get("subbasins") or []
    rain_stations: "OrderedDict[str, dict]" = OrderedDict()
    outlet_stations: "OrderedDict[str, dict]" = OrderedDict()
    for sb in sbs:
        for s in sb.get("rain_stations") or []:
            sid = s.get("id") or s.get("name")
            if sid and (s.get("weight") or 0) > 0:
                rain_stations.setdefault(sid, {"id": sid, "name": s.get("name") or sid})
        o = sb.get("outlet_station") or {}
        oid = o.get("id") or (sb.get("outlet") or {}).get("control_id")
        if oid:
            outlet_stations.setdefault(
                oid, {"id": oid, "name": o.get("name") or (sb.get("outlet") or {}).get("control") or oid, "sb": sb}
            )
    if not rain_stations:
        rain_stations["R1"] = {"id": "R1", "name": "示例雨量站"}

    # ---- 逐日降雨（按站点独立生成，年周期湿季权重）
    dates = [t0 + timedelta(days=i) for i in range(days)]
    rain_by_station: dict[str, list[tuple[datetime, float]]] = {}
    for sid, info in rain_stations.items():
        bias = 0.85 + 0.3 * rnd.random()  # 站间差异
        recs = []
        for i, d in enumerate(dates):
            doy = d.timetuple().tm_yday
            wet = 0.5 + 0.5 * math.sin(2 * math.pi * (doy - 110) / 365.0)  # 5-9 月多雨
            p_wet = 0.05 + 0.45 * wet
            if rnd.random() < p_wet:
                amt = rnd.expovariate(1 / (3.2 + 7.0 * wet)) * bias  # ≈ 900~1100 mm/年
                if rnd.random() < 0.03:  # 少量暴雨
                    amt *= 3.0
                recs.append((d, round(amt, 1)))
            else:
                recs.append((d, 0.0))
        rain_by_station[sid] = recs

    # ---- 蒸发能力（年周期，夏季高）
    evap_recs = []
    for d in dates:
        doy = d.timetuple().tm_yday
        e = 1.2 + 2.6 * (0.5 + 0.5 * math.sin(2 * math.pi * (doy - 105) / 365.0))
        evap_recs.append((d, round(e + rnd.gauss(0, 0.3), 2)))

    # ---- 各单元产汇流 → 出口站流量
    def catchment_rain(sb: dict) -> list[float]:
        series = [(s.get("id") or s.get("name"), float(s.get("weight") or 0)) for s in sb.get("rain_stations") or []]
        series = [(k, w) for k, w in series if w > 0 and k in rain_by_station]
        tw = sum(w for _, w in series) or 1.0
        out = []
        for i in range(days):
            out.append(sum(rain_by_station[k][i][1] * w for k, w in series) / tw)
        return out

    flow_by_station: dict[str, dict] = {}
    for oid, info in outlet_stations.items():
        sb = info["sb"]
        area = float(sb.get("upstream_area_km2") or sb.get("area_km2") or 1000.0)
        p = catchment_rain(sb)
        # 真值参数（后续率定应能回收）+ 上游链：本单元只算区间产流
        k_runoff, k_fast, k_slow = 0.42, 0.45, 0.965
        s1 = s2 = 0.0
        recs = []
        for i, d in enumerate(dates):
            w = min(p[i], 60.0)  # 超渗截断（近似蓄满产流的容量限制）
            r = k_runoff * max(0.0, w - 2.0)  # 2 mm 初损
            s1 = k_fast * s1 + (1 - k_fast) * r
            s2 = k_slow * s2 + (1 - k_slow) * r * 0.35
            q = area * (s1 + s2) / 86.4
            q *= 1 + rnd.gauss(0, 0.05)  # 观测噪声
            recs.append((d, round(max(0.0, q), 2)))
        flow_by_station[oid] = {"name": info["name"], "records": recs}

    return {
        "start": t0.strftime("%Y-%m-%d"),
        "days": days,
        "rain": {k: {"name": v["name"], "records": rain_by_station[k]} for k, v in rain_stations.items()},
        "flow": flow_by_station,
        "evap": {"default": {"name": "流域蒸发能力", "records": evap_recs}},
        "truth": {"runoff_coef": 0.42, "k_fast": 0.45, "k_slow": 0.965, "initial_loss_mm": 2.0},
    }

