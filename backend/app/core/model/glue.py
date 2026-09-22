"""GLUE 参数不确定性分析（可选项，Beven & Binley 1992）。

流程：

1. 在各单元自由参数的率定区间内**均匀随机采样** n 组参数（锁定参数固定为表内值）；
2. 每组参数按排水序**链式演算**全流域（复用 unit_context/eval_unit 热路径，
   单样本 ≈ 5 ms/单元，1000 组 ≈ 15 s）；
3. 以各站**率定期 NSE** 作为似然度量；NSE ≥ 阈值（默认 0.7）的样本为**行为样本**
   （behavioural），权重 = NSE²；若无样本达阈值，回退为 NSE 前 10% 并在 warnings 说明；
4. 行为样本加权推求各站模拟流量的 **5% / 50% / 95% 分位带**（预报不确定性区间），
   并输出行为样本的**参数后验范围**（对比率定区间可读出参数可辨识性）。

与率定共用同一套演算实现，结果与「模型模拟 / 率定」面板同源。
"""
from __future__ import annotations

import time

import numpy as np

from .params import PARAM_KEYS, PARAM_SPEC, default_params
from .simulate import eval_unit, unit_context


def _nse(obs, sim) -> float:
    v = np.isfinite(obs) & np.isfinite(sim)
    if int(v.sum()) < 30:
        return -9e9
    o, s = obs[v], sim[v]
    denom = float(((o - o.mean()) ** 2).sum())
    if denom <= 0:
        return -9e9
    return float(1.0 - ((o - s) ** 2).sum() / denom)


def _wquantiles(mat: np.ndarray, w: np.ndarray, qs=(0.05, 0.5, 0.95)) -> dict:
    """按样本权重逐时段求加权分位数。mat: (n_sample, n_step)。"""
    order = np.argsort(mat, axis=0, kind="stable")
    v = np.take_along_axis(mat, order, axis=0)
    wcol = w[order]                       # (n_sample, n_step)
    cw = np.cumsum(wcol, axis=0)
    cw = cw / cw[-1:]
    out = {}
    for q in qs:
        col = np.empty(mat.shape[1], dtype=float)
        for j in range(mat.shape[1]):
            col[j] = np.interp(q, cw[:, j], v[:, j])
        out[q] = col
    return out


def run_glue(
    subbasins_doc: dict,
    manifest: dict,
    series_loader,
    *,
    config: dict | None = None,
    stop_flag=None,
    on_progress=None,
) -> dict:
    """GLUE 入口（同步执行）。返回分位带 + 行为参数范围 + 每站行为样本统计。

    config: {n_samples(默认1000, 钳50~5000), threshold(默认0.7), seed, split,
             lock: {code:{参数:值}}, period: {start,end,warmup_days}}
    """
    cfg = config or {}
    n_samples = min(max(int(cfg.get("n_samples") or 1000), 50), 5000)
    threshold = float(cfg.get("threshold") or 0.7)
    seed = int(cfg.get("seed") or 0)
    split = min(max(float(cfg.get("split") or 0.7), 0.3), 0.95)
    lock = cfg.get("lock") or {}
    period = cfg.get("period") or None

    sbs = sorted(subbasins_doc.get("subbasins", []), key=lambda s: (s.get("order_index") or 0))
    if not sbs:
        return {"ok": False, "error": "尚未划分子流域"}

    contexts: dict[str, dict] = {}
    for sb in sbs:
        c = unit_context(subbasins_doc, manifest, series_loader, sb["code"], period=period)
        if not c.get("ok"):
            return {"ok": False, "error": c.get("error") or f"{sb['code']} 上下文构建失败"}
        contexts[sb["code"]] = c

    ref = contexts[sbs[0]["code"]]
    n = ref["n"]
    i0 = ref["i0"]
    i_split = int(i0 + (n - i0) * split)

    # ---- 各单元采样方案：自由参数（同率定）+ 区间内均匀采样
    unit_plan = []
    rng = np.random.default_rng(seed)
    for sb in sbs:
        code = sb["code"]
        ctx = contexts[code]
        lock_u = lock.get(code) or {}
        keys = [k for k in PARAM_KEYS if k != "XE"]
        if ctx.get("has_upstream"):
            keys.append("XE")
        keys = [k for k in keys if k not in lock_u]
        bounds = np.array(
            [[float(PARAM_SPEC[k]["min"]), float(PARAM_SPEC[k]["max"])] for k in keys],
            dtype=float,
        ).reshape(len(keys), 2)
        samples = (
            np.ones((n_samples, len(keys)), dtype=np.float64)
            if not keys
            else rng.uniform(bounds[:, 0], bounds[:, 1], size=(n_samples, len(keys)))
        )
        base = default_params(float(sb.get("river_length_km") or 0.0), ctx["dt_h"])
        for k, v in lock_u.items():
            if k in PARAM_KEYS:
                base[k] = float(v)
        unit_plan.append(
            {"code": code, "ctx": ctx, "keys": keys, "samples": samples,
             "base": base, "bounds": bounds, "obs": ctx.get("obs")}
        )
    by_code = {u["code"]: u for u in unit_plan}

    # ---- 参与统计的站（有实测且率定期有效点 ≥30）
    stations = []
    for u in unit_plan:
        obs = u["obs"]
        if obs is None:
            continue
        o = np.asarray(obs)[i0:i_split]
        if int(np.isfinite(o).sum()) < 30:
            continue
        stations.append(u["code"])

    def emit(payload: dict):
        if on_progress:
            try:
                on_progress(payload)
            except Exception:  # noqa: BLE001
                pass

    emit({"phase": "start", "n_samples": n_samples, "stations": stations})
    t0 = time.perf_counter()

    # ---- Monte Carlo 链式演算
    sim: dict[str, np.ndarray] = {
        c: np.full((n_samples, n), np.nan, dtype=np.float32) for c in stations
    }
    nse: dict[str, np.ndarray] = {c: np.full(n_samples, -9e9, dtype=float) for c in stations}

    for i in range(n_samples):
        if stop_flag and stop_flag():
            return {"ok": False, "error": "分析被终止", "status": "stopped"}
        qs: dict[str, np.ndarray] = {}
        for u in unit_plan:
            code, ctx = u["code"], u["ctx"]
            for up in ctx["upstream"]:
                up["q"] = qs.get(up["code"])
            prm = dict(u["base"])
            for k, v in zip(u["keys"], u["samples"][i]):
                prm[k] = float(v)
            qs[code] = eval_unit(ctx, prm)["q"]
        for code in stations:
            q = qs[code]
            sim[code][i] = q.astype(np.float32)
            nse[code][i] = _nse(np.asarray(by_code[code]["obs"])[i0:i_split],
                                q[i0:i_split])
        if (i + 1) % 200 == 0:
            emit({"phase": "run", "done": i + 1, "n_samples": n_samples,
                  "elapsed_s": round(time.perf_counter() - t0, 1)})

    elapsed = round(time.perf_counter() - t0, 1)

    # ---- 行为样本与分位带（逐站）
    stride = max(1, (n - i0) // 1200)
    times = ref["times"]
    out_stations = []
    param_ranges = None
    for code in stations:
        vals = nse[code]
        best_i = int(np.argmax(vals))
        beh = np.where(vals >= threshold)[0]
        fallback = False
        if beh.size < 10:
            beh = np.argsort(vals)[-max(10, n_samples // 10):]
            fallback = True
        w = np.maximum(vals[beh], 0.0) ** 2
        if w.sum() <= 0:
            w = np.ones(beh.size)
        q = _wquantiles(sim[code][beh].astype(float), w)
        u = by_code[code]
        obs = np.asarray(u["obs"])
        u_out = {
            "code": code,
            "name": u["ctx"].get("name"),
            "area_km2": u["ctx"]["area_km2"],
            "outlet_station": u["ctx"].get("outlet_station"),
            "nse_best": round(float(vals[best_i]), 4),
            "nse_median_beh": round(float(np.median(vals[beh])), 4),
            "behavioral_count": int(beh.size),
            "fallback": fallback,
            "threshold": threshold,
            "series": {
                "time": times[i0::stride],
                "obs": [
                    round(float(v), 3) if np.isfinite(v) else None
                    for v in obs[i0::stride]
                ],
                "sim_best": [round(float(x), 3) for x in sim[code][best_i][i0::stride]],
                "q05": [round(float(x), 3) for x in q[0.05][i0::stride]],
                "q50": [round(float(x), 3) for x in q[0.5][i0::stride]],
                "q95": [round(float(x), 3) for x in q[0.95][i0::stride]],
            },
        }
        # 50% 分位带覆盖率（观测点落在 q05~q95 内的比例）
        o_seg, lo, hi = obs[i0:], q[0.05], q[0.95]
        v = np.isfinite(o_seg)
        if int(v.sum()):
            cov = float(((o_seg[v] >= lo[v]) & (o_seg[v] <= hi[v])).mean())
            u_out["cover_90"] = round(cov, 3)
        out_stations.append(u_out)

    # ---- 行为参数范围（全局：NSE 最优站的判定口径，供可辨识性判读）
    main = stations[-1] if stations else None
    if main:
        vals = nse[main]
        beh = np.where(vals >= threshold)[0]
        if beh.size < 10:
            beh = np.argsort(vals)[-max(10, n_samples // 10):]
        rows = []
        for u in unit_plan:
            for j, k in enumerate(u["keys"]):
                col = u["samples"][beh, j]
                rows.append(
                    {
                        "code": u["code"], "key": k,
                        "min": round(float(col.min()), 4),
                        "max": round(float(col.max()), 4),
                        "range_lo": float(PARAM_SPEC[k]["min"]),
                        "range_hi": float(PARAM_SPEC[k]["max"]),
                    }
                )
        param_ranges = rows

    return {
        "ok": True,
        "method": "GLUE",
        "n_samples": n_samples,
        "threshold": threshold,
        "split": split,
        "seed": seed,
        "elapsed_s": elapsed,
        "stations": out_stations,
        "param_ranges": param_ranges,
        "warnings": (
            ["部分站无样本达到行为阈值，已回退为 NSE 前 10% 样本"]
            if any(s["fallback"] for s in out_stations)
            else []
        ),
    }
