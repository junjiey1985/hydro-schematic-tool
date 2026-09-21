"""模拟评价指标：NSE / R² / RMSE / PBIAS / 峰现时间误差 / KGE。

所有函数接受等长的实测 obs 与模拟 sim 序列（自动剔除 NaN 对）。
"""
from __future__ import annotations

import math

import numpy as np


def _pair(obs, sim):
    o = np.asarray(obs, dtype=float)
    s = np.asarray(sim, dtype=float)
    mask = np.isfinite(o) & np.isfinite(s)
    return o[mask], s[mask]


def nse(obs, sim) -> float | None:
    o, s = _pair(obs, sim)
    if len(o) < 3:
        return None
    denom = float(np.sum((o - o.mean()) ** 2))
    if denom <= 0:
        return None
    return float(1.0 - np.sum((s - o) ** 2) / denom)


def r2(obs, sim) -> float | None:
    o, s = _pair(obs, sim)
    if len(o) < 3 or o.std() == 0 or s.std() == 0:
        return None
    return float(np.corrcoef(o, s)[0, 1] ** 2)


def kge(obs, sim) -> float | None:
    o, s = _pair(obs, sim)
    if len(o) < 3 or o.std() == 0 or s.std() == 0 or o.mean() == 0:
        return None
    r = float(np.corrcoef(o, s)[0, 1])
    alpha = float(s.std() / o.std())
    beta = float(s.mean() / o.mean())
    return float(1.0 - math.sqrt((r - 1) ** 2 + (alpha - 1) ** 2 + (beta - 1) ** 2))


def rmse(obs, sim) -> float | None:
    o, s = _pair(obs, sim)
    if len(o) < 3:
        return None
    return float(math.sqrt(float(np.mean((s - o) ** 2))))


def pbias(obs, sim) -> float | None:
    """总量相对误差（%）：正=模拟偏大。"""
    o, s = _pair(obs, sim)
    if len(o) < 3 or o.sum() <= 0:
        return None
    return float((s.sum() - o.sum()) / o.sum() * 100.0)


def _safe_argmax(a: np.ndarray) -> int | None:
    idx = np.where(np.isfinite(a))[0]
    if len(idx) == 0:
        return None
    return int(idx[int(np.argmax(a[idx]))])


def flood_peaks(x: np.ndarray, top: int = 5, min_gap: int = 10, rel_threshold: float = 1.5) -> list[int]:
    """取序列中最显著的 top 个洪峰，返回其索引。

    洪峰定义为「数值 ≥ rel_threshold × 序列均值」的极值点（按数值降序、相互间隔
    ≥ min_gap 个时段）。阈值可避免在平枯水段把无意义的起伏当成"洪峰"。
    若没有任何点超过阈值，则退化为全局最大值。
    """
    x = np.asarray(x, dtype=float)
    finite = np.isfinite(x)
    if not finite.any():
        return []
    thr = float(x[finite].mean()) * rel_threshold
    order = np.argsort(-np.nan_to_num(x, nan=-1e30))
    picked: list[int] = []
    for i in order:
        i = int(i)
        if not np.isfinite(x[i]):
            break
        if x[i] < thr and picked:
            break
        if all(abs(i - j) >= min_gap for j in picked):
            picked.append(i)
            if len(picked) >= top:
                break
    return picked


def peak_error_steps(
    obs,
    sim,
    dt_label: str = "",
    top: int = 5,
    window: int = 15,
    min_gap: int = 10,
) -> dict | None:
    """多场洪峰的峰现时间误差（模拟 - 实测，单位：时段数）。

    连续多年的长序列若只取全局最大值，"峰现时间误差"会被"哪一年最大"主导（可达数百
    时段），没有实际意义。这里改为对实测最大的 top 场洪峰分别统计：在每场实测峰现
    时刻 ±window 内寻找模拟峰值，取各场误差的**中位数**作为主指标。
    """
    o = np.asarray(obs, dtype=float)
    s = np.asarray(sim, dtype=float)
    if len(o) < 3 or len(s) != len(o):
        return None
    events = []
    for io in flood_peaks(o, top=top, min_gap=min_gap):
        if not np.isfinite(o[io]):
            continue
        lo, hi = max(0, io - window), min(len(s), io + window + 1)
        j = _safe_argmax(s[lo:hi])
        if j is None:
            continue
        ism = lo + j
        events.append(
            {
                "obs_step": io,
                "sim_step": ism,
                "diff_steps": ism - io,
                "obs_peak": round(float(o[io]), 2),
                "sim_peak": round(float(s[ism]), 2),
            }
        )
    if not events:
        return None
    diffs = np.array([e["diff_steps"] for e in events], dtype=float)
    return {
        "median_steps": float(np.median(diffs)),
        "max_abs_steps": float(np.max(np.abs(diffs))),
        "n_events": len(events),
        "window": window,
        "unit": dt_label or "step",
        "events": events,
    }


def all_metrics(obs, sim, dt_label: str = "") -> dict:
    o, s = _pair(obs, sim)
    pe = peak_error_steps(o, s, dt_label)
    return {
        "n": int(len(o)),
        "nse": nse(o, s),
        "r2": r2(o, s),
        "kge": kge(o, s),
        "rmse": rmse(o, s),
        "pbias": pbias(o, s),
        "peak_error": pe,
        "obs_mean": float(o.mean()) if len(o) else None,
        "sim_mean": float(s.mean()) if len(s) else None,
    }
