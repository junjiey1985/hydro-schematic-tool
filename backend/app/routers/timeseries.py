"""时序数据接口（率定与预报的输入准备）。

- 导入：CSV（长表 / 宽表，UTF-8 / GBK 容错）→ 逐站等时段序列
- 质检：缺测率、缺测区间、时段识别
- 覆盖率：按预报单元核对"面雨量站是否齐备 + 出口站是否有流量"
- 演示：一键生成合成率定数据（有真值参数，供后续率定回收验证）
"""
from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .. import storage as st
from ..core.timeseries import (
    KIND_AGG,
    KIND_LABELS,
    KINDS,
    TimeSeriesError,
    coverage_report,
    csv_to_records,
    decode_text,
    demo_series,
    detect_interval,
    interval_label,
    match_series_key,
    parse_table,
    qc_series,
    records_to_csv,
    resample,
    series_entry,
    summarize,
)
from .deps import get_project_or_404

router = APIRouter(prefix="/api/projects", tags=["timeseries"])

# 显式指定时段时的秒数；留空则按数据自动识别
INTERVAL_SECONDS = {"1h": 3600, "2h": 7200, "3h": 10800, "6h": 21600, "12h": 43200, "24h": 86400, "1d": 86400}


def _check_kind(kind: str) -> str:
    kind = (kind or "").strip()
    if kind not in KINDS:
        raise HTTPException(400, f"kind 需为 {'/'.join(KINDS)} 之一，当前为 {kind!r}")
    return kind


def _save_series(pid: str, kind: str, key: str, name: str, records, interval_s: int, source: str, station_id: str = "") -> dict:
    """写入标准 CSV 并更新 manifest，返回该序列的清单条目。"""
    path = st.ts_series_path(pid, kind, key)
    path.write_text(records_to_csv(records), encoding="utf-8")

    manifest = st.read_ts_manifest(pid)
    rel = f"{kind}/{path.name}"
    entry = series_entry(kind, key, name, station_id, rel, interval_s, records, source)
    manifest["series"][kind][key] = entry
    manifest["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    st.save_ts_manifest(pid, manifest)
    return entry


def _rebuild_coverage(pid: str, manifest: dict) -> dict:
    return coverage_report(st.read_subbasins(pid), manifest)


# ---------------------------------------------------------------- 导入
@router.post("/{pid}/timeseries/import")
async def import_timeseries(
    pid: str,
    files: list[UploadFile] = File(...),
    kind: str = Form("rain"),
    interval: str = Form(""),
    station: str = Form(""),
    encoding: str = Form(""),
):
    """上传时序 CSV。单列文件可用 station 指定站名；长表/宽表自动识别站点列。"""
    get_project_or_404(pid)
    kind = _check_kind(kind)
    step = INTERVAL_SECONDS.get((interval or "").strip().lower(), 0)
    if (interval or "").strip() and not step:
        raise HTTPException(400, f"不支持的时段 {interval!r}，可选 1h/6h/12h/24h")

    imported: list[dict] = []
    skipped: list[dict] = []
    for uf in files:
        fname = Path(uf.filename or "unnamed.csv").name
        raw = await uf.read()
        if not raw:
            skipped.append({"file": fname, "error": "文件为空"})
            continue
        text, enc = decode_text(raw)
        if encoding and encoding.lower() not in ("", "auto") and enc != encoding.lower():
            # 用户强制指定编码时按指定重解
            try:
                text = raw.decode(encoding)
                enc = encoding
            except (UnicodeDecodeError, LookupError):
                pass
        try:
            parsed = parse_table(text, fname)
        except TimeSeriesError as e:
            skipped.append({"file": fname, "error": str(e)})
            continue

        for raw_key, item in parsed["series"].items():
            if not item["records"]:
                skipped.append({"file": fname, "error": f"{raw_key}：无有效数据"})
                continue
            # 单列文件（宽表只有一列且未匹配到站点名）→ 用用户指定名 / 文件名
            many = len(parsed["series"]) > 1
            key = raw_key
            name = raw_key
            if not many and station.strip():
                key = name = station.strip()
            elif not many and not _looks_like_station(raw_key):
                stem = Path(fname).stem
                key = name = stem

            step_s = step or detect_interval(item["records"])
            recs = resample(item["records"], step_s, KIND_AGG[kind]) if step else item["records"]
            try:
                entry = _save_series(pid, kind, key, name, recs, step_s, fname)
            except OSError as e:  # noqa: PERF203
                skipped.append({"file": fname, "error": f"写入失败：{e}"})
                continue
            imported.append({"file": fname, **{k: entry[k] for k in ("key", "name", "interval", "count", "start", "end", "missing_rate", "status")}})

    manifest = st.read_ts_manifest(pid)
    return {
        "ok": bool(imported),
        "kind": kind,
        "kind_label": KIND_LABELS[kind],
        "imported": imported,
        "skipped": skipped,
        "summary": summarize(manifest),
        "coverage": _rebuild_coverage(pid, manifest),
        "stations_without_series": _stations_without_series(pid, manifest, kind),
        "interval_note": "按时段聚合" if step else "时段自动识别",
    }


def _looks_like_station(key: str) -> bool:
    """判断列名是否像站名（排除 value/数值 这类通用列名）。"""
    k = (key or "").strip().lower()
    return bool(k) and k not in ("value", "数值", "值", "数据", "观测值")


def _stations_of(pid: str, kind: str) -> list[dict]:
    """站点清单：优先取拓扑里的站点（id 与子流域出口站一致），否则回退到图层。"""
    want = {"rain": "rain", "flow": "hydro", "evap": "rain"}.get(kind)
    if not want:
        return []
    topo = st.read_topology(pid) or {}
    out = [{"id": s.get("id"), "name": s.get("name") or s.get("id")} for s in topo.get("stations", []) if s.get("type") == want]
    if out:
        return out
    ltype = "rain_station" if want == "rain" else "hydro_station"
    for f in st.all_features(pid, [ltype]):
        p = f.get("properties") or {}
        out.append({"id": f.get("id"), "name": p.get("名称") or p.get("name") or f.get("id")})
    return out


def _stations_without_series(pid: str, manifest: dict, kind: str) -> list[str]:
    keys = list((manifest.get("series", {}).get(kind) or {}).keys())
    return [s["name"] for s in _stations_of(pid, kind) if not match_series_key(keys, s["id"], s["name"])]


# ---------------------------------------------------------------- 查询
@router.get("/{pid}/timeseries/manifest")
def get_manifest(pid: str):
    """清单 + 覆盖率检查（率定工作台第一步的数据检查）。"""
    get_project_or_404(pid)
    manifest = st.read_ts_manifest(pid)
    sub = st.read_subbasins(pid)
    stations = {kind: _stations_of(pid, kind) for kind in ("rain", "flow")}
    have = {k: list((manifest.get("series", {}).get(k) or {}).keys()) for k in KINDS}
    return {
        "exists": any(have.values()),
        "series": {k: list((manifest.get("series", {}).get(k) or {}).values()) for k in KINDS},
        "summary": summarize(manifest),
        "coverage": coverage_report(sub, manifest),
        "stations": stations,
        "stations_without_series": {k: _stations_without_series(pid, manifest, k) for k in ("rain", "flow")},
        "updated_at": manifest.get("updated_at"),
    }


@router.get("/{pid}/timeseries/{kind}/{key}")
def get_series(pid: str, kind: str, key: str, limit: int = 0):
    """读取某条序列（供前端预览曲线）；limit>0 时等间隔抽稀。"""
    get_project_or_404(pid)
    kind = _check_kind(kind)
    manifest = st.read_ts_manifest(pid)
    entry = (manifest.get("series", {}).get(kind) or {}).get(key)
    if not entry:
        raise HTTPException(404, f"未找到序列 {kind}/{key}")
    path = st.project_dir(pid) / "timeseries" / entry["file"]
    if not path.exists():
        raise HTTPException(404, f"序列文件缺失：{entry['file']}")
    recs = csv_to_records(path.read_text(encoding="utf-8"))
    points = [[dt.strftime("%Y-%m-%d %H:%M"), v] for dt, v in recs]
    if limit and len(points) > limit:
        stride = max(1, len(points) // limit)
        points = points[::stride]
    return {"ok": True, "kind": kind, "entry": entry, "points": points}


@router.delete("/{pid}/timeseries/{kind}/{key}")
def delete_series(pid: str, kind: str, key: str):
    get_project_or_404(pid)
    kind = _check_kind(kind)
    manifest = st.read_ts_manifest(pid)
    entry = (manifest.get("series", {}).get(kind) or {}).pop(key, None)
    if not entry:
        raise HTTPException(404, f"未找到序列 {kind}/{key}")
    path = st.project_dir(pid) / "timeseries" / entry["file"]
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass
    st.save_ts_manifest(pid, manifest)
    return {"ok": True, "coverage": _rebuild_coverage(pid, manifest)}


@router.delete("/{pid}/timeseries")
def clear_timeseries(pid: str, kind: str = ""):
    """清空时序数据（kind 留空则全清）。"""
    get_project_or_404(pid)
    if kind:
        kind = _check_kind(kind)
    st.clear_ts(pid, kind or None)
    manifest = st.read_ts_manifest(pid)
    return {"ok": True, "summary": summarize(manifest), "coverage": _rebuild_coverage(pid, manifest)}


# ---------------------------------------------------------------- 演示数据
@router.post("/{pid}/timeseries/demo")
def make_demo(pid: str, payload: dict | None = None):
    """生成合成率定演示数据（降雨 / 流量 / 蒸发），覆盖当前划分的单元出口站。"""
    get_project_or_404(pid)
    payload = payload or {}
    sub = st.read_subbasins(pid)
    if not sub or not sub.get("subbasins"):
        raise HTTPException(400, "请先划分子流域（率定数据需按预报单元组织）")
    days = int(payload.get("days") or 1095)
    seed = int(payload.get("seed") or 20260921)
    fresh = bool(payload.get("replace", True))
    if fresh:
        st.clear_ts(pid)

    doc = demo_series(sub, days=days, seed=seed, start=payload.get("start") or "2015-01-01")
    created = {"rain": [], "flow": [], "evap": []}
    for kind in KINDS:
        for key, item in doc[kind].items():
            recs = item["records"]
            step_s = detect_interval(recs)
            entry = _save_series(pid, kind, key, item.get("name") or key, recs, step_s, "生成·演示数据", key)
            created[kind].append({"key": key, "name": entry["name"], "count": entry["count"], "interval": entry["interval"]})
    manifest = st.read_ts_manifest(pid)
    return {
        "ok": True,
        "days": doc["days"],
        "start": doc["start"],
        "created": created,
        "truth": doc["truth"],
        "summary": summarize(manifest),
        "coverage": _rebuild_coverage(pid, manifest),
        "note": f"已生成 {doc['days']} 天合成序列（真值参数：{doc['truth']}），可复用为率定验证基准",
    }
