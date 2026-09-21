"""SCE-UA 链式率定（P3）。

对应技术方案 §6.3 / §6.4：

- **链式策略**：按排水序（order_index）自上而下逐单元率定；率定某单元时，其直接上游
  参数已定，上游出流经马斯京根演算后作为该单元的已知入流——每步只优化一个单元的
  自由参数，避免高维联合优化；
- **目标函数**：``F = (1 − NSE) + 0.3·|PBIAS|/100 + 0.1·RMSE/std(obs)``，在率定期上
  计算（序列按 ``split`` 比例切成率定期 / 验证期，验证期只评估不再调参）；
- **算法**：SCE-UA（Duan et al. 1992）。复合体数 = 自由参数数、每复合体 2p+1 点，
  子复合体 p+1 点，梯形概率选择，反射 / 收缩 / 随机变异三步替换最差点；
- **性能**：目标评估走 :func:`~app.core.model.simulate.eval_unit` 热路径（预计算
  面雨量 / 蒸发 / 河道 KE，单次 ≈ 5 ms），3000 次评估 ≈ 15 s/单元，全链 3 单元 < 1 分钟。

无出口站的单元不参与率定：链上演算先用默认参数（其误差被下游率定吸收），最终参数
**借用直接下游已率定单元**，并用最终参数集整体复算一遍作为权威结果（见 runner）。
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


# ---------------------------------------------------------------- 链式率定
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


def run_calibration(
    subbasins_doc: dict,
    manifest: dict,
    series_loader,
    *,
    config: dict | None = None,
    on_progress=None,
    stop_flag=None,
) -> dict:
    """链式率定入口。返回率定结果（含最终参数集与权威的全流域复算结果）。

    - config: {period{start,end,warmup_days}, split, max_evals, seed, tol,
               lock: {code: {参数: 值}}（锁定不率定）}
    - on_progress(payload): 每代回调（进度落盘 / 前端轮询）
    - stop_flag(): 返回 True 则在当前代结束后终止
    """
    cfg = config or {}
    period = cfg.get("period") or None
    split = min(max(float(cfg.get("split") or DEFAULT_SPLIT), 0.3), 0.95)
    max_evals = int(cfg.get("max_evals") or DEFAULT_MAX_EVALS)
    seed = int(cfg.get("seed") or 0)
    tol = float(cfg.get("tol") or DEFAULT_TOL)
    lock = cfg.get("lock") or {}

    sbs = sorted(subbasins_doc.get("subbasins", []), key=lambda s: (s.get("order_index") or 0))
    if not sbs:
        return {"ok": False, "error": "尚未划分子流域"}

    # ---- 逐单元预计算上下文（面雨量/蒸发/河道 KE/实测，一次性 ~0.5s/单元）
    contexts: dict[str, dict] = {}
    for sb in sbs:
        c = unit_context(subbasins_doc, manifest, series_loader, sb["code"], period=period)
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

    # ---- 链式率定（自上而下）
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

        reach_km = float(sb.get("river_length_km") or 0.0)
        base_prm = default_params(reach_km, ctx["dt_h"])
        for k, v in (lock.get(code) or {}).items():
            if k in PARAM_KEYS:
                base_prm[k] = float(v)

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

        o_cal = obs[i0:i_split]
        std_obs = float(np.std(o_cal[np.isfinite(o_cal)])) if len(o_cal) else 0.0
        keys, bounds = free_spec_for(ctx, lock.get(code))

        def F(xvec, _ctx=ctx, _keys=keys, _i0=i0, _i1=i_split, _std=std_obs):
            prm = {k: float(v) for k, v in zip(_keys, xvec)}
            ev = eval_unit(_ctx, prm)
            met = metrics_of(_ctx, ev["q"], i_start=_i0, i_end=_i1)
            return objective_value(met, _std) if met else 1e6

        t0 = time.perf_counter()
        emit({"phase": "start", "unit": code, "free_keys": keys,
              "calib_steps": int(i_split - i0), "obs_steps": n_obs})

        res = sceua(
            F, bounds,
            max_evals=max_evals, tol=tol, seed=seed + hash(code) % 1000,
            stop_flag=stop_flag,
            on_gen=lambda gen, bf, ev_, bx, _c=code: emit(
                {"phase": "run", "unit": _c, "gen": gen, "best_f": round(bf, 6),
                 "evals": ev_, "best": {k: round(float(v), 4) for k, v in zip(keys, bx)}}
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

    # ---- 提前终止时，未跑到的单元补占位记录（前端参数表仍完整可读）
    seen = {u["code"] for u in units_out}
    for sb in sbs:
        code = sb["code"]
        if code in seen:
            continue
        units_out.append(
            {
                "code": code, "name": sb.get("name"), "area_km2": sb.get("area_km2"),
                "params": default_params(float(sb.get("river_length_km") or 0.0),
                                         contexts[code]["dt_h"]),
                "borrowed_from": None, "calibrated": False, "status": "not_run",
                "note": "任务被终止，该单元未参与率定（沿用默认参数）",
                "evals": 0, "gens": 0, "elapsed_s": 0.0, "convergence": [],
            }
        )
    order_of = {sb["code"]: (sb.get("order_index") or 0) for sb in sbs}
    units_out.sort(key=lambda u: order_of.get(u["code"], 0))

    # ---- 无站单元借用直接下游（取第一个已率定的子单元）
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

    # ---- 用最终参数集整体复算一遍：结果与「模型模拟」完全同源，避免两套实现漂移
    final_params = {u["code"]: u["params"] for u in units_out}
    sim = simulate_basin(subbasins_doc, manifest, series_loader, params=final_params,
                         period=period, return_raw=True)
    if not sim.get("ok"):
        return {"ok": False, "error": sim.get("error") or "最终复算失败", "units": units_out}

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
    return {
        "ok": True,
        "status": "stopped" if stopped else "done",
        "units": units_out,
        "metrics": metrics,
        "simulation": sim,
        "split": split,
        "final_params": final_params,
        "elapsed_s": round(time.perf_counter() - t_all, 1),
        "warnings": sim.get("warnings") or [],
    }
