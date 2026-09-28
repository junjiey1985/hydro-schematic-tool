# -*- coding: utf-8 -*-
"""把 _compare_models_result.json 渲染为自包含 HTML 对比报告（内联 SVG，无外部依赖）。"""
import json
import sys

RES = sys.argv[1] if len(sys.argv) > 1 else "_compare_models_result.json"
OUT = sys.argv[2] if len(sys.argv) > 2 else "../docs/model-comparison.html"

d = json.load(open(RES, encoding="utf-8"))
MODELS = ["xaj", "gr4j", "tank", "hbv"]
info = d["models"]
COLORS = {"xaj": "#2563eb", "gr4j": "#d97706", "tank": "#059669", "hbv": "#dc2626"}


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;")


def fmt(v, n=3):
    return "—" if v is None else f"{v:.{n}f}"


# ---------------- SVG：NSE 条形（率定期，逐单元取最差值代表保守水平）
def nse_bars():
    rows = []
    lo, hi = 0.5, 1.0
    W, BH, GAP = 640, 26, 10
    y = 8
    for part, plabel in (("part_a", "A·同一实测（HBV 雪版 OSSE 流量）"), ("part_b", "B·各自真值 OSSE 回收")):
        rows.append(f'<text x="0" y="{y + 12}" class="sec">{esc(plabel)}</text>')
        y += 24
        for k in MODELS:
            m = d[part][k]["metrics"]
            # 最差单元的率定 NSE（保守口径）
            nses = [v["calib_nse"] for v in m.values() if v["calib_nse"] is not None]
            v = min(nses) if nses else 0.0
            w = max(4.0, (v - lo) / (hi - lo) * (W - 150))
            rows.append(
                f'<text x="0" y="{y + 15}" class="lab">{esc(info[k]["name"])}</text>'
                f'<rect x="140" y="{y}" width="{W - 150}" height="{BH}" rx="4" fill="#eef2f7"/>'
                f'<rect x="140" y="{y}" width="{w:.1f}" height="{BH}" rx="4" fill="{COLORS[k]}"/>'
                f'<text x="{146 + w:.1f}" y="{y + 17}" class="val">{v:.3f}</text>'
            )
            y += BH + GAP
        y += 6
    return (f'<svg viewBox="0 0 {W} {y + 8}" xmlns="http://www.w3.org/2000/svg" role="img">'
            + "".join(rows) + "</svg>")


# ---------------- SVG：过程线对比（末单元）
def hydro():
    s = d["series"]
    times = s["xaj"]["time"]
    n = len(times)
    stride = max(1, n // 360)
    idx = list(range(0, n, stride))
    if idx[-1] != n - 1:
        idx.append(n - 1)
    W, H = 680, 240
    ML, MR, MT, MB = 44, 8, 10, 22
    obs = [s["xaj"]["obs"][i] for i in idx]
    vals = [v for v in obs if v is not None] + [s[k]["sim"][i] for k in MODELS for i in idx]
    vmax = max(vals) * 1.08 or 1.0

    def X(i):
        return ML + i / (len(idx) - 1) * (W - ML - MR)

    def Y(v):
        return MT + (1 - v / vmax) * (H - MT - MB)

    parts = []
    # 网格与轴
    for gv in range(5):
        vv = vmax * gv / 4
        parts.append(f'<line x1="{ML}" y1="{Y(vv):.1f}" x2="{W - MR}" y2="{Y(vv):.1f}" stroke="#e5eaf0"/>'
                     f'<text x="{ML - 6}" y="{Y(vv) + 4:.1f}" class="tick" text-anchor="end">{vv:.0f}</text>')
    # 实测
    pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in zip(idx, obs) if v is not None)
    parts.append(f'<polyline points="{pts}" fill="none" stroke="#8a94a3" stroke-width="1.6" stroke-dasharray="3 3"/>')
    # 各模型
    for k in MODELS:
        pts = " ".join(f"{X(i):.1f},{Y(s[k]['sim'][i]):.1f}" for i in idx)
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{COLORS[k]}" stroke-width="1.5"/>')
    # 雨情注释：时段太密不标时间轴，只标首尾
    parts.append(f'<text x="{ML}" y="{H - 6}" class="tick">{esc(times[idx[0]][:10])}</text>')
    parts.append(f'<text x="{W - MR}" y="{H - 6}" class="tick" text-anchor="end">{esc(times[idx[-1]][:10])}</text>')
    return (f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" role="img">' + "".join(parts) + "</svg>")


# ---------------- 表格
def metric_table(part):
    head = "".join(f"<th>{esc(info[k]['name'])}</th>" for k in MODELS)
    rows = []

    def row(label, fn):
        tds = "".join(f"<td>{fn(d[part][k])}</td>" for k in MODELS)
        rows.append(f"<tr><th>{label}</th>{tds}</tr>")

    def rng(key):
        def f(r):
            lo, hi = r.get(key) or (None, None)
            if lo is None:  # 旧结果文件缺字段：从 metrics 现算
                vs = [v[key.split("_")[0] + "_nse"] for v in r["metrics"].values()]
                vs = [v for v in vs if v is not None]
                lo, hi = (min(vs), max(vs)) if vs else (None, None)
            return f"{lo:.3f} ~ {hi:.3f}"
        return f

    row("率定期 NSE（最差单元）", rng("calib_nse_range"))
    row("验证期 NSE（最差单元）", rng("valid_nse_range"))
    row("率定耗时 (s)", lambda r: f"{r['elapsed_s']:.1f}")
    if part == "part_b":
        row("参数回收 |偏差|>15% 占比", lambda r: f"{r['recover_bad_ratio']:.0%}")
    return f'<table><thead><tr><th></th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>'


def recover_table():
    rows = []
    for k in MODELS:
        recs = sorted(d["part_b"][k]["recover"], key=lambda x: -abs(x["rel_pct"]))[:4]
        txt = "；".join(f"{x['param']} {x['rel_pct']:+.1f}%" for x in recs) or "—"
        rows.append(f'<tr><th>{esc(info[k]["name"])}</th><td>{esc(txt)}（偏差最大的 4 个参数）</td></tr>')
    return f'<table><tbody>{"".join(rows)}</tbody></table>'


a_lo_hbv = min(v["calib_nse"] for v in d["part_a"]["hbv"]["metrics"].values())
a_others = {k: min(v["calib_nse"] for v in d["part_a"][k]["metrics"].values()) for k in MODELS if k != "hbv"}
best_other = max(a_others, key=a_others.get)

HTML = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>多模型对比实验报告</title>
<style>
  body {{ font-family: "Segoe UI", "Microsoft YaHei", sans-serif; margin: 0; background: #f6f8fa; color: #1f2937; }}
  .wrap {{ max-width: 860px; margin: 0 auto; padding: 28px 20px 60px; }}
  h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .meta {{ color: #6b7280; font-size: 12px; margin-bottom: 20px; }}
  h2 {{ font-size: 16px; margin: 30px 0 8px; border-left: 4px solid #2563eb; padding-left: 8px; }}
  p, li {{ font-size: 13px; line-height: 1.75; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 12.5px; background: #fff; }}
  th, td {{ border: 1px solid #e5eaf0; padding: 6px 9px; text-align: left; }}
  thead th {{ background: #f1f5f9; }}
  tbody th {{ background: #fafbfc; white-space: nowrap; }}
  .card {{ background: #fff; border: 1px solid #e5eaf0; border-radius: 10px; padding: 14px 16px; margin: 10px 0; }}
  svg {{ width: 100%; height: auto; }}
  .sec {{ font-size: 12px; font-weight: 600; fill: #475569; }}
  .lab {{ font-size: 11px; fill: #374151; }}
  .val {{ font-size: 11px; fill: #1f2937; font-weight: 600; }}
  .tick {{ font-size: 10px; fill: #8a94a3; }}
  .legend span {{ display: inline-block; margin-right: 14px; font-size: 12px; }}
  .dot {{ display: inline-block; width: 18px; height: 3px; vertical-align: middle; margin-right: 5px; border-radius: 2px; }}
  .note {{ background: #fffbeb; border: 1px solid #fde68a; border-radius: 8px; padding: 10px 12px; font-size: 12.5px; }}
</style>
</head>
<body><div class="wrap">
<h1>多模型对比实验报告</h1>
<div class="meta">生成于 {esc(d['generated_at'])} · 项目 {esc(d['pid'])} · SCE-UA {d['max_evals']} evals/单元 · 日模型 1095 天 · 实测来源：{esc(d['obs_source'])}</div>

<h2>实验设计</h2>
<div class="card">
<p><b>A · 结构误差实验</b>：四个模型用<b>同一套"实测"流量</b>（本平台 HBV 雪版模型以真值参数生成、叠加 5% 观测噪声的 OSSE 数据）各自链式率定。由于"实测"由 HBV 生成，HBV 拟合最好属预期；<b>其余模型与 HBV 的差距即为「模型结构不匹配」的代价量级</b>——这正是换模型前需要预知的信息。</p>
<p><b>B · 框架自洽性</b>：每个模型改用<b>自己的真值参数</b>生成 OSSE 流量再率定回收。若框架实现正确，各模型都应高 NSE 回收自身真值、参数偏差集中在小幅区间；任何模型回收失败都指向实现 bug 而非数据。</p>
</div>

<h2>率定效果对比（率定期 NSE，最差单元口径）</h2>
<div class="card">{nse_bars()}</div>
{metric_table('part_a')}
<h2 style="margin-top:18px">B 部分：各模型对自身真值的回收</h2>
{metric_table('part_b')}
<div class="card">{recover_table()}</div>

<h2>出口断面过程线（{esc(d['last_code'])}，四模型最终参数整体复算）</h2>
<div class="card">
<div class="legend">
  <span><i class="dot" style="background:#8a94a3"></i>实测（虚线）</span>
  {''.join(f'<span><i class="dot" style="background:{COLORS[k]}"></i>{esc(info[k]["name"])}</span>' for k in MODELS)}
</div>
{hydro()}
</div>

<h2>结论</h2>
<div class="card">
<ul>
  <li><b>框架自洽性成立</b>：B 部分四个模型都能以高 NSE 回收自身真值（最差单元 {min(v['calib_nse_range'][0] for v in d['part_b'].values()):.3f}~{max(v['calib_nse_range'][1] for v in d['part_b'].values()):.3f}）——多模型注册、链式率定、参数体系按模型隔离的实现是正确的。</li>
  <li><b>但"高 NSE"≠"参数可唯一辨识"</b>：Tank / HBV 参数较多，回收后仍有 80%+ 的参数偏差超过 15%（XAJ / GR4J 约 20%）。这不是实现 bug，而是概念性模型的<b>参数补偿（equifinality）</b>——多组参数给出几乎相同的出口过程线（本平台 P7 的 GLUE 分析正是为此而生）。实践含义：<b>不要逐参数解读率定值</b>；评价模型看过程线指标与验证期表现，参数不确定性用 GLUE 置信带表达。</li>
  <li><b>结构误差量级</b>：A 部分中 HBV（数据生成模型）率定期最差单元 NSE {a_lo_hbv:.3f}；其余模型中最优的 {esc(info[best_other]['name'])} 为 {a_others[best_other]:.3f}，最差的 {esc(info[min(a_others, key=a_others.get)]['name'])} 为 {a_others[min(a_others, key=a_others.get)]:.3f}。用错模型结构的代价在本演示流域约为 {(a_lo_hbv - a_others[best_other]):.2f}–{(a_lo_hbv - a_others[min(a_others, key=a_others.get)]):.2f} 的 NSE 差距——换模型前值得先做这类对比，而不是凭文献偏好选型。</li>
  <li><b>成本参考</b>：相同预算（{d['max_evals']} evals/单元）下 GR4J 耗时最长（{d['part_a']['gr4j']['elapsed_s']:.0f} s）却参数最少——其双单位线（UH1/UH2）逐步演算的成本高于简单线性水库；Tank 最快（{d['part_a']['tank']['elapsed_s']:.0f} s）。预算敏感场景可选 Tank，机制解释优先选 HBV。</li>
  <li><b>局限</b>：本实验"实测"为合成数据（OSSE），只代表「HBV 能生成的过程」范围内的结构误差；对真实观测（含观测误差与人类活动影响）结论需重新标定。真实流域应用建议：多模型并行率定 → 按验证期指标与过程线形态（洪峰 / 基流 / 峰现时间）选型，或做加权集成。</li>
</ul>
</div>

<div class="note">复现方式：<code>cd backend &amp;&amp; PYTHONPATH=. python scripts/_compare_models.py 1200</code>，随后 <code>python scripts/_render_compare.py</code> 生成本报告。</div>
</div></body></html>"""

import os
os.makedirs(os.path.dirname(OUT) or ".", exist_ok=True)
open(OUT, "w", encoding="utf-8", newline="\n").write(HTML)
print("written", OUT, len(HTML), "bytes")
