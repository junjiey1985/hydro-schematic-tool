"""流域级模拟：按预报单元排水序组装「区间产流 + 上游来流河道演算」。

对应技术方案 §6.1 的结构：

    单元 i 出口模拟流量 = 本单元区间产流（新安江）
                       + Σ 直接上游单元出流（马斯京根分段演算至本单元出口）

演算河段的 KE 按上下游单元出口的**空间距离**自动估算（弯曲系数 1.3、波速 1.5 m/s），
XE 取本单元参数；分段数按稳定性自动确定。亦可对某单元显式指定 KE 覆盖自动值。
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

import numpy as np

from ..timeseries import match_series_key
from . import routing
from .metrics import all_metrics
from .params import default_params, sanitize_params
from .xaj import simulate_unit

MM_PER_STEP_TO_M3S = 1000.0  # Q[m³/s] = mm × km² × 1000 / Δt_s


def _series_map(records) -> dict:
    return {dt: v for dt, v in records}


def _axis(start: datetime, end: datetime, step_s: int) -> list[datetime]:
    n = int(round((end - start).total_seconds() / step_s)) + 1
    return [start + timedelta(seconds=i * step_s) for i in range(n)]


def great_circle_km(lon1, lat1, lon2, lat2) -> float:
    r = 6371.0
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dp = p2 - p1
    dl = math.radians(float(lon2) - float(lon1))
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def reach_params(up_xy: dict, down_xy: dict, dt_h: float, xe: float, ke_override: float | None = None) -> dict:
    """由上下游出口坐标估算演算河段参数（KE 汇流时间、分段数）。"""
    if ke_override and float(ke_override) > 0:
        ke = float(ke_override)
    elif up_xy.get("lon") and down_xy.get("lon"):
        dist = great_circle_km(up_xy["lon"], up_xy["lat"], down_xy["lon"], down_xy["lat"])
        t_h, _n = routing.segment_count(dist, dt_h)  # segment_count 内含弯曲系数与波速
        ke = min(max(t_h, dt_h / 2.0), 120.0)
    else:
        ke = dt_h  # 缺省：一个计算时段
    _t, n = routing.segment_count(max(ke, 0.1), dt_h)
    return {"KE": round(ke, 2), "XE": float(xe), "n": n}


def _outlet_xy(sb: dict) -> dict:
    o = sb.get("outlet") or {}
    return {"lon": o.get("lon"), "lat": o.get("lat")}


def _prepare_inputs(
    subbasins_doc: dict,
    manifest: dict,
    series_loader,
    period: dict | None = None,
    extend: dict | None = None,
) -> dict:
    """模拟与率定目标**共用的前置装配**：时段轴 / 蒸发 / 逐单元面雨量。

    这是最重的开销（读序列 + 逐时段泰森加权装配，~0.5 s），率定时必须只做一次，
    之后每次目标评估只跑模型核（~6 ms）。返回 dict，失败时 ``{"ok": False, "error": ...}``。

    ``extend``（P6 预报）：在时序末尾拼接未来时段——
    ``{days: 预见期天数, rain_mm: 未来逐日面雨量(标量或序列), evap_mm?: 蒸发假设(默认取历史均值)}``。
    各单元的观测面雨量只覆盖历史段，未来段统一用 ``rain_mm``（情景雨情）。
    """
    sbs = sorted(subbasins_doc.get("subbasins", []), key=lambda s: (s.get("order_index") or 0))
    if not sbs:
        return {"ok": False, "error": "尚未划分子流域"}

    # ---------------- 时段基准（蒸发序列优先，其次任一降雨序列）
    evap_items = list((manifest.get("series", {}).get("evap") or {}).values())
    rain_items = list((manifest.get("series", {}).get("rain") or {}).values())
    base = evap_items[0] if evap_items else (rain_items[0] if rain_items else None)
    if base is None:
        return {"ok": False, "error": "没有时序数据：请先导入或生成降雨/蒸发序列"}
    step_s = int(base.get("interval_s") or 86400)
    dt_h = step_s / 3600.0
    dt_s = float(step_s)

    start, end = base.get("start"), base.get("end")
    if period and period.get("start"):
        start = max(start, period["start"]) if start else period["start"]
    if period and period.get("end"):
        end = min(end, period["end"]) if end else period["end"]
    start_dt = datetime.strptime(start, "%Y-%m-%d %H:%M")
    end_dt = datetime.strptime(end, "%Y-%m-%d %H:%M")
    axis = _axis(start_dt, end_dt, step_s)
    n = len(axis)
    if n < 30:
        return {"ok": False, "error": f"模拟时段过短（仅 {n} 个时段）"}

    warnings: list[str] = []
    cache: dict[str, dict] = {}

    def loader_cached(kind: str, key: str) -> dict:
        ck = f"{kind}/{key}"
        if ck not in cache:
            cache[ck] = _series_map(series_loader(kind, key))
        return cache[ck]

    # ---------------- 蒸发
    if evap_items:
        evap_map = loader_cached("evap", next(iter((manifest["series"]["evap"]).keys())))
        e_arr = np.array([evap_map.get(dt, 3.0) for dt in axis], dtype=float)
    else:
        e_arr = np.full(n, 3.0)
        warnings.append("缺少蒸发序列，按常数 3.0 mm/d 处理")

    # ---------------- 逐单元面雨量（站名匹配一次、序列读取一次）
    rain_keys = list((manifest.get("series", {}).get("rain") or {}).keys())
    unit_rain: dict[str, np.ndarray] = {}
    for sb in sbs:
        stations = [
            (s.get("id") or s.get("name"), float(s.get("weight") or 0))
            for s in (sb.get("rain_stations") or [])
            if (s.get("weight") or 0) > 0
        ]
        tw = sum(w for _, w in stations)
        resolved = []
        for sid, w in stations:
            sk = match_series_key(rain_keys, sid, sid)
            if sk:
                resolved.append((loader_cached("rain", sk), w))
        if not resolved:
            unit_rain[sb["code"]] = np.zeros(n)
            warnings.append(f"{sb['code']}: 无可用雨量序列，面雨量按 0 处理")
            continue
        p_map = np.zeros(n)
        miss = 0
        for i, dt in enumerate(axis):
            got = wsum = 0.0
            for m, w in resolved:
                v = m.get(dt)
                if v is not None:
                    got += w * float(v)
                    wsum += w
            p_map[i] = got / wsum if wsum > 0 else 0.0
            if wsum == 0:
                miss += 1
        unit_rain[sb["code"]] = p_map
        if miss:
            warnings.append(f"{sb['code']}: {miss} 个时段面雨量缺测，按 0 处理")

    # ---------------- 预报扩展（P6）：历史装配完成后，在时序末尾拼接未来情景时段
    extend = extend or {}
    ext_days = min(max(int(extend.get("days") or 0), 0), 3650)
    n_obs = n
    if ext_days:
        ext_evap = extend.get("evap_mm")
        if ext_evap is None:
            ext_evap = float(np.mean(e_arr)) if len(e_arr) else 3.0
        last = axis[-1]
        axis = axis + [last + timedelta(seconds=step_s * (i + 1)) for i in range(ext_days)]
        n = len(axis)
        e_arr = np.concatenate([e_arr, np.full(ext_days, float(ext_evap))])
        warnings.append(f"预报：末尾拼接 {ext_days} 天情景时段（蒸发按 {ext_evap:.1f} mm/d）")
        raw_vals = extend.get("rain_mm")
        if raw_vals is None:
            vals = [0.0] * ext_days
        elif isinstance(raw_vals, (list, tuple)):
            seq = [float(v) for v in raw_vals]
            vals = (seq + [0.0] * ext_days)[:ext_days]
        else:
            vals = [float(raw_vals)] * ext_days
        for code, arr in unit_rain.items():
            unit_rain[code] = np.concatenate([arr, np.array(vals, dtype=float)])
        warnings.append(f"预报：未来 {ext_days} 天采用情景降雨（合计 {sum(vals):.1f} mm），非实测")

    return {
        "ok": True,
        "sbs": sbs,
        "axis": axis,
        "times": [dt.strftime("%Y-%m-%d %H:%M") for dt in axis],
        "n": n,
        "n_obs": n_obs,
        "extend_days": ext_days,
        "step_s": step_s,
        "dt_h": dt_h,
        "dt_s": dt_s,
        "e_arr": e_arr if len(e_arr) == n else np.full(n, 3.0),
        "unit_rain": unit_rain,
        "loader_cached": loader_cached,
        "warnings": warnings,
    }


def unit_context(
    subbasins_doc: dict,
    manifest: dict,
    series_loader,
    code: str,
    period: dict | None = None,
) -> dict:
    """为**单个单元**的率定目标函数预计算全部固定量（只做一次，之后每次评估仅跑模型核）。

    返回 dict：``{code, name, area_km2, dt_s, dt_h, p(面雨量), e(蒸发), times,
    upstream[{code, ke, q?}], obs, outlet_station, i0}``。
    ``upstream[].q`` 由链式率定在率定到该单元前填入（上游已率定的出流）。
    """
    ctx = _prepare_inputs(subbasins_doc, manifest, series_loader, period)
    if not ctx.get("ok"):
        return ctx
    sbs, axis, times, n = ctx["sbs"], ctx["axis"], ctx["times"], ctx["n"]
    dt_h = ctx["dt_h"]
    sb = next((s for s in sbs if s.get("code") == code), None)
    if sb is None:
        return {"ok": False, "error": f"未找到预报单元 {code}"}
    area = float(sb.get("area_km2") or 0.0)
    if area <= 0:
        return {"ok": False, "error": f"{code} 单元面积异常"}

    # 直接上游：演算河段 KE 只依赖几何与时段（XE 属本单元参数，评估时才代入）
    upstream = []
    for other in sbs:
        if other.get("parent") != code:
            continue
        rp = reach_params(_outlet_xy(other), _outlet_xy(sb), dt_h, 0.2)
        upstream.append({"code": other["code"], "ke": rp["KE"], "q": None})

    # 出口站实测
    obs = None
    o_station = sb.get("outlet_station") or {}
    o_sid = o_station.get("id") or (sb.get("outlet") or {}).get("control_id")
    o_sname = o_station.get("name") or (sb.get("outlet") or {}).get("control")
    if o_sid or o_sname:
        flow_keys = list((manifest.get("series", {}).get("flow") or {}).keys())
        fk = match_series_key(flow_keys, o_sid or "", o_sname or "")
        if fk:
            obs_map = ctx["loader_cached"]("flow", fk)
            obs = np.array([obs_map.get(dt, np.nan) for dt in axis], dtype=float)

    warmup_days = int((period or {}).get("warmup_days") or 0)
    i0 = min(max(int(warmup_days * 3600.0 / ctx["step_s"]), 0), max(0, n - 10))

    out = {
        "ok": True,
        "code": code,
        "name": sb.get("name"),
        "area_km2": area,
        "dt_s": ctx["dt_s"],
        "dt_h": dt_h,
        "step_s": ctx["step_s"],
        "n": n,
        "times": times,
        "p": ctx["unit_rain"].get(code, np.zeros(n)),
        "e": ctx["e_arr"],
        "upstream": upstream,
        "has_upstream": bool(upstream),
        "obs": obs,
        "outlet_station": o_sname or None,
        "outlet_station_id": o_sid or None,
        "i0": i0,
        "warnings": list(ctx["warnings"]),
    }
    return out


def eval_unit(ctx: dict, params: dict) -> dict:
    """用给定参数评估单个单元（率定目标的热路径，~6 ms）。

    需 ``ctx`` 由 :func:`unit_context` 生成，且 ``ctx["upstream"][i]["q"]`` 已填入
    上游已率定的出流。返回 ``{q(全时段 m³/s), balance}``。
    """
    prm = sanitize_params(params)
    res = simulate_unit(ctx["p"], ctx["e"], prm)
    q_out = res["q"] * ctx["area_km2"] * MM_PER_STEP_TO_M3S / ctx["dt_s"]
    for up in ctx.get("upstream") or []:
        if up.get("q") is None:
            continue
        q_out = q_out + routing.muskingum_route(up["q"], up["ke"], prm["XE"], ctx["dt_h"])
    return {"q": q_out, "balance": res["balance"], "params": prm}


def metrics_of(ctx: dict, q: np.ndarray, i_start: int | None = None, i_end: int | None = None) -> dict | None:
    """单元出口的指标（默认暖期起至序列末尾），并把峰现时段换算为实际时刻。"""
    obs = ctx.get("obs")
    if obs is None:
        return None
    n = ctx["n"]
    a = ctx["i0"] if i_start is None else min(max(int(i_start), 0), n)
    b = n if i_end is None else min(max(int(i_end), a), n)
    o = np.asarray(obs)[a:b]
    s = np.asarray(q)[a:b]
    valid = np.isfinite(o) & np.isfinite(s)
    t_valid = [t for t, m in zip(ctx["times"][a:b], valid) if m]
    met = all_metrics(o[valid], s[valid], dt_label=f"{ctx['step_s'] / 3600:g}h")
    pe = met.get("peak_error")
    if pe:
        for e in pe.get("events") or []:
            if 0 <= e["obs_step"] < len(t_valid):
                e["obs_time"] = t_valid[e["obs_step"]]
            if 0 <= e["sim_step"] < len(t_valid):
                e["sim_time"] = t_valid[e["sim_step"]]
    return met


def simulate_basin(
    subbasins_doc: dict,
    manifest: dict,
    series_loader,
    params: dict | None = None,
    period: dict | None = None,
    return_raw: bool = False,
    extend: dict | None = None,
) -> dict:
    """全流域模拟。

    - subbasins_doc: storage.read_subbasins() 结果
    - manifest: 时序清单（用其统计的起止与时段）
    - series_loader(kind, key) -> [(dt, value)]：序列读取回调
    - params: {unit_code: {K,B,SM,EX,KGF,CG,CI,CS,XE,KE?}}（缺省补默认）
    - period: {"start","end","warmup_days"}（可选）
    - extend: 预报扩展（见 :func:`_prepare_inputs`），P6 情景预报用
    """
    ctx = _prepare_inputs(subbasins_doc, manifest, series_loader, period, extend=extend)
    if not ctx.get("ok"):
        return ctx
    sbs = ctx["sbs"]
    axis, times, n = ctx["axis"], ctx["times"], ctx["n"]
    step_s, dt_h, dt_s = ctx["step_s"], ctx["dt_h"], ctx["dt_s"]
    e_arr, unit_rain = ctx["e_arr"], ctx["unit_rain"]
    loader_cached = ctx["loader_cached"]
    warnings = list(ctx["warnings"])

    # ---------------- 逐单元（排水序）演算
    unit_q: dict[str, np.ndarray] = {}
    unit_info: list[dict] = []
    params = params or {}

    for sb in sbs:
        code = sb["code"]
        area = float(sb.get("area_km2") or 0.0)
        if area <= 0:
            warnings.append(f"{code}: 单元面积异常，跳过")
            continue
        reach_km = float(sb.get("river_length_km") or 0.0)
        prm = sanitize_params(params.get(code) or default_params(reach_km, dt_h))

        p_unit = unit_rain.get(code, np.zeros(n))
        res = simulate_unit(p_unit, e_arr, prm)
        q_local = res["q"] * area * MM_PER_STEP_TO_M3S / dt_s  # m³/s

        # 直接上游来流经河道演算至本单元出口
        q_up_total = np.zeros(n)
        upstream = []
        for other in sbs:
            if other.get("parent") != code:
                continue
            upstream.append(other["code"])
            if other["code"] not in unit_q:
                continue
            rp = reach_params(_outlet_xy(other), _outlet_xy(sb), dt_h, prm["XE"])
            q_up_total += routing.muskingum_route(unit_q[other["code"]], rp["KE"], rp["XE"], dt_h)

        q_out = q_local + q_up_total
        unit_q[code] = q_out

        # 出口站实测对比
        obs = None
        o_station = sb.get("outlet_station") or {}
        o_sid = o_station.get("id") or (sb.get("outlet") or {}).get("control_id")
        o_sname = o_station.get("name") or (sb.get("outlet") or {}).get("control")
        if o_sid or o_sname:
            flow_keys = list((manifest.get("series", {}).get("flow") or {}).keys())
            fk = match_series_key(flow_keys, o_sid or "", o_sname or "")
            if fk:
                obs_map = loader_cached("flow", fk)
                obs = np.array([obs_map.get(dt, np.nan) for dt in axis], dtype=float)

        unit_info.append(
            {
                "code": code,
                "name": sb.get("name"),
                "area_km2": area,
                "params": prm,
                "upstream": upstream,
                "outlet_station": o_sname or None,
                "outlet_station_id": o_sid or None,
                "q": q_out,
                "obs": obs,
                "balance": res["balance"],
            }
        )

    # ---------------- 指标（剔除暖期）与序列抽稀
    warmup_days = int((period or {}).get("warmup_days") or 0)
    i0 = min(max(int(warmup_days * 3600.0 / step_s), 0), max(0, n - 10))

    out_units = []
    for u in unit_info:
        q, obs = u["q"], u["obs"]
        met = None
        if obs is not None:
            valid = np.isfinite(obs[i0:]) & np.isfinite(q[i0:])
            t_valid = [t for t, m in zip(times[i0:], valid) if m]
            met = all_metrics(obs[i0:][valid], q[i0:][valid], dt_label=f"{step_s / 3600:g}h")
            pe = met.get("peak_error") if met else None
            if pe:  # 把时段序号换算成实际时刻（序列抽稀后序号不可直接对应）
                for e in pe.get("events") or []:
                    if 0 <= e["obs_step"] < len(t_valid):
                        e["obs_time"] = t_valid[e["obs_step"]]
                    if 0 <= e["sim_step"] < len(t_valid):
                        e["sim_time"] = t_valid[e["sim_step"]]
        stride = max(1, (n - i0) // 1200)
        out_units.append(
            {
                "code": u["code"],
                "name": u["name"],
                "area_km2": u["area_km2"],
                "params": u["params"],
                "upstream": u["upstream"],
                "outlet_station": u["outlet_station"],
                "balance": u["balance"],
                "metrics": met,
                "series": {
                    "time": times[i0::stride],
                    "sim": [round(float(v), 3) for v in q[i0::stride]],
                    "obs": (
                        [round(float(v), 3) if np.isfinite(v) else None for v in obs[i0::stride]]
                        if obs is not None
                        else None
                    ),
                },
            }
        )

    out = {
        "ok": True,
        "dt_s": step_s,
        "n_steps": n,
        "period": {"start": times[i0], "end": times[-1], "warmup_steps": i0},
        "units": out_units,
        "water_balance": [{"code": u["code"], **u["balance"]} for u in unit_info],
        "warnings": warnings,
    }
    if ctx.get("extend_days"):
        out["forecast"] = {
            "start": times[ctx["n_obs"]],
            "days": ctx["extend_days"],
            "n_obs": ctx["n_obs"],
        }
    if return_raw:
        # 内部使用（真值流量生成 / 率定目标函数），不参与 JSON 序列化
        out["_raw"] = {
            "axis": axis,
            "times": times,
            "units": [
                {
                    "code": u["code"],
                    "outlet_station_id": u.get("outlet_station_id"),
                    "outlet_station": u.get("outlet_station"),
                    "q": u["q"],
                    "obs": u["obs"],
                }
                for u in unit_info
            ],
        }
    return out


def truth_basin_flow(
    subbasins_doc: dict,
    manifest: dict,
    series_loader,
    truth: dict | None = None,
    noise: float = 0.05,
    seed: int = 20260921,
    period: dict | None = None,
) -> dict:
    """观测系统模拟实验（OSSE）：用**真值参数**跑一遍全流域模型，产出各单元出口站的
    合成"实测"流量序列（叠加乘性观测噪声），供率定回收验证。

    与 ``simulate_basin`` 使用完全相同的模型结构与参数化，因此用真值参数率定应能
    把 NSE 推到 ≈ 0.99，是检验 P3 率定引擎是否收敛的基准。

    返回 ``{station_id: {"name": 站名, "code": 单元, "records": [(datetime, Q)]}}``。
    """
    import random

    from .params import DEMO_TRUTH_PARAMS

    truth = dict(truth or DEMO_TRUTH_PARAMS)
    evap_items = list((manifest.get("series", {}).get("evap") or {}).values())
    rain_items = list((manifest.get("series", {}).get("rain") or {}).values())
    base = evap_items[0] if evap_items else (rain_items[0] if rain_items else None)
    if base is None:
        return {}
    dt_h = float(base.get("interval_s") or 86400) / 3600.0

    # 每单元：真值参数 + 按河长估算的 KE
    params: dict[str, dict] = {}
    for sb in subbasins_doc.get("subbasins", []):
        prm = default_params(float(sb.get("river_length_km") or 0.0), dt_h)
        prm.update(truth)
        params[sb["code"]] = prm

    res = simulate_basin(subbasins_doc, manifest, series_loader, params=params, period=period, return_raw=True)
    if not res.get("ok"):
        return {}

    rnd = random.Random(seed)
    axis = res["_raw"]["axis"]
    out: dict[str, dict] = {}
    for u in res["_raw"]["units"]:
        sid = u.get("outlet_station_id")
        if not sid:
            continue
        recs = []
        for t, q in zip(axis, u["q"]):
            noisy = float(q) * (1.0 + rnd.gauss(0.0, noise))
            recs.append((t, round(max(0.0, noisy), 3)))
        out[sid] = {"name": u.get("outlet_station") or sid, "code": u["code"], "records": recs}
    return out
