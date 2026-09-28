# -*- coding: utf-8 -*-
"""把 _hybrid_residual_result.json 渲染成决策级预研报告（docs/hybrid-residual-study.md）。

关键比较口径：以**负对照（S2，基模型 = 数据生成模型）**的 ΔNSE 作为「协议地板」——
地板来源是参数失配（率定值 ≠ 真值，见 P12 equifinality）+ 序列自相关，
任何结构误差场景必须先超过地板，才谈得上「学到了结构」。

用法：PYTHONPATH=. python scripts/_render_hybrid.py
"""
import json

SRC = "_hybrid_residual_result.json"
DST = "../docs/hybrid-residual-study.md"

with open(SRC, encoding="utf-8") as f:
    d = json.load(f)

P = d["protocol"]
SC = d["scenarios"]
CTRL_KEY = "hbv"


def f3(v):
    return "—" if v is None else f"{float(v):.3f}"


def f1(v):
    return "—" if v is None else f"{float(v):.1f}"


def signed(v, nd=4):
    return "—" if v is None else f"{float(v):+.{nd}f}"


def best_gain(sc):
    g = sc["mean_delta_nse"]
    vals = [v for v in (g.get("ridge"), g.get("mlp")) if v is not None]
    return max(vals) if vals else None


floor = best_gain(SC[CTRL_KEY]) if CTRL_KEY in SC else None
s1_keys = [k for k in SC if k != CTRL_KEY]
s1_best_key = max(s1_keys, key=lambda k: best_gain(SC[k]) or -9) if s1_keys else None
s1_best = best_gain(SC[s1_best_key]) if s1_best_key else None

lines = []
w = lines.append

w("# 混合模型预研：概念性模型 + 数据驱动残差校正")
w("")
w(f"> 生成时间 {d['generated_at']}　|　项目 `{d['pid']}`　|　率定预算 {d['max_evals']} evals/单元　|　"
  f"实测来源：{P['obs_source']}（OSSE）")
w("")

# ------------------------------------------------------------------ 摘要
w("## 1. 结论摘要")
w("")
w("**一句话**：残差校正在本实验里对**所有**基模型都有效（0.86~0.97 → 0.97~0.99），"
  "但它把模型之间的差异**抹平**了——校正后三个场景几乎不可区分；"
  "因此这个增益**不能作为「模型结构重要/不重要」的证据**，也不能替代率定本身。"
  "另外，线性岭回归与 MLP 的差距 ≤0.003 NSE，**没有证据支持引入 LSTM 这类非线性序列模型**。")
w("")
w(f"1. **三种场景全都有增益、且校正后趋同**：")
for k, s in SC.items():
    us = list((s.get("units") or {}).values())
    if not us:
        continue
    b0 = min(u["base"]["nse"] for u in us if u["base"]["nse"] is not None)
    b1 = max(u["base"]["nse"] for u in us if u["base"]["nse"] is not None)
    c0 = min(min(u["ridge"]["nse"], u["mlp"]["nse"]) for u in us
             if u["ridge"]["nse"] is not None and u["mlp"]["nse"] is not None)
    c1 = max(max(u["ridge"]["nse"], u["mlp"]["nse"]) for u in us
             if u["ridge"]["nse"] is not None and u["mlp"]["nse"] is not None)
    w(f"   - `{k}`：基模型测试段 NSE {b0:.3f}~{b1:.3f} → 校正后 **{c0:.3f}~{c1:.3f}**")
w(f"2. **负对照（S2）也拿到 {signed(floor)}**：这一场景的基模型**就是**生成数据的 HBV，"
  f"理论上残差只剩观测噪声；它仍能被「修正」出 {signed(floor)} 的提升，"
  f"说明**增益主要来自「基模型有多少残差可洗」，而不是「残差里有多少结构」**——"
  f"有限预算下率定参数的失配（P12 已量化的参数补偿）本身就是一个大而稳定的可学偏差。")
if s1_best is not None and floor is not None:
    w(f"3. **结构误差场景（{SC[s1_best_key]['label']}）{signed(s1_best)}**，"
      f"比负对照高 {signed(s1_best - floor)}。这个差值才是「结构缺失」可归因的部分，"
      f"**远小于总增益**——若只看 S1 而忽略负对照，会高估结构学习的价值约 "
      f"{(s1_best / floor):.1f} 倍。")
w("4. **置换检验（打乱目标后同样拟合）≈ 0**（各场景 "
  + "、".join(f"{signed((s['mean_delta_nse'] or {}).get('placebo'))}"
              for s in SC.values()) + "）：证明增益既不是特征泄漏、也不是实现漏洞——"
  "必须有真实的预测信息才能拿到。")
mlp_gap = max(abs((s["mean_delta_nse"]["ridge"] or 0) - (s["mean_delta_nse"]["mlp"] or 0))
              for s in SC.values())
w(f"5. **线性已够**：岭回归与 MLP 的平均 ΔNSE 差 **≤{mlp_gap:.4f}**（各场景）。"
  f"残差里可学的部分基本是线性可分的，非线性（更别说循环结构）没有兑现成精度。")
w("6. **对平台的含义**：混合层会**掩盖**本平台的核心诊断能力——参数可解释性与模型结构对比"
  "（P12 的差异分析在校正后会失效）。它只应作为「精度模式」的可选增强，"
  "且其精度提升**不得**被解读为任何关于模型结构的结论。")
w("")
w("> **本研究的副产物（已修）**：实验暴露出一个**可复现性缺陷** —— "
  "`calibrate.py` 用 `seed = cfg_seed + hash(unit_code) % 1000` 为各单元错开随机种子，"
  "而 Python 对字符串的 `hash()` **按进程随机化**（PYTHONHASHSEED）："
  "同一配置在不同进程/服务重启后得到不同率定结果（实测同预算下 NSE 波动 0.002~0.01，"
  "并向下游传播到测试段 0.02 级）。已改为 `zlib.crc32(code)` 稳定哈希，"
  "并验证 `PYTHONHASHSEED=1/2/0` 三个进程结果逐位一致。")
w("")

# ------------------------------------------------------------------ 设计
w("## 2. 实验设计")
w("")
w("### 2.1 严格时序三段协议（杜绝泄漏）")
w("")
w("| 段 | 占比 | 步数 | 用途 | 截止 |")
w("|---|---|---|---|---|")
w(f"| A | {P['A'][1] / P['n']:.0%} | {P['A'][1] - P['A'][0]} | 概念性模型率定（内部再 70/30 分期） | {P['A_end']} |")
w(f"| B | {(P['B'][1] - P['B'][0]) / P['n']:.0%} | {P['B'][1] - P['B'][0]} | 残差模型训练（用 A 段参数的**样本外**残差） | {P['B_end']} |")
w(f"| C | {(P['C'][1] - P['C'][0]) / P['n']:.0%} | {P['C'][1] - P['C'][0]} | 测试（两模型均未见过） | — |")
w("")
w("残差模型**刻意不在率定期训练**：率定期残差被概念性模型刚拟合过、系统性偏差被压小，"
  "在它上面训练会给出虚高的增益。B 段对概念性模型是样本外、对残差模型是训练集，C 段对两者都是样本外。")
w("")
w("### 2.2 三个场景（含负对照）")
w("")
w("| 场景 | 基模型 | 与数据生成模型关系 | 预期 |")
w("|---|---|---|---|")
w(f"| S1-a | 新安江 `xaj` | 结构不同（无雪模块） | 残差含结构误差 |")
w(f"| S1-b | Tank `tank` | 结构不同（串联水箱） | 结构误差最大 |")
w(f"| S2 | HBV `hbv` | **就是数据生成模型**（雪版 OSSE 真值） | 残差≈噪声 → **负对照/地板** |")
w("")
w("实测流量由演示项目的 HBV 雪版真值参数生成（OSSE，含 5% 噪声）。三个场景用的是**同一套实测**，"
  "唯一变量是基模型——因此场景间的差值可以干净地归因到「模型结构不匹配」。")
w("")
w("### 2.3 残差模型（两个拟合器，同特征集消融）")
w("")
w("拟合目标在 `sqrt(流量)` 空间：`r = sqrt(obs) − sqrt(sim)`（开方压缩峰值异方差，"
  "使均方损失不被大洪水主导）；重建时 `sim_hyb = (sqrt(sim) + r̂)²`。")
w("")
w("特征全部**因果**（只用 t 及以前已知量，可用于实时预报）：")
w("")
w("| 组 | 内容 | 个数 |")
w("|---|---|---|")
w("| 面雨量 | `p_lag0..3` | 4 |")
w("| 气温 | `t_lag0..1`（雪版模型的关键驱动） | 2 |")
w("| 蒸发 | `e` | 1 |")
w("| 基模型模拟流 | `zsim_lag0..2`（sqrt 空间） | 3 |")
w("| 历史残差 | `res_lag1..3`（自回归记忆，替代 RNN 的状态） | 3 |")
w("| 季节 | 日序 `sin`/`cos` | 2 |")
w("")
w("| 拟合器 | 说明 | 参数量 |")
w("|---|---|---|")
w("| 岭回归 | 线性闭式解，λ 在 B 段内部留出尾部验证集上选（不碰 C） | 每单元 1 组系数 |")
w("| MLP | 单隐层 8 单元 tanh，Adam（手写反向传播），L2=1e-4，B 段尾部早停 | ~150 权重 |")
w("")
w("两个拟合器共用同一特征矩阵 → 干净的「线性可解释部分 vs 非线性增量」对比。")
w("")

# ------------------------------------------------------------------ 结果
w("## 3. 结果总览")
w("")
w("| 基模型 | 场景 | A 段率定 NSE（最差~最优） | 测试段基模型 NSE | 平均 ΔNSE（岭） | 平均 ΔNSE（MLP） | 置换检验 ΔNSE | 超地板 |")
w("|---|---|---|---|---|---|---|---|")
for k, s in SC.items():
    a = [v for v in (s.get("a_calib_nse") or {}).values() if v is not None]
    a_txt = f"{min(a):.3f}~{max(a):.3f}" if a else "—"
    bs = [u["base"]["nse"] for u in (s.get("units") or {}).values() if u["base"]["nse"] is not None]
    b_txt = f"{min(bs):.3f}~{max(bs):.3f}" if bs else "—"
    g = s["mean_delta_nse"]
    over = None if floor is None or k == CTRL_KEY else (
        (best_gain(s) - floor) if best_gain(s) is not None else None)
    over_txt = "地板（对照）" if k == CTRL_KEY else signed(over)
    w(f"| `{k}` | {s['label']} | {a_txt} | {b_txt} | {signed(g['ridge'])} | {signed(g['mlp'])} | "
      f"{signed(g.get('placebo'))} | {over_txt} |")
w("")
w("> 注：测试段基模型 NSE 普遍**高于** A 段率定 NSE —— 这是本演示数据（3 年合成序列）的性质"
  "（后段过程更平缓易拟合），不是泄漏；但也意味着绝对 NSE 不能跨段横向比较，只能在同一测试段内比。")
w("")

for idx, (k, s) in enumerate(SC.items(), start=1):
    w(f"### 3.{idx} 逐单元明细 — {s['label']}")
    w("")
    w("| 单元 | 训练/测试 | 指标 | 基模型 | +岭回归 | +MLP |")
    w("|---|---|---|---|---|---|")
    for code, u in (s.get("units") or {}).items():
        b, r, m = u["base"], u["ridge"], u["mlp"]
        w(f"| {code} | {u['n_train']}/{u['n_test']} | NSE | {f3(b['nse'])} | {f3(r['nse'])} | {f3(m['nse'])} |")
        w(f"| | | KGE | {f3(b['kge'])} | {f3(r['kge'])} | {f3(m['kge'])} |")
        w(f"| | | PBIAS (%) | {f1(b['pbias'])} | {f1(r['pbias'])} | {f1(m['pbias'])} |")
        w(f"| | | 洪峰偏差 (%) | {f1(b['peak_bias_pct'])} | {f1(r['peak_bias_pct'])} | {f1(m['peak_bias_pct'])} |")
        w(f"| | | 峰现偏移 (时段) | {f1(b['peak_shift_steps'])} | {f1(r['peak_shift_steps'])} | {f1(m['peak_shift_steps'])} |")
        w(f"| | | **ΔNSE** | — | **{signed(u['delta_nse_ridge'])}** | **{signed(u['delta_nse_mlp'])}** |")
        w(f"| | | ΔNSE（置换检验） | — | {signed(u.get('delta_nse_placebo'))} | — |")
    w("")
    w(f"基模型率定耗时 {s['elapsed_s']}s（每单元 {d['max_evals']} evals）；"
      f"残差模型训练 <1 s/单元/拟合器（纯 numpy）。")
    w("")

# ------------------------------------------------------------------ 解读
w("## 4. 为什么必须看负对照与置换检验")
w("")
w("如果只做 S1，看到「+0.02 ~ +0.10 的 NSE 提升」很容易得出「残差学习能补模型结构」的结论。"
  "两个对照打断了这个推理：")
w("")
w(f"- **负对照（S2，基模型 = 数据生成模型 HBV）**：理论上残差只剩噪声，但它仍拿到 "
  f"{signed(floor)} 的平均 ΔNSE —— 与结构误差场景同一量级。"
  f"可见增益的主体是「参数失配 + 自相关」，不是结构；")
w("- **置换检验（打乱目标训练）**：把训练/验证的残差目标打乱后同样拟合，增益坍塌到 ≈0"
  "（部分场景略正，来自「均值偏差订正」这一项仍成立）。这说明增益需要真实预测信息，"
  "排除了特征泄漏与实现漏洞。")
w("")
w("为什么参数失配这么「可学」？率定是**有限预算下的局部最优**：P12 对比实验已量化，"
  "即使 NSE 回收 0.99，仍有大量参数与真值偏差 >15%（参数补偿 / equifinality）。"
  "这种偏差在时间上稳定、在量级上可观，正是数据驱动模型最擅长的目标——"
  "于是「残差校正」在相当程度上变成了**对率定不足的二次补偿**，而不是对物理过程的补充。")
w("")
w("还有一个值得警惕的观察：**校正后三个场景的 NSE 全部落进 0.97~0.99**，"
  "基模型之间 0.86 vs 0.96 的差距被抹平。如果拿混合模型的精度去做模型选型，"
  "会得出「选谁都一样」的错误结论——因为校正器把结构差异一起洗掉了。")
w("")

w("## 5. 局限（不要过度解读本报告）")
w("")
w("- **单流域、单套 OSSE**：3 个单元、B 段仅 ~274 步/单元；OSSE 的「结构误差」是人为设定的，"
  "真实观测还含观测误差、取用水、闸坝调度——后者**不可学**，届时增益会比本报告更小；")
w("- **增益依赖基模型的率定程度**：本实验的基模型只给 700 evals/单元，"
  "其残差里混着大量「没率定到位」的成分（负对照证明了这一点）。"
  "若把预算提到 3000，基模型残差变小，残差校正的增益会同步变小——"
  "**本报告的 ΔNSE 不能当作混合模型的固有收益**；")
w("- **未做超参搜索**：MLP 结构（隐层 8、1200 epoch）与特征集固定，未在 C 段上调优（刻意为之）。"
  "若允许调参，增益会更大——但那已是「在测试集上选模型」，不可信；")
w("- **残差模型与流域/基模型绑定**：换流域需重训，跨流域迁移（预训练+微调）未验证；")
w("- **破坏水量平衡**：残差校正直接改造出流，概念性模型的水量闭合恒等式不再成立，"
  "工程上需在用完后做一次平衡订正（P2 的闭合账本已具备该能力）；")
w("- **未评估低流量/枯季**：NSE 由大洪水主导，枯季表现可能被掩盖。")
w("")

w("## 6. 建议")
w("")
w("| 结论 | 依据 | 行动 |")
w("|---|---|---|")
w("| **不引入 LSTM/深度模型** | 线性岭回归与 MLP 的 ΔNSE 差 ≤0.003，非线性未兑现 | 若做混合层，用线性残差项（可解释、参数少、训练毫秒级） |")
w("| **不把混合精度当作结构证据** | 负对照（同模型）也涨 "+signed(floor)+"，校正后三场景趋同 0.97~0.99 | 平台内若提供混合模式，须同时展示「基模型 vs 校正后」与负对照口径，避免误用 |")
w("| **先补率定、再谈混合** | 增益主体是参数失配 | 提高 `max_evals`／用 P5 联合率定／用 GLUE 看可辨识性，先把基模型榨到位；混合层只做兜底 |")
w("| **峰值保真不能靠 NSE 目标** | 多个单元洪峰偏差在校正后变差 | 若面向防洪，训练目标改峰加权，或对洪峰单独建模 |")
w("| 下一步：稳健性检验（H1） | 单流域单套数据不足以决策 | 多流域 × 4 基模型 × 多种子重跑本协议，看「S1 − 负对照」是否稳定为正 |")
w("")
w("**给路线图的直接答复**：混合模型的真实价值定位是**兜底与精度上限**，不是「更强的模型」；"
  "而本项目的数据规模下，一个线性残差项就能拿到全部可得收益，"
  "**不必**为它引入深度学习栈与训练管线。若将来接入更多流域的**真实观测**（观测误差不可学），"
  "再重新评估——届时增益会更小，结论只会更保守。")
w("")
w("---")
w("")
w(f"复现：`cd backend && PYTHONPATH=. python scripts/_hybrid_residual.py {d['pid']} {d['max_evals']}`"
  f"（离线，无需服务；报告：`python scripts/_render_hybrid.py`）")
w("")

with open(DST, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print(f"报告已写 {DST}（{len(lines)} 行, {sum(len(x) for x in lines)} 字符）")
print(f"地板(负对照) = {signed(floor)}  |  S1 最好 = {s1_best_key} {signed(s1_best)}")
