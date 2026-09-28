# -*- coding: utf-8 -*-
"""混合模型预研：概念性模型 + 数据驱动残差校正（numpy 实现，无 torch/sklearn 依赖）。

问题：概念性模型（新安江/Tank/HBV）结构不匹配时，残差里既有系统性偏差也有噪声。
     一个只在**过去信息**上训练的数据驱动模型，能把系统性那部分学回来多少？

协议（严格时序三段，杜绝泄漏）：
  A [0, 45%)     概念性模型率定（run_calibration，内部再 70/30 分期）
  B [45%, 70%)   残差模型训练（用 A 段参数跑出的**样本外**残差）
  C [70%, 100%)  测试（两个模型都没见过；概念性模型对 C 也是样本外）

场景：
  S1 xaj / tank  结构不匹配（基模型 ≠ 数据生成模型 HBV 雪版）→ 残差含系统结构
  S2 hbv         负对照（基模型 = 数据生成模型）→ 残差应近似纯噪声，增益应 ≈ 0

残差模型（sqrt 流量空间，压制峰值异方差）：
  r = sqrt(obs) − sqrt(sim)；特征全部因果（只用 t 及以前的已知量）：
  面雨量 lag0..3 / 气温 lag0..1 / 蒸发 / 基模型模拟流 sqrt 空间 lag0..2 /
  历史残差 lag1..3 / 日序 sin·cos；两个拟合器做消融：岭回归（闭式解）vs MLP（1 隐层 Adam）

产出：_hybrid_residual_result.json（原始结果）+ docs/hybrid-residual-study.md（结论报告）
"""
import json
import sys
import time
from datetime import datetime

import numpy as np

sys.path.insert(0, ".")

from app import storage as st
from app.core.model.calibrate import run_calibration
from app.core.model.metrics import all_metrics
from app.core.model.simulate import simulate_basin, unit_context
from app.core.timeseries import csv_to_records

PID = sys.argv[1] if len(sys.argv) > 1 else "p_dee39fceeab6"
MAX_EVALS = int(sys.argv[2]) if len(sys.argv) > 2 else 800
SPLIT_A, SPLIT_B = 0.45, 0.25            # C = 1 − A − B = 0.30
SCENARIOS = [("xaj", "S1 结构不匹配·新安江"), ("tank", "S1 结构不匹配·Tank"),
             ("hbv", "S2 负对照·HBV（数据生成模型）")]
RIDGE_LAMBDAS = [1e-3, 1e-2, 1e-1, 1.0, 10.0, 100.0]


def loader(kind, key):
    p = st.ts_series_path(PID, kind, key)
    return csv_to_records(p.read_text(encoding="utf-8")) if p.exists() else []


def as_dt(x):
    """时间轴元素可能是 datetime 或 ISO 字符串，统一成 datetime。"""
    if isinstance(x, str):
        return datetime.fromisoformat(x)
    return x


# ---------------------------------------------------------------- 特征工程
FEAT_LAGS = (0, 1, 2, 3)                 # 面雨量 lag
MAX_LAG = 3                              # 历史残差最大 lag


def build_features(p, e, t, sim, obs, times):
    """构造因果特征矩阵；返回 (X, y, mask, names)。y = sqrt(obs) − sqrt(sim)。"""
    n = len(sim)
    z_sim = np.sqrt(np.maximum(sim, 0.0))
    z_obs = np.sqrt(np.maximum(np.where(np.isfinite(obs), obs, np.nan), 0.0))
    r = z_obs - z_sim
    doy = np.array([as_dt(dt).timetuple().tm_yday for dt in times], dtype=float)

    cols, names = [], []
    for L in FEAT_LAGS:                         # 面雨量（mm）
        cols.append(np.roll(p, L)); names.append(f"p_lag{L}")
    for L in (0, 1):                            # 气温
        if t is not None:
            cols.append(np.roll(t, L)); names.append(f"t_lag{L}")
    cols.append(e); names.append("e")           # 蒸发
    for L in (0, 1, 2):                         # 基模型模拟流（sqrt 空间，因果可得）
        cols.append(np.roll(z_sim, L)); names.append(f"zsim_lag{L}")
    for L in (1, 2, 3):                         # 历史残差（需 obs 已知）
        cols.append(np.roll(r, L)); names.append(f"res_lag{L}")
    cols.append(np.sin(2 * np.pi * doy / 365.25)); names.append("doy_sin")
    cols.append(np.cos(2 * np.pi * doy / 365.25)); names.append("doy_cos")

    X = np.column_stack(cols)
    # 有效行：跳过开头（lag 不足）+ 残差 lag 依赖的 obs 必须有限
    ok = np.ones(n, dtype=bool)
    ok[: max(max(FEAT_LAGS), MAX_LAG) + 1] = False
    ok &= np.isfinite(z_sim) & np.isfinite(z_obs)
    for L in (1, 2, 3):
        shifted = np.ones(n, dtype=bool)
        shifted[L:] = np.isfinite(r[:-L])
        shifted[:L] = False
        ok &= shifted
    for j in range(X.shape[1]):
        ok &= np.isfinite(X[:, j])
    return X, r, ok, names


def standardize(Xtr, Xte):
    mu = Xtr.mean(axis=0)
    sd = Xtr.std(axis=0)
    sd[sd < 1e-9] = 1.0
    return (Xtr - mu) / sd, (Xte - mu) / sd


def fit_ridge(X, y, Xv, yv):
    """闭式解岭回归；λ 在 B 段内部留出的尾部验证集上选（不碰 C）。"""
    best, best_sse = None, np.inf
    Xc = np.column_stack([np.ones(len(X)), X])
    Xcv = np.column_stack([np.ones(len(Xv)), Xv])
    for lam in RIDGE_LAMBDAS:
        A = Xc.T @ Xc + lam * np.eye(Xc.shape[1])
        A[0, 0] -= lam                      # 不惩罚截距
        w = np.linalg.solve(A, Xc.T @ y)
        sse = float(np.sum((Xcv @ w - yv) ** 2))
        if sse < best_sse:
            best, best_sse = w, sse
    return best, best_sse


def fit_mlp(X, y, Xv, yv, hidden=8, epochs=1200, lr=0.02, l2=1e-4, seed=2026):
    """单隐层 tanh MLP，Adam（手写反向传播）；按 B 段尾部验证集早停。"""
    rng = np.random.default_rng(seed)
    ym, ys = float(y.mean()), float(y.std() or 1.0)
    Y = (y - ym) / ys
    Yv = (yv - ym) / ys
    d, h = X.shape[1], hidden
    W1 = rng.normal(0, 1 / np.sqrt(d), (d, h))
    b1 = np.zeros(h)
    W2 = rng.normal(0, 1 / np.sqrt(h), (h, 1))
    b2 = np.zeros(1)
    ps = [W1, b1, W2, b2]
    ms = [np.zeros_like(q) for q in ps]
    vs = [np.zeros_like(q) for q in ps]
    b1a, b2a, eps = 0.9, 0.999, 1e-8
    best, best_v = [q.copy() for q in ps], np.inf

    def fwd(pp, Xa):
        H = np.tanh(Xa @ pp[0] + pp[1])
        return H, (H @ pp[2] + pp[3]).ravel()

    for ep in range(1, epochs + 1):
        H, out = fwd(ps, X)
        g = 2.0 * (out - Y) / len(X)                    # dL/dout
        gW2 = H.T @ g[:, None] + l2 * ps[2]
        gb2 = np.array([g.sum()])
        gH = (g[:, None] @ ps[2].T) * (1 - H ** 2)      # tanh'
        gW1 = X.T @ gH + l2 * ps[0]
        gb1 = gH.sum(axis=0)
        for i, gr in enumerate([gW1, gb1, gW2, gb2]):
            ms[i] = b1a * ms[i] + (1 - b1a) * gr
            vs[i] = b2a * vs[i] + (1 - b2a) * gr ** 2
            mh = ms[i] / (1 - b1a ** ep)
            vh = vs[i] / (1 - b2a ** ep)
            ps[i] = ps[i] - lr * mh / (np.sqrt(vh) + eps)
        if ep % 10 == 0:
            _, ov = fwd(ps, Xv)
            v = float(np.mean((ov - Yv) ** 2))
            if v < best_v:
                best_v, best = v, [q.copy() for q in ps]
    W1, b1, W2, b2 = best
    return {"W1": W1, "b1": b1, "W2": W2, "b2": b2, "ym": ym, "ys": ys, "val_mse": best_v}


def predict_mlp(model, X):
    H = np.tanh(X @ model["W1"] + model["b1"])
    return (H @ model["W2"] + model["b2"]).ravel() * model["ys"] + model["ym"]


# ---------------------------------------------------------------- 指标
def flow_metrics(obs, sim):
    m = all_metrics(obs, sim, dt_label="24h")
    pe = m.get("peak_error") or {}
    ev = pe.get("events") or []
    pcts = [((x["sim_peak"] - x["obs_peak"]) / x["obs_peak"] * 100.0)
            for x in ev if x.get("obs_peak")]
    return {
        "nse": m.get("nse"), "kge": m.get("kge"), "rmse": m.get("rmse"), "pbias": m.get("pbias"),
        "peak_bias_pct": round(float(np.median(pcts)), 1) if pcts else None,
        "peak_shift_steps": pe.get("median_steps"),
    }


def fmt(v, nd=3):
    return "—" if v is None else f"{float(v):.{nd}f}"


print("=" * 84)
print(f"混合模型预研（残差学习）｜ pid={PID}  预算={MAX_EVALS} evals/单元")
print(f"协议：A 率定 [0,{SPLIT_A:.0%})  B 残差训练 [{SPLIT_A:.0%},{SPLIT_A + SPLIT_B:.0%})  C 测试 [{SPLIT_A + SPLIT_B:.0%},100%)")

sub = st.read_subbasins(PID)
man = st.read_ts_manifest(PID)
codes = [s["code"] for s in sorted(sub.get("subbasins") or [], key=lambda x: x.get("order_index") or 0)]
print(f"单元：{codes}")

# 时间轴（以末端单元上下文为准）
ctx_last = unit_context(sub, man, loader, codes[-1], period=None, model="xaj")
times = [as_dt(t) for t in ctx_last["times"]]
n = len(times)
iA, iB = int(n * SPLIT_A), int(n * (SPLIT_A + SPLIT_B))
pA = {"start": times[0].strftime("%Y-%m-%d %H:%M"),
      "end": times[iA - 1].strftime("%Y-%m-%d %H:%M")}  # _prepare_inputs 只认该格式
print(f"序列 {n} 步（{times[0]:%Y-%m-%d} ~ {times[-1]:%Y-%m-%d}）")
print(f"  A {iA} 步（~{times[iA - 1]:%Y-%m-%d}）| B {iB - iA} 步（~{times[iB - 1]:%Y-%m-%d}）| C {n - iB} 步")

out = {
    "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    "pid": PID, "max_evals": MAX_EVALS,
    "protocol": {"n": n, "A": [0, iA], "B": [iA, iB], "C": [iB, n],
                 "A_end": times[iA - 1].strftime("%Y-%m-%d"),
                 "B_end": times[iB - 1].strftime("%Y-%m-%d"),
                 "obs_source": "项目演示流量（HBV 雪版 OSSE）",
                 "space": "sqrt(流量)"},
    "scenarios": {},
}

for mk, label in SCENARIOS:
    print("=" * 84)
    print(f"{label}（基模型 {mk}）")
    t0 = time.perf_counter()
    res = run_calibration(sub, man, loader,
                          config={"model": mk, "max_evals": MAX_EVALS, "seed": 7,
                                  "split": 0.7, "period": pA})
    assert res.get("ok"), (mk, res.get("error"))
    prm = res.get("final_params") or {}
    a_nse = [(m["code"], (m.get("calib") or {}).get("nse")) for m in res.get("metrics") or []]
    print(f"  A 段率定 {time.perf_counter() - t0:.1f}s  A 段率定 NSE："
          + " ".join(f"{c}={fmt(v)}" for c, v in a_nse))

    # 全序列基模型出流（全分辨率）
    sim_all = simulate_basin(sub, man, loader, params=prm, model=mk, return_raw=True)
    raw = sim_all["_raw"]
    q_by_code = {u["code"]: np.asarray(u["q"], dtype=float) for u in raw["units"]}
    obs_by_code = {u["code"]: (np.asarray(u["obs"], dtype=float) if u["obs"] is not None else None)
                   for u in raw["units"]}

    units_out, d_nse = {}, []
    for code in codes:
        obs = obs_by_code.get(code)
        if obs is None or not np.isfinite(obs).any():
            continue
        ctx = unit_context(sub, man, loader, code, period=None, model=mk)
        sim = q_by_code[code]
        X, r, ok, names = build_features(ctx["p"], ctx["e"], ctx.get("t"), sim, obs, ctx["times"])
        idx_tr = np.where(ok)[0]
        tr = idx_tr[idx_tr < iB]
        te = np.where(ok & (np.arange(n) >= iB))[0]
        if len(tr) < 60 or len(te) < 30:
            print(f"    [{code}] 样本不足，跳过（train {len(tr)} / test {len(te)}）")
            continue

        # B 段内部再留尾部 20% 作为残差模型的验证（选 λ / 早停），不碰 C
        nv = max(20, int(len(tr) * 0.2))
        fit, val = tr[:-nv], tr[-nv:]
        Xf, Xv, Xt = X[fit], X[val], X[te]
        Xf_s, Xv_s = standardize(Xf, Xv)
        Xt_s = (Xt - Xf.mean(axis=0)) / np.where(Xf.std(axis=0) < 1e-9, 1.0, Xf.std(axis=0))

        w_ridge, _ = fit_ridge(Xf_s, r[fit], Xv_s, r[val])
        mlp = fit_mlp(Xf_s, r[fit], Xv_s, r[val])

        # 置换检验（placebo）：把训练/验证目标打乱后同样拟合 → 增益应坍塌到 ≈0。
        # 这是排除「特征泄漏 / 实现漏洞」的关键对照，而不是可选项。
        rng = np.random.default_rng(11)
        w_pl, _ = fit_ridge(Xf_s, r[fit][rng.permutation(len(fit))],
                            Xv_s, r[val][rng.permutation(len(val))])

        z_sim_te = np.sqrt(np.maximum(sim[te], 0.0))
        base_te = np.maximum(sim[te], 0.0)
        Xt_c = np.column_stack([np.ones(len(Xt_s)), Xt_s])
        ridge_te = (z_sim_te + Xt_c @ w_ridge) ** 2
        placebo_te = (z_sim_te + Xt_c @ w_pl) ** 2
        mlp_te = (z_sim_te + predict_mlp(mlp, Xt_s)) ** 2
        obs_te = obs[te]

        m_base = flow_metrics(obs_te, base_te)
        m_ridge = flow_metrics(obs_te, ridge_te)
        m_mlp = flow_metrics(obs_te, mlp_te)
        m_pl = flow_metrics(obs_te, placebo_te)
        d_nse.append(m_ridge["nse"] - m_base["nse"])
        units_out[code] = {
            "n_train": int(len(fit)), "n_val": int(len(val)), "n_test": int(len(te)),
            "n_features": len(names),
            "base": m_base, "ridge": m_ridge, "mlp": m_mlp, "placebo": m_pl,
            "delta_nse_ridge": round(m_ridge["nse"] - m_base["nse"], 4),
            "delta_nse_mlp": round(m_mlp["nse"] - m_base["nse"], 4),
            "delta_nse_placebo": round(m_pl["nse"] - m_base["nse"], 4),
            "mlp_val_mse": round(mlp["val_mse"], 5),
        }
        print(f"    [{code}] 测试段 NSE  基模型 {fmt(m_base['nse'])} → 岭 {fmt(m_ridge['nse'])}"
              f" → MLP {fmt(m_mlp['nse'])} → 置换 {fmt(m_pl['nse'])}"
              f"  (Δ 岭 {m_ridge['nse'] - m_base['nse']:+.4f} / MLP {m_mlp['nse'] - m_base['nse']:+.4f}"
              f" / 置换 {m_pl['nse'] - m_base['nse']:+.4f})")

    import statistics as _st
    out["scenarios"][mk] = {
        "label": label, "model": mk, "elapsed_s": round(time.perf_counter() - t0, 1),
        "a_calib_nse": {c: v for c, v in a_nse},
        "units": units_out,
        "mean_delta_nse": {
            "ridge": round(float(_st.mean([u["delta_nse_ridge"] for u in units_out.values()])), 4)
            if units_out else None,
            "mlp": round(float(_st.mean([u["delta_nse_mlp"] for u in units_out.values()])), 4)
            if units_out else None,
            "placebo": round(float(_st.mean([u["delta_nse_placebo"] for u in units_out.values()])), 4)
            if units_out else None,
        },
        "feature_names": names if units_out else [],
    }
    print(f"  → 平均 ΔNSE：岭 {out['scenarios'][mk]['mean_delta_nse']['ridge']} / "
          f"MLP {out['scenarios'][mk]['mean_delta_nse']['mlp']} / "
          f"置换 {out['scenarios'][mk]['mean_delta_nse']['placebo']}")

with open("_hybrid_residual_result.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print("=" * 84)
print("原始结果已写 _hybrid_residual_result.json（报告由 _render_hybrid.py 生成）")
