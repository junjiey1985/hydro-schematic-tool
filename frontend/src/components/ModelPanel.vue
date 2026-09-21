<template>
  <div v-if="show" class="mask" @click.self="$emit('close')">
    <div class="modal mp-modal">
      <h3>
        模型模拟 · 新安江三水源 + 马斯京根
        <span class="mp-sub">逐单元演算区间产流 → 沿上下游链马斯京根分段演算至各站出口</span>
      </h3>

      <div class="body">
        <!-- ============ 数据前提 ============ -->
        <div v-if="blockReason" class="hintbar warn-bar" style="margin-bottom: 12px">
          {{ blockReason }}
        </div>

        <template v-else>
          <!-- ============ ① 参数集 ============ -->
          <div class="sub-sec">
            ① 参数集
            <span class="muted">列为预报单元；表内可直接编辑，出区间标红</span>
            <span class="mp-grow"></span>
            <button class="lnk" :disabled="!!state.busy" @click="resetDefault">恢复默认</button>
            <button class="lnk" :disabled="!!state.busy" @click="saveParams">保存为项目参数</button>
          </div>

          <div class="tbl-wrap mp-param-wrap">
            <table class="tbl">
              <thead>
                <tr>
                  <th style="width: 186px">参数</th>
                  <th v-for="u in units" :key="u.code" class="num" style="width: 96px">
                    <span class="dot" :style="{ background: colorOf(u.code) }"></span>{{ u.code }}
                  </th>
                  <th style="width: 104px">率定区间</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="p in paramRows" :key="p.key">
                  <td>
                    {{ p.label }}
                    <span class="muted">{{ p.key }}</span>
                  </td>
                  <td v-for="u in units" :key="u.code" class="num">
                    <input
                      class="mini"
                      :class="{ bad: isBad(u.code, p) }"
                      :value="edited[u.code] ? edited[u.code][p.key] : ''"
                      @input="onEdit(u.code, p, $event.target.value)"
                    />
                  </td>
                  <td class="muted small">{{ p.min }} ~ {{ p.max }}</td>
                </tr>
                <tr>
                  <td>
                    马斯京根汇流时间 KE
                    <span class="muted">按出口间距估算，不可改</span>
                  </td>
                  <td v-for="u in units" :key="u.code" class="num muted">
                    {{ fmt(u.params.KE, 1) }} h
                  </td>
                  <td class="muted small">河长 1.3/1.5 m·s⁻¹</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div class="mp-fixed">
            固定参数：WM = 140 mm（UM 20 / LM 60 / DM 60）· C = 0.15 · IMP = 0.01 · KSS = 0.7 —— 区域经验值，不参与率定
          </div>

          <!-- ============ ② 时段与运行 ============ -->
          <div class="sub-sec">② 模拟时段与运行</div>
          <div class="mp-run-row">
            <label>起</label>
            <input v-model="form.start" type="date" />
            <label>止</label>
            <input v-model="form.end" type="date" />
            <label>预热</label>
            <input v-model.number="form.warmup" type="number" min="0" step="30" class="mp-num" />
            <span class="muted">天（预热期不计入指标）</span>
            <span class="mp-grow"></span>
            <button class="btn primary" :disabled="!ready || !!state.busy" @click="doRun">
              运行模拟
            </button>
          </div>
          <div v-if="badCells.length" class="mp-fixed bad-line">
            有 {{ badCells.length }} 个参数超出率定区间，请修正后再运行。
          </div>

          <!-- ============ ③ 模拟结果 ============ -->
          <div class="sub-sec">
            ③ 模拟结果
            <span v-if="sim" class="muted">
              {{ sim.period.start }} ~ {{ sim.period.end }} · {{ sim.n_steps }} 个时段 ·
              {{ sim.dt_s / 3600 }} h 步长 · 耗时 {{ lastMs }} ms
            </span>
          </div>

          <div v-if="!sim" class="empty-line">
            尚未运行。点击「运行模拟」，用当前参数演算全流域并输出过程线与指标。
          </div>

          <template v-else>
            <!-- 单元切换 -->
            <div class="mp-units">
              <button
                v-for="u in sim.units"
                :key="u.code"
                class="mp-ubtn"
                :class="{ on: u.code === activeCode }"
                @click="activeCode = u.code"
              >
                <span class="dot" :style="{ background: colorOf(u.code) }"></span>
                {{ u.code }}
                <b :class="nseClass(u)">{{ fmt(u.metrics && u.metrics.nse, 3) }}</b>
              </button>
            </div>

            <template v-if="active">
              <!-- 指标卡 -->
              <div class="mp-cards">
                <div class="mp-card">
                  <div class="ck">NSE 纳什效率</div>
                  <div class="cv" :class="nseClass(active)">{{ fmt(met.nse, 3) }}</div>
                </div>
                <div class="mp-card">
                  <div class="ck">R² 相关系数</div>
                  <div class="cv">{{ fmt(met.r2, 3) }}</div>
                </div>
                <div class="mp-card">
                  <div class="ck">KGE</div>
                  <div class="cv">{{ fmt(met.kge, 3) }}</div>
                </div>
                <div class="mp-card">
                  <div class="ck">RMSE</div>
                  <div class="cv">{{ fmt(met.rmse, 1) }} <i>m³/s</i></div>
                </div>
                <div class="mp-card">
                  <div class="ck">PBIAS 总量偏差</div>
                  <div class="cv" :class="Math.abs(met.pbias || 0) > 10 ? 'warn-t' : 'ok-t'">
                    {{ fmt(met.pbias, 1) }}<i>%</i>
                  </div>
                </div>
                <div class="mp-card">
                  <div class="ck">峰现时间误差</div>
                  <div class="cv">
                    {{ pe.median_steps == null ? '—' : pe.median_steps }} <i>{{ pe.unit || '' }}</i>
                  </div>
                  <div class="cs">
                    实测均值 {{ fmt(met.obs_mean, 1) }} · 模拟均值 {{ fmt(met.sim_mean, 1) }} m³/s
                  </div>
                </div>
              </div>

              <!-- 过程线 -->
              <div class="mp-chart-box">
                <div class="mp-chart-head">
                  <b>{{ active.code }} {{ active.name }}</b>
                  <span class="muted">
                    面积 {{ fmt(active.area_km2, 0) }} km²
                    <template v-if="active.outlet_station"> · 出口 {{ active.outlet_station }}</template>
                    <template v-if="active.upstream && active.upstream.length">
                      · 上游来流 {{ active.upstream.join('、') }}
                    </template>
                    <template v-else> · 无上游来流</template>
                  </span>
                  <span class="mp-grow"></span>
                  <span class="lg"><i class="lg-o"></i>实测</span>
                  <span class="lg"><i class="lg-s"></i>模拟</span>
                </div>
                <svg class="mp-chart" :viewBox="`0 0 ${chart.W} ${chart.H}`" preserveAspectRatio="none">
                  <g v-for="t in chart.yt" :key="'y' + t.v">
                    <line :x1="chart.PAD.l" :y1="t.y" :x2="chart.W - chart.PAD.r" :y2="t.y" stroke="#eef2f6" />
                    <text :x="chart.PAD.l - 5" :y="t.y + 3.5" text-anchor="end" class="ax">{{ t.label }}</text>
                  </g>
                  <path :d="chart.obsD" fill="none" stroke="#7b8a99" stroke-width="1.1" />
                  <path :d="chart.simD" fill="none" stroke="#1e6fa8" stroke-width="1.4" />
                </svg>
                <div class="mp-chart-foot">
                  <span>{{ sim.period.start.slice(0, 10) }}</span>
                  <span class="muted">单位 m³/s · 纵轴上限 {{ fmt(chart.max, 0) }}</span>
                  <span>{{ sim.period.end.slice(0, 10) }}</span>
                </div>
              </div>

              <!-- 洪峰明细 -->
              <div v-if="(pe.events || []).length" class="mp-peaks">
                <div class="mp-peaks-t">最显著的 {{ pe.events.length }} 场洪峰（峰现时间误差 {{ pe.median_steps }} {{ pe.unit }}）</div>
                <table class="tbl">
                  <thead>
                    <tr>
                      <th style="width: 140px">实测峰现</th>
                      <th style="width: 140px">模拟峰现</th>
                      <th class="num" style="width: 84px">误差</th>
                      <th class="num" style="width: 96px">实测峰值</th>
                      <th class="num" style="width: 96px">模拟峰值</th>
                      <th class="num">相对误差</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="(e, i) in pe.events" :key="i">
                      <td class="small">{{ e.obs_time || '—' }}</td>
                      <td class="small">{{ e.sim_time || '—' }}</td>
                      <td class="num">{{ e.diff_steps > 0 ? '+' : '' }}{{ e.diff_steps }}</td>
                      <td class="num">{{ fmt(e.obs_peak, 1) }}</td>
                      <td class="num">{{ fmt(e.sim_peak, 1) }}</td>
                      <td class="num" :class="Math.abs(peakRel(e)) > 15 ? 'warn-t' : 'ok-t'">
                        {{ fmt(peakRel(e), 1) }}%
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>

              <!-- 水量平衡 -->
              <div class="sub-sec">水量平衡（mm，模拟全时段累计）</div>
              <div class="tbl-wrap mp-wb-wrap">
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
                    <tr v-for="b in sim.water_balance" :key="b.code">
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
                        <span class="ok-t">
                          {{ b.closure_rate == null ? '—' : b.closure_rate.toExponential(1) }}
                        </span>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div class="mp-fixed">
                恒等式 <b>P = E + q + Δ蓄量</b>；闭合率 = 闭合误差 / 降水总量，机器精度（≈1e-16）即通过。
                出流 q 为单元出口断面（含上游来流演算结果）。
              </div>

              <!-- 警告 -->
              <div v-if="(sim.warnings || []).length" class="mp-warn">
                <div v-for="(w, i) in sim.warnings" :key="i">· {{ w }}</div>
              </div>
            </template>
          </template>
        </template>
      </div>

      <div class="foot">
        <span class="muted" style="margin-right: auto">
          <template v-if="state.simResult">
            结果已保存在内存，切换单元不影响；再次「运行模拟」即用新参数重算
          </template>
        </span>
        <button class="btn" @click="refresh">重新载入参数</button>
        <button class="btn primary" @click="$emit('close')">关闭</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import {
  loadCalibrationParams,
  loadTimeseries,
  runSimulation,
  saveCalibrationParams,
  state,
  subbasinColorOf,
  toast
} from '../store'

const props = defineProps({ show: { type: Boolean, default: false } })
defineEmits(['close'])

const form = reactive({ start: '', end: '', warmup: 0 })
const edited = reactive({})       // code -> { 参数: 数值 }
const activeCode = ref('')
const lastMs = ref(0)

const cal = computed(() => state.calibration)
const units = computed(() => (cal.value && cal.value.units) || [])
const sim = computed(() => state.simResult)
const ready = computed(() => units.value.length > 0)

const blockReason = computed(() => {
  if (!state.project) return '尚未打开项目。'
  if (!cal.value) return '正在读取模型参数…若长时间无响应，请确认后端服务正常。'
  if (!units.value.length) return '尚未划分子流域：模型按预报单元组织，请先在顶栏完成「子流域划分」。'
  const s = (state.timeseries && state.timeseries.summary) || {}
  const c = s.counts || {}
  if (!c.rain || !c.evap) return '缺少时序数据：请先在顶栏「时序数据」导入（或生成）降雨与蒸发序列。'
  return ''
})

// 可编辑参数行（spec 里未标记不可率的都开放；KE 由间距估算，单列只读展示）
const paramRows = computed(() => {
  const sp = (cal.value && cal.value.spec) || {}
  return Object.entries(sp)
    .filter(([, v]) => v.calibrate !== false)
    .map(([key, v]) => ({ key, ...v }))
})

const active = computed(() => (sim.value ? (sim.value.units || []).find((u) => u.code === activeCode.value) : null))
const met = computed(() => (active.value && active.value.metrics) || {})
const pe = computed(() => met.value.peak_error || {})

// ---------------- 参数编辑
function syncEdited() {
  for (const k of Object.keys(edited)) delete edited[k]
  for (const u of units.value) {
    const row = {}
    for (const p of paramRows.value) row[p.key] = u.params[p.key]
    edited[u.code] = row
  }
}

watch(() => cal.value, syncEdited, { immediate: true })

function onEdit(code, p, raw) {
  const v = raw === '' ? null : Number(raw)
  edited[code][p.key] = Number.isFinite(v) ? v : null
}

const badCells = computed(() => {
  const out = []
  for (const u of units.value) {
    for (const p of paramRows.value) {
      const v = edited[u.code] ? edited[u.code][p.key] : null
      if (v === null || !Number.isFinite(v) || v < p.min || v > p.max) out.push(`${u.code}.${p.key}`)
    }
  }
  return out
})

function isBad(code, p) {
  const v = edited[code] ? edited[code][p.key] : null
  return v === null || !Number.isFinite(v) || v < p.min || v > p.max
}

// ---------------- 操作
async function refresh() {
  await loadCalibrationParams(true)
  syncEdited()
}

async function resetDefault() {
  if (!confirm('恢复为默认参数（清空已保存的项目参数集）？')) return
  await saveCalibrationParams(null, { reset: true })
  state.simResult = null
}

async function saveParams() {
  if (badCells.value.length) {
    toast(`有 ${badCells.value.length} 个参数超出区间，未保存`, 'warn')
    return
  }
  const params = {}
  for (const u of units.value) params[u.code] = { ...edited[u.code] }
  await saveCalibrationParams(params)
}

async function doRun() {
  if (badCells.value.length) {
    toast(`有 ${badCells.value.length} 个参数超出区间`, 'warn')
    return
  }
  const params = {}
  for (const u of units.value) params[u.code] = { ...edited[u.code] }
  const period = {}
  if (form.start) period.start = `${form.start} 00:00`
  if (form.end) period.end = `${form.end} 00:00`
  if (form.warmup) period.warmup_days = Number(form.warmup)
  const t0 = performance.now()
  try {
    const r = await runSimulation({ params, period })
    lastMs.value = Math.round(performance.now() - t0)
    if (!activeCode.value) activeCode.value = (r.units[0] || {}).code || ''
    else if (!(r.units || []).some((u) => u.code === activeCode.value)) {
      activeCode.value = (r.units[0] || {}).code || ''
    }
    const okN = (r.units || []).filter((u) => (u.metrics || {}).nse >= 0.5).length
    toast(`模拟完成：${r.units.length} 个单元，NSE ≥ 0.5 的单元 ${okN} 个`, okN ? 'ok' : 'warn', 4200)
  } catch (e) {
    /* store 已提示 */
  }
}

async function loadPeriodDefault() {
  // 用时序数据的起止做默认时段
  const sp = (state.timeseries && state.timeseries.summary && state.timeseries.summary.span) || null
  if (sp && sp.start) {
    form.start = sp.start.slice(0, 10)
    form.end = sp.end.slice(0, 10)
  }
}

watch(
  () => props.show,
  async (v) => {
    if (!v) return
    await loadCalibrationParams(true)
    await loadTimeseries(false)
    await loadPeriodDefault()
  }
)

// ---------------- 过程线（内联 SVG，无需图表库）
const CW = 760
const CH = 200
const PAD = { l: 46, r: 10, t: 10, b: 18 }

function pathOf(arr, n, max) {
  if (!arr || !arr.length) return ''
  const iw = CW - PAD.l - PAD.r
  const ih = CH - PAD.t - PAD.b
  const x = (i) => PAD.l + (n <= 1 ? 0 : (iw * i) / (n - 1))
  const y = (v) => CH - PAD.b - ih * Math.min(1, Math.max(0, v / max))
  let d = ''
  let pen = false
  for (let i = 0; i < arr.length; i++) {
    const v = arr[i]
    if (v === null || v === undefined || !Number.isFinite(v)) {
      pen = false
      continue
    }
    d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`
    pen = true
  }
  return d
}

const chart = computed(() => {
  const a = active.value
  const s = (a && a.series) || {}
  const simArr = s.sim || []
  const obsArr = s.obs || []
  const vals = simArr.concat(obsArr).filter((v) => v !== null && v !== undefined && Number.isFinite(v))
  const max = Math.max(1, ...vals) * 1.08
  const ih = CH - PAD.t - PAD.b
  const yt = []
  for (let k = 0; k <= 4; k++) {
    const v = (max * k) / 4
    yt.push({ v, y: CH - PAD.b - (ih * v) / max, label: v >= 100 ? v.toFixed(0) : v.toFixed(1) })
  }
  return {
    W: CW,
    H: CH,
    PAD,
    max,
    yt,
    simD: pathOf(simArr, simArr.length, max),
    obsD: pathOf(obsArr, obsArr.length, max)
  }
})

// ---------------- 小工具
function fmt(v, n = 2) {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return '—'
  const x = Number(v)
  if (n === 0) return x.toFixed(0)
  return x.toFixed(n)
}
function colorOf(code) {
  const list = (state.subbasins && state.subbasins.subbasins) || []
  const sb = list.find((x) => x.code === code)
  return sb ? subbasinColorOf(sb) : '#93a1b0'
}
function nseClass(u) {
  const v = (u.metrics || {}).nse
  if (v === null || v === undefined) return 'muted'
  if (v >= 0.75) return 'ok-t'
  if (v >= 0.5) return 'warn-t'
  return 'bad-t'
}
function peakRel(e) {
  if (!e || !e.obs_peak) return null
  return ((e.sim_peak - e.obs_peak) / e.obs_peak) * 100
}
</script>

<style scoped>
.mp-modal {
  width: 940px;
  max-width: 96vw;
}
.mp-modal h3 {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.mp-sub {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-3);
}
.mp-grow {
  flex: 1;
}
.mp-param-wrap {
  max-height: 300px;
}
.mp-fixed {
  font-size: 11px;
  color: var(--text-3);
  line-height: 1.65;
  margin: 5px 0 2px;
}
.mp-fixed b {
  color: var(--text-2);
}
.bad-line {
  color: var(--accent);
}
.mp-run-row {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
  font-size: 12px;
}
.mp-run-row label {
  color: var(--text-3);
}
.mp-run-row input {
  padding: 5px 8px;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  background: #fff;
  font-family: inherit;
  font-size: 12px;
  color: var(--text);
}
.mp-num {
  width: 74px;
  text-align: right;
}
.mp-units {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 9px;
}
.mp-ubtn {
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
.mp-ubtn:hover {
  border-color: var(--primary);
}
.mp-ubtn.on {
  border-color: var(--primary);
  background: var(--primary-soft);
  color: var(--primary);
  font-weight: 600;
}
.mp-ubtn b {
  font-variant-numeric: tabular-nums;
}
.mp-cards {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 7px;
  margin-bottom: 10px;
}
.mp-card {
  padding: 7px 9px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel-2);
}
.mp-card .ck {
  font-size: 11px;
  color: var(--text-3);
  margin-bottom: 2px;
  white-space: nowrap;
}
.mp-card .cv {
  font-size: 15px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.mp-card .cv i {
  font-size: 11px;
  font-weight: 400;
  color: var(--text-3);
  font-style: normal;
}
.mp-card .cs {
  font-size: 10.5px;
  color: var(--text-3);
  margin-top: 2px;
}
.mp-chart-box {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 8px 10px 4px;
  background: #fff;
}
.mp-chart-head {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  margin-bottom: 4px;
  flex-wrap: wrap;
}
.mp-chart-head .lg {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--text-3);
  font-size: 11px;
}
.mp-chart-head .lg i {
  display: inline-block;
  width: 16px;
  height: 2px;
}
.lg-o {
  background: #7b8a99;
}
.lg-s {
  background: #1e6fa8;
}
.mp-chart {
  width: 100%;
  height: 200px;
  display: block;
  background: linear-gradient(#fbfcfe, #fff);
  overflow: visible;
}
.mp-chart .ax {
  font-size: 9.5px;
  fill: #93a1b0;
}
.mp-chart-foot {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: var(--text-3);
  padding-top: 2px;
}
.mp-peaks {
  margin-top: 10px;
}
.mp-peaks-t {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--text-2);
  margin-bottom: 4px;
}
.mp-peaks .tbl-wrap,
.mp-peaks table {
  border: 1px solid var(--line);
  border-radius: 7px;
}
.mp-peaks table {
  overflow: hidden;
}
.mp-wb-wrap {
  max-height: 220px;
}
.mp-warn {
  margin-top: 8px;
  font-size: 11px;
  line-height: 1.7;
  color: var(--amber);
}
</style>
