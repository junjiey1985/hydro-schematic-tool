"""SCE-UA 率定（P3 链式 / P5 联合）。

对应技术方案 §6.3 / §6.4：

- **链式策略（默认）**：按排水序（order_index）自上而下逐单元率定；率定某单元时，
  其直接上游参数已定，上游出流经马斯京根演算后作为该单元的已知入流——每步只优化
  一个单元的自由参数，避免高维联合优化；
- **联合模式（P5）**：所有有出口实测的单元同时优化，目标 = 各站目标函数的加权平均
  （权重默认均等，可按单元指定）；支持**参数共享分组**（``share: {参数: [单元...]}``，
  组内该参数只占一个决策变量）；决策维度 = Σ单元自由参数 − 共享合并数；
- **目标函数**：``F = (1 − NSE) + 0.3·|PBIAS|/100 + 0.1·RMSE/std(obs)``，在率定期上
  计算（序列按 ``split`` 比例切成率定期 / 验证期，验证期只评估不再调参）；
- **算法**：SCE-UA（Duan et al. 1992）。复合体数 = 自由参数数、每复合体 2p+1 点，
  子复合体 p+1 点，梯形概率选择，反射 / 收缩 / 随机变异三步替换最差点；
- **性能**：目标评估走 :func:`~app.core.model.simulate.eval_unit` 热路径（预计算
  面雨量 / 蒸发 / 河道 KE，单次 ≈ 5 ms/单元），链式 3000 次评估 ≈ 15 s/单元。

无出口站的单元不参与率定：链上演算先用默认参数（其误差被下游率定吸收），最终参数
**借用直接下游已率定单元**，并用最终参数集整体复算一遍作为权威结果（两模式一致）。
"""
from __future__ import annotations

import time

import numpy as np

from .metrics import all_metrics
from .params import PARAM_KEYS, PARAM_SPEC, default_params
from .simulate import eval_unit, metrics_of, simulate_basin, unit_context

W_PBIAS = 0.3      # 目标函数中水量偏差权重
W_RMSE = 0.1       # 目标函数中归一化 RMSE 权重
DEFAULT_SPLIT = 0.7        # 率定期占比（其余为验证期）
DEFAULT_MAX_EVALS = 3000
DEFAULT_TOL = 1e-4
DEFAULT_K_STOP = 5


# ---------------------------------------------------------------- 指标
def metrics_pair(obs, q, times, i0: int, i1: int, dt_label: str = "") -> dict | None:
    """在 [i0, i1) 上计算指标（剔除缺测对），峰现时刻换算为实际时刻。"""
    if obs is None:
        return None
    o = np.asarray(obs)[i0:i1]
    s = np.asarray(q)[i0:i1]
    valid = np.isfinite(o) & np.isfinite(s)
    t_valid = [t for t, m in zip(list(times)[i0:i1], valid) if m]
    met = all_metrics(o[valid], s[valid], dt_label=dt_label)
    pe = met.get("peak_error")
    if pe:
        for e in pe.get("events") or []:
            if 0 <= e["obs_step"] < len(t_valid):
                e["obs_time"] = t_valid[e["obs_step"]]
            if 0 <= e["sim_step"] < len(t_valid):
                e["sim_time"] = t_valid[e["sim_step"]]
    return met


def objective_value(met: dict, std_obs: float) -> float:
    """F = (1−NSE) + 0.3·|PBIAS|/100 + 0.1·RMSE/std(obs)。"""
    nse = met.get("nse")
    if nse is None:
        return 1e6
    rmse_n = (met.get("rmse") or 0.0) / std_obs if std_obs > 0 else 0.0
    return float((1.0 - nse) + W_PBIAS * abs(met.get("pbias") or 0.0) / 100.0 + W_RMSE * rmse_n)


# ---------------------------------------------------------------- SCE-UA
def sceua(
    func,
    bounds: list[tuple[float, float]],
    *,
    max_evals: int = DEFAULT_MAX_EVALS,
    n_complex: int | None = None,
    m: int | None = None,
    tol: float = DEFAULT_TOL,
    k_stop: int = DEFAULT_K_STOP,
    seed: int = 0,
    on_gen=None,
    stop_flag=None,
) -> dict:
    """SCE-UA 全局优化（最小化）。

    返回 ``{best_x, best_f, evals, gens, status(converged|max_evals|stopped), history}``。
    ``on_gen(gen, best_f, evals, best_x)`` 每代回调（进度落盘）。
    """
    p = len(bounds)
    m = int(m or 2 * p + 1)              # 每复合体点数
    q = p + 1                            # 子复合体点数
    s = int(n_complex or max(2, p))      # 复合体数
    lo = np.array([float(b[0]) for b in bounds], dtype=float)
    hi = np.array([float(b[1]) for b in bounds], dtype=float)
    rng = np.random.default_rng(seed)
    npts = s * m
    beta = m                             # 每复合体每次大循环的 CCE 演化次数

    state = {"evals": 0, "stop": False}

    def feval(x: np.ndarray) -> float:
        state["evals"] += 1
        try:
            return float(func(np.clip(x, lo, hi)))
        except Exception:  # noqa: BLE001 —— 个别参数组合数值异常时按坏点处理
            return 1e6

    # 初始种群：均匀随机撒点（标准 SCE-UA 做法）
    pts = lo + (hi - lo) * rng.random((npts, p))
    fx = np.array([feval(x) for x in pts], dtype=float)

    # 梯形选择概率（子复合体内越优被选概率越大）
    w = np.array([2.0 * (m + 1 - i) / (m * (m + 1)) for i in range(1, m + 1)])

    def evolve_complex(pts_c: np.ndarray, fx_c: np.ndarray):
        """对一个复合体做 beta 次 CCE 演化，返回新 (点, 值)。"""
        for _ in range(beta):
            if state["evals"] >= max_evals or (stop_flag and stop_flag()):
                break
            order = np.argsort(fx_c, kind="stable")
            pts_c, fx_c = pts_c[order], fx_c[order]
            idx = rng.choice(m, size=q, replace=False, p=w)
            sub, subf = pts_c[idx], fx_c[idx]
            j = int(np.argmax(subf))          # 子复合体最差点
            worst, fworst = sub[j], float(subf[j])
            cen = sub.mean(axis=0)            # 质心（含最差点，标准做法）

            newx, newf = None, None
            xr = np.clip(2.0 * cen - worst, lo, hi)          # 反射
            fr = feval(xr)
            if fr < fworst:
                newx, newf = xr, fr
            else:
                xc = np.clip(0.5 * (cen + worst), lo, hi)    # 收缩
                fc = feval(xc)
                if fc < fworst:
                    newx, newf = xc, fc
                else:                                        # 随机变异
                    xn = lo + (hi - lo) * rng.random(p)
                    newx, newf = xn, feval(xn)
            pts_c[idx[j]], fx_c[idx[j]] = newx, newf
        return pts_c, fx_c

    hist: list[dict] = []
    best_hist: list[float] = []
    gen = 0
    status = "max_evals"

    while state["evals"] < max_evals:
        order = np.argsort(fx, kind="stable")
        pts, fx = pts[order], fx[order]
        for k in range(s):                     # 连续块划分复合体（Duan 1992）
            blk = slice(k * m, (k + 1) * m)
            pts[blk], fx[blk] = evolve_complex(pts[blk].copy(), fx[blk].copy())
        order = np.argsort(fx, kind="stable")
        pts, fx = pts[order], fx[order]

        gen += 1
        best = float(fx[0])
        best_hist.append(best)
        hist.append({"gen": gen, "best_f": round(best, 6), "evals": state["evals"]})
        if on_gen:
            on_gen(gen, best, state["evals"], [float(v) for v in pts[0]])

        if stop_flag and stop_flag():
            status = "stopped"
            break
        if len(best_hist) > k_stop and abs(best_hist[-1 - k_stop] - best) < tol:
            status = "converged"
            break

    order = np.argsort(fx, kind="stable")
    return {
        "best_x": [float(v) for v in pts[order[0]]],
        "best_f": float(fx[order[0]]),
        "evals": state["evals"],
        "gens": gen,
        "status": status,
        "history": hist,
    }


# ---------------------------------------------------------------- 公共
def free_spec_for(ctx: dict, lock: dict | None = None) -> tuple[list[str], list[tuple[float, float]]]:
    """单元的自由参数与率定区间。

    8 个产流参数恒开放；XE 仅当该单元有直接上游（来流演算用得到）才开放，
    否则不可辨识。``lock`` 中的键被固定为给定值、从自由集中剔除。
    """
    lock = lock or {}
    keys = [k for k in PARAM_KEYS if k != "XE"]
    if ctx.get("has_upstream"):
        keys.append("XE")
    keys = [k for k in keys if k not in lock]
    bounds = [(float(PARAM_SPEC[k]["min"]), float(PARAM_SPEC[k]["max"])) for k in keys]
    return keys, bounds


def _base_params(sb: dict, ctx: dict, lock: dict | None = None) -> dict:
    """单元的基础参数：按河长默认 + 项目锁定值。"""
    prm = default_params(float(sb.get("river_length_km") or 0.0), ctx["dt_h"])
    for k, v in (lock or {}).items():
        if k in PARAM_KEYS:
            prm[k] = float(v)
    return prm


def _station_f(ctx: dict, q: np.ndarray, i0: int, i1: int) -> float | None:
    """单站目标值（无实测返回 None）。"""
    if ctx.get("obs") is None:
        return None
    o = np.asarray(ctx["obs"])[i0:i1]
    n_obs = int(np.isfinite(o).sum())
    if n_obs < 30:
        return None
    std_obs = float(np.std(o[np.isfinite(o)]))
    met = metrics_of(ctx, q, i_start=i0, i_end=i1)
    return objective_value(met, std_obs) if met else None


def _finalize_basin(
    subbasins_doc: dict,
    manifest: dict,
    series_loader,
    contexts: dict[str, dict],
    final_params: dict[str, dict],
    *,
    period: dict | None,
    split: float,
    sbs: list[dict],
) -> dict:
    """用最终参数集整体复算（权威结果，与「模型模拟」完全同源）并算分期指标。"""
    sim = simulate_basin(subbasins_doc, manifest, series_loader, params=final_params,
                         period=period, return_raw=True)
    if not sim.get("ok"):
        return {"ok": False, "error": sim.get("error") or "最终复算失败"}

    raw = {u["code"]: u for u in sim["_raw"]["units"]}
    axis_times = sim["_raw"]["times"]
    metrics = []
    for sb in sbs:
        code = sb["code"]
        r = raw.get(code)
        if not r:
            continue
        n = sim["n_steps"]
        i0 = min(contexts[code]["i0"], max(0, n - 10))
        i_split = int(i0 + (n - i0) * split)
        dt_label = f"{sim['dt_s'] / 3600:g}h"
        metrics.append(
            {
                "code": code,
                "calib": metrics_pair(r["obs"], r["q"], axis_times, i0, i_split, dt_label),
                "valid": metrics_pair(r["obs"], r["q"], axis_times, i_split, n, dt_label),
                "calib_range": [axis_times[i0], axis_times[min(i_split, n - 1)]],
                "valid_range": [axis_times[min(i_split, n - 1)], axis_times[-1]],
            }
        )

    sim.pop("_raw", None)
    return {"ok": True, "simulation": sim, "metrics": metrics}


def run_calibration(
    subbasins_doc: dict,
    manifest: dict,
    series_loader,
    *,
    config: dict | None = None,
    on_progress=None,
    stop_flag=None,
) -> dict:
    """率定入口（P3 链式 / P5 联合，config.mode 选择，默认链式）。

    - config: {mode?: "chain"|"joint", period{start,end,warmup_days}, split,
               max_evals, seed, tol, lock: {code: {参数: 值}},
               share?: {参数: [单元...]}, weights?: {code: 权重}（联合）}
    - on_progress(payload): 每代回调（进度落盘 / 前端轮询）
    - stop_flag(): 返回 True 则在当前代结束后终止
    """
    cfg = config or {}

    sbs = sorted(subbasins_doc.get("subbasins", []), key=lambda s: (s.get("order_index") or 0))
    if not sbs:
        return {"ok": False, "error": "尚未划分子流域"}

    # ---- 逐单元预计算上下文（面雨量/蒸发/河道 KE/实测，一次性 ~0.5s/单元）
    contexts: dict[str, dict] = {}
    for sb in sbs:
        c = unit_context(subbasins_doc, manifest, series_loader, sb["code"], period=cfg.get("period"))
        if not c.get("ok"):
            return {"ok": False, "error": c.get("error") or f"{sb['code']} 上下文构建失败"}
        contexts[sb["code"]] = c

    def emit(payload: dict):
        if on_progress:
            try:
                on_progress(payload)
            except Exception:  # noqa: BLE001 —— 进度回调失败不应中断率定
                pass

    if stop_flag and stop_flag():
        return {"ok": False, "error": "任务在开始前被终止", "status": "stopped"}

    mode = str(cfg.get("mode") or "chain").strip().lower()
    pack = {"doc": subbasins_doc, "manifest": manifest, "loader": series_loader}
    if mode == "joint":
        return _run_joint(sbs, contexts, cfg, pack, emit, stop_flag)
    return _run_chain(sbs, contexts, cfg, pack, emit, stop_flag)


def _not_run_placeholders(sbs, contexts, units_out: list[dict]) -> None:
    """提前终止时，未跑到的单元补占位记录（前端参数表仍完整可读）。"""
    seen = {u["code"] for u in units_out}
    for sb in sbs:
        code = sb["code"]
        if code in seen:
            continue
        units_out.append(
            {
                "code": code, "name": sb.get("name"), "area_km2": sb.get("area_km2"),
                "params": _base_params(sb, contexts[code]),
                "borrowed_from": None, "calibrated": False, "status": "not_run",
                "note": "任务被终止，该单元未参与率定（沿用默认参数）",
                "evals": 0, "gens": 0, "elapsed_s": 0.0, "convergence": [],
            }
        )


def _borrow_for_skipped(sbs, units_out: list[dict]) -> None:
    """无站单元借用直接下游（取第一个已率定的子单元）。"""
    by_code = {u["code"]: u for u in units_out}
    for sb in sbs:
        u = by_code.get(sb["code"])
        if not u or u.get("calibrated"):
            continue
        donor = next((by_code[o["code"]] for o in sbs if o.get("parent") == sb["code"]
                      and by_code.get(o["code"], {}).get("calibrated")), None)
        if donor:
            u["params"] = dict(donor["params"])
            u["borrowed_from"] = donor["code"]
            if u.get("status") == "not_run":
                u["note"] = f"任务被终止，该单元未参与率定；参数暂借 {donor['code']}"
            else:
                u["note"] = f"无出口实测，参数借用 {donor['code']}（直接下游已率定单元）"


# ---------------------------------------------------------------- 链式率定（P3）
def _run_chain(sbs, contexts, cfg, pack, emit, stop_flag) -> dict:
    """按排水序自上而下逐单元率定；上游出流演算后作已知入流。"""
    period = cfg.get("period") or None
    split = min(max(float(cfg.get("split") or DEFAULT_SPLIT), 0.3), 0.95)
    max_evals = int(cfg.get("max_evals") or DEFAULT_MAX_EVALS)
    seed = int(cfg.get("seed") or 0)
    tol = float(cfg.get("tol") or DEFAULT_TOL)
    lock = cfg.get("lock") or {}

    qs: dict[str, np.ndarray] = {}
    units_out: list[dict] = []
    stopped = False
    t_all = time.perf_counter()

    for sb in sbs:
        code = sb["code"]
        ctx = contexts[code]
        if stop_flag and stop_flag():
            stopped = True
            emit({"phase": "stopped", "unit": code})
            break

        base_prm = _base_params(sb, ctx, lock.get(code))

        # 上游来流（已率定单元的出流）
        for up in ctx["upstream"]:
            up["q"] = qs.get(up["code"])

        i0, n = ctx["i0"], ctx["n"]
        i_split = int(i0 + (n - i0) * split)
        obs = ctx.get("obs")
        n_obs = int(np.isfinite(obs[i0:i_split]).sum()) if obs is not None else 0

        if n_obs < 30:
            # 无实测：链上演算用当前基础参数（其误差被下游率定吸收），参数借用下游
            ev = eval_unit(ctx, base_prm)
            qs[code] = ev["q"]
            units_out.append(
                {
                    "code": code, "name": ctx.get("name"), "area_km2": ctx["area_km2"],
                    "params": base_prm, "borrowed_from": None, "calibrated": False,
                    "note": "出口站无实测流量，不参与率定；最终参数借用直接下游已率定单元",
                    "evals": 0, "gens": 0, "elapsed_s": 0.0,
                    "status": "skipped", "convergence": [],
                }
            )
            emit({"phase": "skip", "unit": code, "reason": "无出口实测流量"})
            continue

        keys, bounds = free_spec_for(ctx, lock.get(code))

        if not keys:
            # 全部参数被锁定：没有可优化的维度，直接用锁定值演算（不空跑 SCE-UA）
            ev = eval_unit(ctx, base_prm)
            qs[code] = ev["q"]
            units_out.append(
                {
                    "code": code, "name": ctx.get("name"), "area_km2": ctx["area_km2"],
                    "params": base_prm, "borrowed_from": None, "calibrated": False,
                    "note": "该单元全部参数被锁定，未做优化（沿用表内取值）",
                    "evals": 0, "gens": 0, "elapsed_s": 0.0,
                    "status": "locked", "convergence": [],
                }
            )
            emit({"phase": "skip", "unit": code, "reason": "全部参数被锁定"})
            continue

        def F(xvec, _ctx=ctx, _keys=keys, _i0=i0, _i1=i_split):
            prm = {k: float(v) for k, v in zip(_keys, xvec)}
            ev = eval_unit(_ctx, prm)
            f = _station_f(_ctx, ev["q"], _i0, _i1)
            return 1e6 if f is None else f

        t0 = time.perf_counter()
        emit({"phase": "start", "unit": code, "free_keys": keys,
              "calib_steps": int(i_split - i0), "obs_steps": n_obs})

        res = sceua(
            F, bounds,
            max_evals=max_evals, tol=tol, seed=seed + hash(code) % 1000,
            stop_flag=stop_flag,
            on_gen=lambda gen, bf, ev_, bx, _c=code, _k=keys: emit(
                {"phase": "run", "unit": _c, "gen": gen, "best_f": round(bf, 6),
                 "evals": ev_, "best": {k: round(float(v), 4) for k, v in zip(_k, bx)}}
            ),
        )

        best_prm = {k: float(v) for k, v in zip(keys, res["best_x"])}
        full_prm = dict(base_prm)
        full_prm.update(best_prm)
        ev = eval_unit(ctx, full_prm)
        qs[code] = ev["q"]

        units_out.append(
            {
                "code": code, "name": ctx.get("name"), "area_km2": ctx["area_km2"],
                "params": full_prm, "borrowed_from": None, "calibrated": True,
                "outlet_station": ctx.get("outlet_station"),
                "free_keys": keys,
                "objective": round(res["best_f"], 6),
                "evals": res["evals"], "gens": res["gens"], "status": res["status"],
                "elapsed_s": round(time.perf_counter() - t0, 1),
                "convergence": res["history"],
                "metrics_chain": metrics_of(ctx, ev["q"], i_start=i0, i_end=n),
            }
        )
        emit({"phase": "done", "unit": code, "unit_status": res["status"],
              "evals": res["evals"], "gens": res["gens"],
              "objective": round(res["best_f"], 6),
              "unit_params": {k: round(v, 4) for k, v in full_prm.items()}})

    return _assemble_result(sbs, contexts, cfg, pack, units_out, stopped, t_all, mode="chain")


# ---------------------------------------------------------------- 联合率定（P5）
def _build_slots(sbs, contexts, lock: dict, share: dict) -> tuple[list[dict], dict]:
    """联合模式的决策变量表。

    返回 ``(slots, unit_slots)``：
    - slots: [{key, units, bounds, shared}]，units 为该变量影响的单元（共享组 ≥2 个单元）；
    - unit_slots: {code: {参数: 变量值来源说明}}，仅供展示（free_keys 用）。

    共享语义：``share: {参数: [单元...]}`` 中该参数在组内只占一个决策变量；组内某单元
    若锁定了该参数则仍用锁定值（不进组）。组内自由单元 < 2 时退化为各单元独立变量。
    """
    # 每单元的自由参数
    unit_keys: dict[str, list[str]] = {}
    for sb in sbs:
        code = sb["code"]
        unit_keys[code] = free_spec_for(contexts[code], lock.get(code))[0]

    # 共享组：key -> [自由单元]
    groups: dict[str, list[str]] = {}
    for k, codes in (share or {}).items():
        if k not in PARAM_KEYS:
            continue
        members = [c for c in (codes or []) if c in unit_keys and k in unit_keys[c]]
        if len(members) >= 2:
            groups[k] = sorted(members, key=lambda c: next(
                (s.get("order_index") or 0) for s in sbs if s["code"] == c))

    slots: list[dict] = []
    in_group: set[tuple[str, str]] = set()      # (code, key) 已归入共享组
    for k, members in groups.items():
        slots.append(
            {
                "key": k, "units": members, "shared": True,
                "label": f"{k}({'/'.join(members)})",
                "bounds": (float(PARAM_SPEC[k]["min"]), float(PARAM_SPEC[k]["max"])),
            }
        )
        for c in members:
            in_group.add((c, k))

    for sb in sbs:
        code = sb["code"]
        for k in unit_keys[code]:
            if (code, k) in in_group:
                continue
            slots.append(
                {
                    "key": k, "units": [code], "shared": False,
                    "label": f"{code}.{k}",
                    "bounds": (float(PARAM_SPEC[k]["min"]), float(PARAM_SPEC[k]["max"])),
                }
            )

    return slots, {"unit_keys": unit_keys, "groups": groups}


def _run_joint(sbs, contexts, cfg, pack, emit, stop_flag) -> dict:
    """所有实测站同时优化；目标 = 各站目标函数加权平均；支持参数共享分组。"""
    period = cfg.get("period") or None
    split = min(max(float(cfg.get("split") or DEFAULT_SPLIT), 0.3), 0.95)
    max_evals = int(cfg.get("max_evals") or DEFAULT_MAX_EVALS)
    seed = int(cfg.get("seed") or 0)
    tol = float(cfg.get("tol") or DEFAULT_TOL)
    lock = cfg.get("lock") or {}
    weights_in = cfg.get("weights") or {}

    # ---- 率定期切分点（各单元时段轴一致，取首单元）
    ref = contexts[sbs[0]["code"]]
    i0, n = ref["i0"], ref["n"]
    i_split = int(i0 + (n - i0) * split)

    # ---- 决策变量（含共享合并）
    slots, slot_meta = _build_slots(sbs, contexts, lock, cfg.get("share") or {})
    if not slots:
        return {"ok": False, "error": "没有可优化的自由参数（全部被锁定？）"}
    bounds = [s["bounds"] for s in slots]

    # ---- 参与目标的站（有实测且率限期有效点 ≥30）
    stations: list[dict] = []
    for sb in sbs:
        code = sb["code"]
        ctx = contexts[code]
        if ctx.get("obs") is None:
            continue
        o = np.asarray(ctx["obs"])[i0:i_split]
        if int(np.isfinite(o).sum()) < 30:
            continue
        stations.append({"code": code, "ctx": ctx, "weight": float(weights_in.get(code, 1.0))})
    if not stations:
        return {"ok": False, "error": "联合率定需要至少一个有出口实测的单元"}

    base_prms = {sb["code"]: _base_params(sb, contexts[sb["code"]], lock.get(sb["code"]))
                 for sb in sbs}
    unit_keys = slot_meta["unit_keys"]
    groups = slot_meta["groups"]

    def decode(xvec) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for v, s in zip(xvec, slots):
            val = float(v)
            for c in s["units"]:
                out.setdefault(c, {})[s["key"]] = val
        return out

    def chain_eval(prm_by_unit: dict[str, dict]) -> dict[str, np.ndarray]:
        """按排水序演算全链（上游出流填入下游 upstream 后评估）。"""
        qs: dict[str, np.ndarray] = {}
        for sb in sbs:
            code = sb["code"]
            ctx = contexts[code]
            for up in ctx["upstream"]:
                up["q"] = qs.get(up["code"])
            qs[code] = eval_unit(ctx, {**base_prms[code], **prm_by_unit.get(code, {})})["q"]
        return qs

    wsum = sum(s["weight"] for s in stations)

    def F(xvec) -> float:
        qs = chain_eval(decode(xvec))
        total = 0.0
        for s in stations:
            f = _station_f(s["ctx"], qs[s["code"]], i0, i_split)
            if f is None:
                return 1e6
            total += s["weight"] * f
        return total / wsum if wsum > 0 else 1e6

    # ---- SCE-UA 维度提示：p 增大时种群 s*m = p*(2p+1) 增长很快，评估次数需跟上
    p = len(slots)
    npts = p * (2 * p + 1)
    suggest = 3 * npts
    warn_dim = (
        f"联合模式 {p} 维（共享组 {len(groups)} 个："
        + "、".join(f"{k}←{'/'.join(m)}" for k, m in groups.items()) + "）"
        if groups else f"联合模式 {p} 维"
    )
    if max_evals < suggest:
        warn_dim += f"；建议 max_evals ≥ {suggest}（当前 {max_evals}，可能收敛不足）"

    emit({"phase": "start", "unit": "JOINT", "free_keys": [s["label"] for s in slots],
          "calib_steps": int(i_split - i0),
          "obs_steps": int(np.isfinite(np.asarray(stations[0]["ctx"]["obs"])[i0:i_split]).sum())})

    t0 = time.perf_counter()
    res = sceua(
        F, bounds,
        max_evals=max_evals, tol=tol, seed=seed + 7,
        stop_flag=stop_flag,
        on_gen=lambda gen, bf, ev_, bx: emit(
            {"phase": "run", "unit": "JOINT", "gen": gen, "best_f": round(bf, 6),
             "evals": ev_, "evals_per": round(ev_ / max(gen, 1), 1),
             "best": {s["label"]: round(float(v), 4) for s, v in zip(slots, bx)}}
        ),
    )
    if stop_flag and stop_flag():
        res = {**res, "status": "stopped"}

    # ---- 用最优解演算全链，得到各单元出流
    best_prm_by_unit = decode(res["best_x"])
    qs = chain_eval(best_prm_by_unit)

    units_out: list[dict] = []
    station_f: dict[str, float] = {}
    for s in stations:
        f = _station_f(s["ctx"], qs[s["code"]], i0, i_split)
        if f is not None:
            station_f[s["code"]] = f

    for sb in sbs:
        code = sb["code"]
        ctx = contexts[code]
        own = dict(base_prms[code])
        own.update(best_prm_by_unit.get(code) or {})
        own_keys = sorted((best_prm_by_unit.get(code) or {}).keys())
        shared_keys = sorted(k for k, m in groups.items() if code in m)
        f = station_f.get(code)
        has_station = any(s["code"] == code for s in stations)
        units_out.append(
            {
                "code": code, "name": ctx.get("name"), "area_km2": ctx["area_km2"],
                "params": own, "borrowed_from": None,
                "calibrated": bool(own_keys or shared_keys),
                "outlet_station": ctx.get("outlet_station"),
                "free_keys": own_keys,
                "shared_keys": shared_keys,
                "objective": round(f, 6) if f is not None else None,
                "evals": res["evals"], "gens": res["gens"], "status": "joint",
                "elapsed_s": round(time.perf_counter() - t0, 1),
                "convergence": [],
                "note": (
                    "联合率定：与其他单元同时优化"
                    + (f"（{','.join(shared_keys)} 为共享参数）" if shared_keys else "")
                    if (own_keys or shared_keys) else "无自由参数（全部锁定）"
                ),
                "metrics_chain": metrics_of(ctx, qs[code], i_start=i0, i_end=n)
                if has_station else None,
            }
        )
    emit({"phase": "done", "unit": "JOINT", "unit_status": res["status"],
          "evals": res["evals"], "gens": res["gens"],
          "objective": round(res["best_f"], 6),
          "unit_params": {c: {k: round(v, 4) for k, v in (best_prm_by_unit.get(c) or {}).items()}
                          for c in best_prm_by_unit}})

    out = _assemble_result(sbs, contexts, cfg, pack, units_out, res["status"] == "stopped",
                           t0, mode="joint")
    if out.get("ok"):
        out["joint"] = True
        out["joint_convergence"] = res["history"]
        out["slots"] = [{"key": s["key"], "units": s["units"], "shared": s["shared"],
                         "label": s["label"]} for s in slots]
        out["objective"] = round(res["best_f"], 6)
        out["warnings"] = [*(out.get("warnings") or []), warn_dim]
    return out


# ---------------------------------------------------------------- 结果装配（两模式共用）
def _assemble_result(sbs, contexts, cfg, pack, units_out, stopped, t0, *, mode: str) -> dict:
    """借用补齐 → 整体复算 → 分期指标 → 汇总返回。"""
    period = cfg.get("period") or None
    split = min(max(float(cfg.get("split") or DEFAULT_SPLIT), 0.3), 0.95)
    subbasins_doc, manifest, series_loader = pack["doc"], pack["manifest"], pack["loader"]

    _not_run_placeholders(sbs, contexts, units_out)
    _borrow_for_skipped(sbs, units_out)

    final_params = {u["code"]: u["params"] for u in units_out}
    fin = _finalize_basin(subbasins_doc, manifest, series_loader, contexts, final_params,
                          period=period, split=split, sbs=sbs)
    if not fin.get("ok"):
        return {"ok": False, "error": fin.get("error") or "最终复算失败", "units": units_out}

    return {
        "ok": True,
        "mode": mode,
        "status": "stopped" if stopped else "done",
        "units": units_out,
        "metrics": fin["metrics"],
        "simulation": fin["simulation"],
        "split": split,
        "final_params": final_params,
        "elapsed_s": round(time.perf_counter() - t0, 1),
        "warnings": fin["simulation"].get("warnings") or [],
    }
