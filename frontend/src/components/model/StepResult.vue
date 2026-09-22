<template>
  <div class="step">
    <!-- 数据源 -->
    <div class="src-bar">
      <button class="src-btn" :class="{ on: mode === 'sim' }" :disabled="!sim" @click="mode = 'sim'">
        模拟结果
        <span v-if="sim" class="muted">{{ sim.n_steps }} 时段</span>
      </button>
      <button class="src-btn" :class="{ on: mode === 'calib' }" :disabled="!calib" @click="mode = 'calib'">
        率定结果
        <span v-if="calib" class="muted mono">{{ (calib.run_id || '').slice(0, 8) }}</span>
      </button>
      <span class="grow"></span>
      <span v-if="mode === 'calib' && calib" class="muted small">
        {{ calib.finished_at ? calib.finished_at.replace('T', ' ').slice(0, 16) : '' }} ·
        用时 {{ calib.elapsed_s }} s ·
        {{ calib.stopped ? '已终止（部分单元未率定）' : '正常结束' }}
      </span>
    </div>

    <div v-if="!doc" class="empty-line">
      <template v-if="mode === 'sim'">
        还没有模拟结果。到「② 配置」点「运行模拟（当前参数）」即用当前参数演算全流域。
      </template>
      <template v-else>
        还没有率定结果。到「② 配置」点「启动率定」，或在「③ 运行」里点选一次已完成的任务。
      </template>
    </div>

    <template v-else>
      <!-- 率定：单元状态 + 采纳 -->
      <template v-if="mode === 'calib'">
        <div class="sub-sec">
          ① 单元率定情况
          <span class="muted">{{ calib.joint ? '联合率定：全站同时优化，目标为各站加权平均' : '按排水序自上而下链式率定；无出口实测的单元不独立率定' }}</span>
          <span class="grow"></span>
          <a class="btn sm" :href="exportUrl('params')" download>导出参数 CSV</a>
          <a class="btn sm" :href="exportUrl('flow')" download>导出过程线 CSV</a>
          <button id="cal-adopt" class="btn primary sm" :disabled="!!state.busy" @click="adopt">
            采纳为项目参数
          </button>
        </div>
        <div class="tbl-wrap unit-wrap">
          <table class="tbl">
            <thead>
              <tr>
                <th style="width: 62px">单元</th>
                <th style="width: 90px">名称</th>
                <th style="width: 88px">出口站</th>
                <th class="num" style="width: 68px">控制面积</th>
                <th class="num" style="width: 62px">评估</th>
                <th class="num" style="width: 54px">代数</th>
                <th class="num" style="width: 80px">最优 F</th>
                <th class="num" style="width: 62px">用时</th>
                <th>状态</th>
                <th class="num" style="width: 120px">率定期 NSE</th>
                <th class="num" style="width: 120px">验证期 NSE</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="u in calibUnits" :key="u.code">
                <td>
                  <span class="dot" :style="{ background: colorOf(u.code) }"></span>{{ u.code }}
                </td>
                <td class="small">{{ u.name || '—' }}</td>
                <td class="small">{{ u.outlet_station || '—' }}</td>
                <td class="num muted">{{ fmt(u.area_km2, 1) }}</td>
                <td class="num">{{ u.evals || 0 }}</td>
                <td class="num">{{ u.gens || 0 }}</td>
                <td class="num">{{ fmt(u.objective, 5) }}</td>
                <td class="num">{{ u.elapsed_s == null ? '—' : u.elapsed_s }}</td>
                <td class="small">
                  <span :class="u.calibrated ? 'ok-t' : 'warn-t'">
                    {{ u.calibrated ? '已率定' : statusCn(u.status) }}
                  </span>
                  <span v-if="u.borrowed_from" class="muted"> ← {{ u.borrowed_from }}</span>
                  <span v-if="u.shared_keys && u.shared_keys.length" class="muted">
                    （{{ u.shared_keys.join('、') }} 共享）
                  </span>
                </td>
                <td class="num" :class="nseClass(mOf(u.code, 'calib'))">{{ fmt(mOf(u.code, 'calib'), 3) }}</td>
                <td class="num" :class="nseClass(mOf(u.code, 'valid'))">{{ fmt(mOf(u.code, 'valid'), 3) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div v-if="notes.length" class="note">
          <div v-for="(n, i) in notes" :key="i">· {{ n }}</div>
        </div>

        <!-- 真值回收对比 -->
        <template v-if="(calib.truth_compare || []).length">
          <div class="sub-sec">
            ② 真值参数回收对比
            <span class="muted">演示数据的"实测"由真值参数生成，可检验率定是否收敛到真值</span>
          </div>
          <div class="tbl-wrap truth-wrap">
            <table class="tbl">
              <thead>
                <tr>
                  <th style="width: 62px">单元</th>
                  <th v-for="k in truthKeys" :key="k" class="num">{{ k }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="c in calib.truth_compare" :key="c.code">
                  <td>
                    <span class="dot" :style="{ background: colorOf(c.code) }"></span>{{ c.code }}
                  </td>
                  <td v-for="row in ordered(c.rows)" :key="row.key" class="num cell2">
                    <div class="v1">{{ fmt(row.calib, 3) }}</div>
                    <div class="v2">真值 {{ fmt(row.truth, 3) }}</div>
                    <div class="v3" :class="relClass(row.rel_pct)">
                      {{ row.rel_pct == null ? '—' : (row.rel_pct > 0 ? '+' : '') + row.rel_pct + '%' }}
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="note">
            不敏感参数（如 SM、CS、EX）常出现"等效性"——数值偏离真值但 NSE 几乎不变，这不代表率定失败。
            真正要看的是 K / B / CG / CI / KGF 这些高敏参数的回收情况。
          </div>
        </template>
      </template>

      <!-- 单元切换 -->
      <div class="sub-sec">
        {{ mode === 'calib' ? '③' : '①' }} 单元过程线
        <span class="muted">点击切换单元；括号内为 NSE</span>
        <span class="grow"></span>
        <span class="muted">{{ doc.period.start }} ~ {{ doc.period.end }} · {{ doc.n_steps }} 时段 · {{ doc.dt_s / 3600 }} h 步长</span>
      </div>
      <div class="units">
        <button
          v-for="u in doc.units"
          :key="u.code"
          class="ubtn"
          :class="{ on: u.code === activeCode }"
          @click="activeCode = u.code"
        >
          <span class="dot" :style="{ background: colorOf(u.code) }"></span>
          {{ u.code }}
          <b :class="nseClass(nseOf(u))">{{ fmt(nseOf(u), 3) }}</b>
        </button>
      </div>

      <template v-if="active">
        <!-- 指标卡 -->
        <div class="cards" :class="{ 'cards-2': mode === 'calib' }">
          <div v-for="d in metDefs" :key="d.key" class="card">
            <div class="ck">{{ d.label }}</div>
            <template v-if="mode === 'calib'">
              <div class="pair">
                <span><i>率定</i><b :class="d.cls ? d.cls(valOf(d, 'calib')) : ''">{{ show(d, 'calib') }}</b></span>
                <span><i>验证</i><b :class="d.cls ? d.cls(valOf(d, 'valid')) : ''">{{ show(d, 'valid') }}</b></span>
              </div>
            </template>
            <template v-else>
              <div class="cv" :class="d.cls ? d.cls(valOf(d, null)) : ''">
                {{ show(d, null) }} <i v-if="d.unit && !d.isPeak">{{ d.unit }}</i>
              </div>
              <div v-if="d.isPeak" class="cs">
                实测均值 {{ fmt(metAll.obs_mean, 1) }} · 模拟均值 {{ fmt(metAll.sim_mean, 1) }} m³/s
              </div>
            </template>
          </div>
        </div>

        <!-- 过程线 -->
        <div class="chart-box">
          <div class="chart-head">
            <b>{{ active.code }} {{ active.name }}</b>
            <span class="muted">
              面积 {{ fmt(active.area_km2, 0) }} km²
              <template v-if="active.outlet_station"> · 出口 {{ active.outlet_station }}</template>
              <template v-if="(active.upstream || []).length"> · 上游来流 {{ active.upstream.join('、') }}</template>
              <template v-else> · 无上游来流</template>
            </span>
            <span class="grow"></span>
            <span v-if="splitIndex != null" class="muted">阴影为验证期</span>
          </div>
          <EChart :option="flowOpt" :height="252" />
        </div>

        <!-- 洪峰明细 -->
        <div class="sub-sec">
          洪峰明细
          <span class="muted">多场洪峰统计：取最显著 5 场（≥1.5×均值、间隔≥10 时段），±15 时段窗口内匹配，取中位数</span>
          <span class="grow"></span>
          <span v-if="mode === 'calib'" class="seg">
            <button :class="{ on: peakMode === 'calib' }" @click="peakMode = 'calib'">率定期</button>
            <button :class="{ on: peakMode === 'valid' }" @click="peakMode = 'valid'">验证期</button>
            <button :class="{ on: peakMode === 'all' }" @click="peakMode = 'all'">全时段</button>
          </span>
        </div>
        <div v-if="!peak.events || !peak.events.length" class="empty-line small">
          该时段内没有达到阈值的显著洪峰。
        </div>
        <div v-else class="tbl-wrap peak-wrap">
          <table class="tbl">
            <thead>
              <tr>
                <th style="width: 146px">实测峰现</th>
                <th style="width: 146px">模拟峰现</th>
                <th class="num" style="width: 78px">误差</th>
                <th class="num">实测峰值</th>
                <th class="num">模拟峰值</th>
                <th class="num">相对误差</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(e, i) in peak.events" :key="i">
                <td class="small">{{ e.obs_time || '—' }}</td>
                <td class="small">{{ e.sim_time || '—' }}</td>
                <td class="num">{{ e.diff_steps > 0 ? '+' : '' }}{{ e.diff_steps }}</td>
                <td class="num">{{ fmt(e.obs_peak, 1) }}</td>
                <td class="num">{{ fmt(e.sim_peak, 1) }}</td>
                <td class="num" :class="Math.abs(peakRel(e) || 0) > 15 ? 'warn-t' : 'ok-t'">
                  {{ fmt(peakRel(e), 1) }}%
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- 水量平衡 -->
        <div class="sub-sec">水量平衡（mm，全时段累计）</div>
        <div class="tbl-wrap wb-wrap">
          <table class="tbl">
            <thead>
              <tr>
                <th style="width: 54px">单元</th>
                <th class="num">降水 P</th>
                <th class="num">蒸发 E</th>
                <th class="num">出流 q</th>
                <th class="num">Δ蓄量</th>
                <th class="num">闭合误差</th>
                <th class="num">径流系数</th>
                <th class="num" style="width: 92px">闭合率</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="b in doc.water_balance" :key="b.code">
                <td>
                  <span class="dot" :style="{ background: colorOf(b.code) }"></span>{{ b.code }}
                </td>
                <td class="num">{{ fmt(b.sum_p, 1) }}</td>
                <td class="num">{{ fmt(b.sum_e, 1) }}</td>
                <td class="num">{{ fmt(b.sum_q, 1) }}</td>
                <td class="num">{{ fmt(b.storage_change, 1) }}</td>
                <td class="num">{{ b.closure == null ? '—' : b.closure.toExponential(1) }}</td>
                <td class="num">{{ fmt(b.sum_p > 0 ? b.sum_q / b.sum_p : null, 3) }}</td>
                <td class="num">
                  <span class="ok-t">{{ b.closure_rate == null ? '—' : b.closure_rate.toExponential(1) }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="note">
          恒等式 <b>P = E + q + Δ蓄量</b>；闭合率 = 闭合误差 / 降水总量，机器精度（≈1e-16）即通过。
          出流 q 为单元出口断面（含上游来流演算结果）。
        </div>

        <!-- 参数不确定性（GLUE）P7 -->
        <template v-if="mode === 'calib'">
          <div class="sub-sec">
            参数不确定性（GLUE）
            <span class="muted">参数区间均匀采样 → 全链演算 → NSE² 加权 90% 置信带（Beven &amp; Binley, 1992）</span>
            <span class="grow"></span>
            <label class="glue-lb">采样数 <input id="glue-n" v-model.number="glueForm.n_samples" type="number" min="50" max="5000" step="50" class="num-in" /></label>
            <label class="glue-lb">阈值 NSE ≥ <input id="glue-th" v-model.number="glueForm.threshold" type="number" min="0" max="1" step="0.05" class="num-in" /></label>
            <button id="glue-run" class="btn primary sm" :disabled="glueRunning || !!state.busy" @click="runGlue">
              {{ glueRunning ? '分析中…' : '运行 GLUE 分析' }}
            </button>
          </div>
          <div v-if="!glueResult && !glueRunning" class="empty-line small">
            还没有 GLUE 分析结果。设置采样数（建议 ≥500）与行为阈值后点「运行 GLUE 分析」。
          </div>
          <template v-if="glueResult">
            <div class="units">
              <button
                v-for="s in glueResult.stations"
                :key="s.code"
                class="ubtn"
                :class="{ on: s.code === glueCode }"
                @click="glueCode = s.code"
              >
                <span class="dot" :style="{ background: colorOf(s.code) }"></span>
                {{ s.code }}
                <b>行为 {{ s.behavioral_count }}</b>
              </button>
              <span class="muted small">
                采样 {{ glueResult.n_samples }} · 阈值 {{ glueResult.threshold }} · 用时 {{ glueResult.elapsed_s }} s
              </span>
            </div>
            <div v-if="glueCur" class="chart-box">
              <div class="chart-head">
                <b>{{ glueCur.code }} {{ glueCur.name }}</b>
                <span class="muted">
                  NSE 最优 {{ fmt(glueCur.nse_best, 3) }} · 行为中位 {{ fmt(glueCur.nse_median_beh, 3) }}
                  <template v-if="glueCur.cover_90 != null"> · 90% 区间覆盖率 {{ fmt(glueCur.cover_90 * 100, 0) }}%</template>
                  <template v-if="glueCur.fallback"> · <span class="warn-t">无样本达阈值，已回退 NSE 前 10%</span></template>
                </span>
              </div>
              <EChart :option="glueOpt" :height="252" />
            </div>
            <div class="note">
              阴影带为 90% 置信区间（NSE² 加权 5%~95% 分位）；蓝线为 50% 分位（中心趋势），
              红线为 NSE 最优样本过程线，黑点为实测。
            </div>

            <div class="sub-sec">
              行为参数后验范围
              <span class="muted">NSE ≥ {{ glueResult.threshold }} 样本的参数取值范围（括号内为率定区间）；范围越窄，参数可辨识性越好</span>
            </div>
            <div class="tbl-wrap glue-wrap">
              <table class="tbl">
                <thead>
                  <tr>
                    <th style="width: 62px">单元</th>
                    <th style="width: 72px">参数</th>
                    <th class="num">后验 min</th>
                    <th class="num">后验 max</th>
                    <th class="num" style="width: 90px">占区间宽</th>
                    <th class="num">率定区间</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="r in glueResult.param_ranges" :key="r.code + r.key">
                    <td>
                      <span class="dot" :style="{ background: colorOf(r.code) }"></span>{{ r.code }}
                    </td>
                    <td class="small mono">{{ r.key }}</td>
                    <td class="num">{{ fmt(r.min, 3) }}</td>
                    <td class="num">{{ fmt(r.max, 3) }}</td>
                    <td class="num" :class="bandClass(r)">{{ bandPct(r) }}%</td>
                    <td class="num muted">({{ fmt(r.range_lo, 2) }} ~ {{ fmt(r.range_hi, 2) }})</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <div v-if="(glueResult.warnings || []).length" class="warn-box">
              <div v-for="(w, i) in glueResult.warnings" :key="i">· {{ w }}</div>
            </div>
          </template>
        </template>

        <div v-if="(doc.warnings || []).length" class="warn-box">
          <div v-for="(w, i) in doc.warnings" :key="i">· {{ w }}</div>
        </div>
      </template>
    </template>
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import EChart from './EChart.vue'
import { api } from '../../api'
import { flowOption } from '../../charts'
import { adoptCalibrationResult, state, subbasinColorOf, toast } from '../../store'

const props = defineProps({
  view: { type: String, default: '' } // 'sim' | 'calib'
})
const emit = defineEmits(['goto'])

const mode = ref('sim')
const activeCode = ref('')
const peakMode = ref('all')

const sim = computed(() => state.simResult)
const calib = computed(() => state.calib.result)
const doc = computed(() => (mode.value === 'calib' ? calib.value && calib.value.simulation : sim.value))

function syncMode() {
  const v = props.view
  if (v === 'calib' && calib.value) mode.value = 'calib'
  else if (v === 'sim' && sim.value) mode.value = 'sim'
  else if (!doc.value) mode.value = calib.value ? 'calib' : 'sim'
}

watch(
  () => [props.view, !!state.calib.result, !!state.simResult],
  () => {
    syncMode()
    if (mode.value === 'calib') peakMode.value = 'calib'
    else peakMode.value = 'all'
    const list = (doc.value && doc.value.units) || []
    if (!list.some((u) => u.code === activeCode.value)) activeCode.value = (list[0] || {}).code || ''
  },
  { immediate: true }
)

// 切换数据源时重置峰值口径与单元选择
watch(mode, () => {
  peakMode.value = mode.value === 'calib' ? 'calib' : 'all'
  const list = (doc.value && doc.value.units) || []
  if (!list.some((u) => u.code === activeCode.value)) activeCode.value = (list[0] || {}).code || ''
})

const calibUnits = computed(() => (calib.value && calib.value.units) || [])
const metricsMap = computed(() => {
  const out = {}
  for (const m of (calib.value && calib.value.metrics) || []) out[m.code] = m
  return out
})
const active = computed(() => ((doc.value && doc.value.units) || []).find((u) => u.code === activeCode.value))
const metAll = computed(() => (active.value && active.value.metrics) || {})

function mOf(code, which) {
  const m = metricsMap.value[code]
  if (!m || !m[which]) return null
  return (m[which] || {}).nse
}
function nseOf(u) {
  if (mode.value === 'calib') return mOf(u.code, 'calib')
  return (u.metrics || {}).nse
}

const notes = computed(() => calibUnits.value.filter((u) => u.note).map((u) => `${u.code}：${u.note}`))

const truthKeys = computed(() => {
  const rows = (calib.value && calib.value.truth_compare) || []
  return rows.length ? rows[0].rows.map((r) => r.key) : []
})
function ordered(rows) {
  const idx = truthKeys.value
  return [...rows].sort((a, b) => idx.indexOf(a.key) - idx.indexOf(b.key))
}
function relClass(p) {
  if (p == null) return 'muted'
  return Math.abs(p) < 6 ? 'ok-t' : Math.abs(p) < 20 ? 'warn-t' : 'muted'
}

// ---------------- 指标
const metDefs = [
  { key: 'nse', label: 'NSE 纳什效率', n: 3, cls: (v) => nseClass(v) },
  { key: 'r2', label: 'R² 相关系数', n: 3 },
  { key: 'kge', label: 'KGE', n: 3 },
  { key: 'rmse', label: 'RMSE', n: 1, unit: 'm³/s' },
  { key: 'pbias', label: 'PBIAS 总量偏差', n: 1, unit: '%', cls: (v) => (Math.abs(v || 0) > 10 ? 'warn-t' : 'ok-t') },
  { key: '__peak', label: '峰现时间误差', isPeak: true }
]

function periodMet(which) {
  const m = metricsMap.value[activeCode.value]
  if (!m) return {}
  if (which === 'calib') return m.calib || {}
  if (which === 'valid') return m.valid || {}
  return metAll.value
}
function valOf(d, which) {
  const m = periodMet(which)
  if (d.isPeak) {
    const pe = m.peak_error || {}
    return pe.median_steps
  }
  return m[d.key]
}
function show(d, which) {
  const v = valOf(d, which)
  if (d.isPeak) {
    const m = periodMet(which)
    if (v == null) return '—'
    return `${v} ${(m.peak_error || {}).unit || ''}`
  }
  return fmt(v, d.n)
}

// ---------------- 过程线
const splitIndex = computed(() => {
  if (mode.value !== 'calib' || !calib.value) return null
  const m = metricsMap.value[activeCode.value]
  const t = (active.value && active.value.series && active.value.series.time) || []
  if (!m || !m.valid_range || !t.length) return null
  const idx = t.findIndex((x) => x === m.valid_range[0])
  return idx > 0 ? idx : null
})

const flowOpt = computed(() => {
  const s = (active.value && active.value.series) || {}
  return flowOption({
    times: s.time || [],
    obs: s.obs || [],
    sim: s.sim || [],
    splitIndex: splitIndex.value
  })
})

// ---------------- 洪峰
const peak = computed(() => {
  const m = periodMet(mode.value === 'calib' ? peakMode.value : null)
  return m.peak_error || {}
})
function peakRel(e) {
  if (!e || !e.obs_peak) return null
  return ((e.sim_peak - e.obs_peak) / e.obs_peak) * 100
}

// ---------------- 采纳
async function adopt() {
  const rid = calib.value && calib.value.run_id
  if (!rid) return
  if (!confirm(`将任务 ${String(rid).slice(0, 8)} 的最终参数采纳为项目默认参数集？`)) return
  await adoptCalibrationResult(rid)
  emit('goto', 'config', {})
}

// ---------------- 导出（P5：导出表入模）
function exportUrl(what) {
  const pid = state.project && state.project.id
  const rid = (calib.value && calib.value.run_id) || ''
  return `/api/projects/${pid}/calibration/export?what=${what}&rid=${rid}`
}

// ---------------- GLUE 参数不确定性（P7）
const glueForm = reactive({ n_samples: 500, threshold: 0.7 })
const glueRunning = ref(false)
const glueResult = ref(null)
const glueCode = ref('')

const glueCur = computed(
  () => ((glueResult.value && glueResult.value.stations) || []).find((s) => s.code === glueCode.value) || null
)

async function runGlue() {
  if (glueRunning.value) return
  glueRunning.value = true
  try {
    const res = await api.calibrationGlue(state.project.id, {
      n_samples: glueForm.n_samples || 500,
      threshold: glueForm.threshold || 0.7,
      seed: 7
    })
    glueResult.value = res
    glueCode.value = ((res.stations || [])[0] || {}).code || ''
    toast(`GLUE 分析完成：${res.n_samples} 样本 / ${res.elapsed_s}s`)
  } catch (e) {
    toast((e && e.message) || 'GLUE 分析失败', 'err')
  } finally {
    glueRunning.value = false
  }
}

// 置信带图：q05 与 (q95-q05) 同 stack，后者带 areaStyle 形成面积带
const glueOpt = computed(() => {
  const s = (glueCur.value && glueCur.value.series) || {}
  const t = s.time || []
  const lo = s.q05 || []
  const hi = s.q95 || []
  const band = lo.map((v, i) => (v == null || hi[i] == null ? null : +(hi[i] - v).toFixed(3)))
  const mkLine = (name, data, extra = {}) => ({
    name,
    type: 'line',
    data,
    symbol: 'none',
    connectNulls: true,
    ...extra
  })
  return {
    animation: false,
    grid: { left: 54, right: 12, top: 26, bottom: 24 },
    legend: { top: 0, itemWidth: 14, itemHeight: 8, textStyle: { fontSize: 10, color: '#666' } },
    tooltip: {
      trigger: 'axis',
      textStyle: { fontSize: 11 },
      valueFormatter: (v) => (v == null ? '—' : Number(v).toFixed(2))
    },
    xAxis: {
      type: 'category',
      data: t,
      axisLabel: { fontSize: 10, color: '#888', formatter: (v) => v.slice(5, 10) }
    },
    yAxis: {
      type: 'value',
      name: 'm³/s',
      nameTextStyle: { fontSize: 10, color: '#888' },
      axisLabel: { fontSize: 10, color: '#888' },
      splitLine: { lineStyle: { color: '#eee' } }
    },
    series: [
      mkLine('q05', lo, { stack: 'band', lineStyle: { opacity: 0 }, areaStyle: { opacity: 0 }, silent: true, itemStyle: { color: 'transparent' } }),
      mkLine('90% 置信带', band, {
        stack: 'band',
        lineStyle: { opacity: 0 },
        areaStyle: { color: 'rgba(47,115,218,0.18)' },
        itemStyle: { color: 'transparent' },
        silent: true
      }),
      mkLine('q50 中位', s.q50 || [], { lineStyle: { width: 1.4, color: '#2f73da' }, itemStyle: { color: '#2f73da' } }),
      mkLine('NSE 最优', s.sim_best || [], { lineStyle: { width: 1.2, color: '#d64a2f' }, itemStyle: { color: '#d64a2f' } }),
      mkLine('实测', s.obs || [], {
        type: 'scatter',
        symbolSize: 3,
        itemStyle: { color: '#333' }
      })
    ]
  }
})

function bandPct(r) {
  const span = (r.range_hi - r.range_lo) || 1
  return Math.min(100, Math.round(((r.max - r.min) / span) * 100))
}
function bandClass(r) {
  const p = bandPct(r)
  return p < 40 ? 'ok-t' : p < 70 ? 'warn-t' : 'muted'
}

function colorOf(code) {
  const list = (state.subbasins && state.subbasins.subbasins) || []
  const sb = list.find((x) => x.code === code)
  return sb ? subbasinColorOf(sb) : '#93a1b0'
}
function statusCn(s) {
  return { skipped: '借用参数', not_run: '未运行', locked: '全部锁定', stopped: '提前终止' }[s] || s || '—'
}
function nseClass(v) {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return 'muted'
  const x = Number(v)
  if (x >= 0.75) return 'ok-t'
  if (x >= 0.5) return 'warn-t'
  return 'bad-t'
}
function fmt(v, n = 2) {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return '—'
  return Number(v).toFixed(n)
}
</script>

<style scoped>
.step {
  font-size: 12px;
}
.src-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding-bottom: 8px;
  margin-bottom: 4px;
  border-bottom: 1px solid var(--line);
}
.src-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 13px;
  border: 1px solid var(--line-strong);
  border-radius: 7px;
  background: #fff;
  color: var(--text-2);
  font-size: 12px;
}
.src-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.src-btn.on {
  border-color: var(--primary);
  background: var(--primary-soft);
  color: var(--primary);
  font-weight: 600;
}
.grow {
  flex: 1;
}
.units {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 9px;
}
.ubtn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 11px;
  border: 1px solid var(--line-strong);
  border-radius: 20px;
  background: #fff;
  color: var(--text-2);
  font-size: 12px;
}
.ubtn:hover {
  border-color: var(--primary);
}
.ubtn.on {
  border-color: var(--primary);
  background: var(--primary-soft);
  color: var(--primary);
  font-weight: 600;
}
.ubtn b {
  font-variant-numeric: tabular-nums;
}
.cards {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 7px;
  margin-bottom: 10px;
}
.cards-2 {
  grid-template-columns: repeat(6, minmax(0, 1fr));
}
.card {
  padding: 7px 9px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel-2);
}
.card .ck {
  font-size: 11px;
  color: var(--text-3);
  margin-bottom: 3px;
  white-space: nowrap;
}
.card .cv {
  font-size: 15px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.card .cv i {
  font-size: 11px;
  font-weight: 400;
  color: var(--text-3);
  font-style: normal;
}
.card .cs {
  font-size: 10.5px;
  color: var(--text-3);
  margin-top: 2px;
}
.pair {
  display: flex;
  gap: 8px;
}
.pair span {
  display: flex;
  flex-direction: column;
  line-height: 1.35;
}
.pair i {
  font-size: 10px;
  font-style: normal;
  color: var(--text-3);
}
.pair b {
  font-size: 14px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.chart-box {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 8px 10px 2px;
  background: #fff;
}
.chart-head {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  margin-bottom: 2px;
  flex-wrap: wrap;
}
.seg {
  display: inline-flex;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  overflow: hidden;
}
.seg button {
  border: none;
  border-radius: 0;
  padding: 3px 10px;
  font-size: 11px;
  background: #fff;
  color: var(--text-3);
}
.seg button.on {
  background: var(--primary-soft);
  color: var(--primary);
  font-weight: 600;
}
.peak-wrap,
.unit-wrap,
.truth-wrap {
  max-height: 250px;
}
.wb-wrap {
  max-height: 210px;
}
.cell2 {
  line-height: 1.3;
  padding-top: 4px;
  padding-bottom: 4px;
}
.cell2 .v1 {
  font-weight: 600;
}
.cell2 .v2 {
  font-size: 10px;
  color: var(--text-3);
}
.cell2 .v3 {
  font-size: 10px;
}
.note {
  font-size: 11px;
  color: var(--text-3);
  line-height: 1.7;
  margin-top: 5px;
}
.note b {
  color: var(--text-2);
}
.warn-box {
  margin-top: 8px;
  font-size: 11px;
  line-height: 1.7;
  color: var(--amber);
}
.mono {
  font-family: ui-monospace, monospace;
}
.glue-lb {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: var(--text-2);
}
.num-in {
  width: 62px;
  padding: 3px 6px;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.glue-wrap {
  max-height: 260px;
}
</style>
