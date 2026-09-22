<template>
  <div class="step">
    <!-- 参数矩阵 -->
    <div class="sub-sec">
      ① 参数集
      <span class="muted">列为预报单元，表内可直接编辑；出区间标红并拦截运行</span>
      <span class="grow"></span>
      <button class="lnk" :disabled="!!state.busy" @click="reload">重新载入</button>
      <button class="lnk" :disabled="!!state.busy" @click="resetDefault">恢复默认</button>
      <button class="lnk" :disabled="!!state.busy" @click="saveParams">保存为项目参数</button>
    </div>

    <div class="tbl-wrap param-wrap">
      <table class="tbl">
        <thead>
          <tr>
            <th style="width: 200px">参数</th>
            <th style="width: 76px">
              锁定
              <div class="th-sub">固定不率定</div>
            </th>
            <th v-for="u in units" :key="u.code" class="num" style="width: 92px">
              <span class="dot" :style="{ background: colorOf(u.code) }"></span>{{ u.code }}
            </th>
            <th style="width: 104px">率定区间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="p in paramRows" :key="p.key" :class="{ locked: locked[p.key] }">
            <td>
              {{ p.label }}
              <span class="muted">{{ p.key }}</span>
            </td>
            <td class="num">
              <input type="checkbox" :checked="!!locked[p.key]" @change="toggleLock(p.key)" />
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
              <span class="muted">按出口间距估算</span>
            </td>
            <td class="num muted">—</td>
            <td v-for="u in units" :key="u.code" class="num muted">
              {{ fmt(u.params.KE, 1) }} h
            </td>
            <td class="muted small">河长 × 1.3 / 1.5 m·s⁻¹</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="note">
      固定参数：WM = 140 mm（UM 20 / LM 60 / DM 60）· C = 0.15 · IMP = 0.01 · KSS = 0.7 —— 区域经验值，不参与率定。
      勾选「锁定」的参数在率定中保持不变，取表内当前值；XE（马斯京根流量比重因子）仅在有上游来流的单元参与率定。
      <span v-if="lockedCount" class="warn-t">当前锁定 {{ lockedCount }} 个参数。</span>
    </div>
    <div v-if="badCells.length" class="bad-line">
      有 {{ badCells.length }} 个参数超出率定区间，请修正后再运行。
    </div>
    <div v-if="allLocked" class="bad-line">
      所有参数都被锁定，率定将没有可优化的参数 —— 请至少解锁一个。
    </div>

    <!-- 模拟时段 -->
    <div class="sub-sec">② 模拟时段</div>
    <div class="row">
      <label>起</label>
      <input v-model="form.start" type="date" />
      <label>止</label>
      <input v-model="form.end" type="date" />
      <label>预热</label>
      <input v-model.number="form.warmup" type="number" min="0" step="30" class="num-in" />
      <span class="muted">天（预热期不计入指标）</span>
      <span class="grow"></span>
      <button class="btn" :disabled="!ready || !!state.busy" @click="doSimulate">
        运行模拟（当前参数）
      </button>
    </div>

    <!-- 率定设置 -->
    <div class="sub-sec">③ 率定设置</div>
    <div class="row">
      <label>率定期占比</label>
      <input v-model.number="form.split" type="number" min="0.3" max="0.95" step="0.05" class="num-in" />
      <span class="muted">其余为验证期（只评估不调参）</span>
      <label style="margin-left: 10px">最大评估</label>
      <input
        id="cal-max-evals"
        v-model.number="form.maxEvals"
        type="number"
        min="200"
        step="200"
        class="num-in wide"
      />
      <label>随机种子</label>
      <input v-model.number="form.seed" type="number" min="0" step="1" class="num-in" />
      <span class="grow"></span>
      <button id="cal-start" class="btn primary" :disabled="!canCalibrate" @click="doCalibrate">
        启动率定
      </button>
    </div>
    <div class="note">
      SCE-UA 链式率定：按排水序自上而下逐单元优化。单次目标评估约 4.6 ms，
      <b>{{ evalHint }}</b
      >。目标函数 F = (1−NSE) + 0.3·|PBIAS|/100 + 0.1·RMSE/std(obs)，在率定期上计算。
    </div>

    <div class="step-foot">
      <span class="muted">
        <template v-if="state.simResult">
          最近一次模拟：{{ simMeta }}
        </template>
        <template v-else>尚未运行模拟</template>
      </span>
      <span class="grow"></span>
      <button class="btn" :disabled="!state.simResult" @click="$emit('goto', 'result', { view: 'sim' })">
        查看模拟结果
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from 'vue'
import {
  loadCalibrationParams,
  loadCalibrationRuns,
  loadTimeseries,
  runSimulation,
  saveCalibrationParams,
  startCalibration,
  state,
  subbasinColorOf,
  toast
} from '../../store'

const emit = defineEmits(['goto', 'started'])

const form = reactive({ start: '', end: '', warmup: 0, split: 0.7, maxEvals: 3000, seed: 7 })
const edited = reactive({})
const locked = reactive({})

const cal = computed(() => state.calibration)
const units = computed(() => (cal.value && cal.value.units) || [])
const ready = computed(() => units.value.length > 0)

const paramRows = computed(() => {
  const sp = (cal.value && cal.value.spec) || {}
  return Object.entries(sp)
    .filter(([, v]) => v.calibrate !== false)
    .map(([key, v]) => ({ key, ...v }))
})

const lockedCount = computed(() => Object.keys(locked).filter((k) => locked[k]).length)
const allLocked = computed(() => paramRows.value.length > 0 && lockedCount.value >= paramRows.value.length)

const evalHint = computed(() => {
  const n = Math.max(1, Math.round(form.maxEvals || 0))
  const per = units.value.length
  const sec = (n * per * 0.0046).toFixed(0)
  return `${n} 次评估 × ${per} 个单元 ≈ ${sec} 秒`
})

const simMeta = computed(() => {
  const r = state.simResult
  if (!r) return ''
  return `${r.period.start} ~ ${r.period.end} · ${r.n_steps} 时段 · ${r.dt_s / 3600} h 步长`
})

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
function isBad(code, p) {
  const v = edited[code] ? edited[code][p.key] : null
  return v === null || !Number.isFinite(v) || v < p.min || v > p.max
}
const badCells = computed(() => {
  const out = []
  for (const u of units.value) {
    for (const p of paramRows.value) if (isBad(u.code, p)) out.push(`${u.code}.${p.key}`)
  }
  return out
})
function toggleLock(key) {
  locked[key] = !locked[key]
}

// ---------------- 操作
async function reload() {
  await loadCalibrationParams(true)
  syncEdited()
}
async function resetDefault() {
  if (!confirm('恢复为默认参数（清空已保存的项目参数集）？')) return
  await saveCalibrationParams(null, { reset: true })
  state.simResult = null
  for (const k of Object.keys(locked)) delete locked[k]
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

function paramsOf() {
  const params = {}
  for (const u of units.value) params[u.code] = { ...edited[u.code] }
  return params
}
function periodOf() {
  const period = {}
  if (form.start) period.start = `${form.start} 00:00`
  if (form.end) period.end = `${form.end} 00:00`
  if (form.warmup) period.warmup_days = Number(form.warmup)
  return period
}

async function doSimulate() {
  if (badCells.value.length) {
    toast(`有 ${badCells.value.length} 个参数超出区间`, 'warn')
    return
  }
  try {
    const r = await runSimulation({ params: paramsOf(), period: periodOf() })
    const okN = (r.units || []).filter((u) => (u.metrics || {}).nse >= 0.5).length
    toast(`模拟完成：${r.units.length} 个单元，NSE ≥ 0.5 的单元 ${okN} 个`, okN ? 'ok' : 'warn', 4200)
    emit('goto', 'result', { view: 'sim' })
  } catch (e) {
    /* store 已提示 */
  }
}

const canCalibrate = computed(
  () => ready.value && !badCells.value.length && !allLocked.value && !state.busy && !isRunning.value
)
const isRunning = computed(() => {
  const st = state.calib.status
  return !!(st && (st.status === 'running' || st.phase === 'queued') && st.phase !== 'finished')
})

async function doCalibrate() {
  if (badCells.value.length || allLocked.value) return
  const lock = {}
  for (const key of Object.keys(locked)) {
    if (!locked[key]) continue
    for (const u of units.value) {
      lock[u.code] = lock[u.code] || {}
      lock[u.code][key] = edited[u.code][key]
    }
  }
  try {
    await startCalibration({
      period: periodOf(),
      split: Number(form.split) || 0.7,
      max_evals: Number(form.maxEvals) || 3000,
      seed: Number(form.seed) || 0,
      lock
    })
    await loadCalibrationRuns()
    emit('started')
    emit('goto', 'run', {})
  } catch (e) {
    toast(e.message || '启动率定失败', 'warn')
  }
}

// 打开面板时初始化
watch(
  () => state.calibration,
  async () => {
    const sp = (state.timeseries && state.timeseries.summary && state.timeseries.summary.span) || null
    if (sp && sp.start && !form.start) {
      form.start = sp.start.slice(0, 10)
      form.end = sp.end.slice(0, 10)
    }
  },
  { immediate: true }
)

function colorOf(code) {
  const list = (state.subbasins && state.subbasins.subbasins) || []
  const sb = list.find((x) => x.code === code)
  return sb ? subbasinColorOf(sb) : '#93a1b0'
}
function fmt(v, n = 2) {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return '—'
  return Number(v).toFixed(n)
}

defineExpose({ reload, ensurePeriod: async () => loadTimeseries(false) })
</script>

<style scoped>
.step {
  font-size: 12px;
}
.param-wrap {
  /* 压住表高，让「运行模拟 / 启动率定」尽量不落出首屏（9 行参数可滚动查看） */
  max-height: 232px;
}
.th-sub {
  font-size: 10px;
  font-weight: 400;
  color: var(--text-3);
}
.grow {
  flex: 1;
}
.note {
  font-size: 11px;
  color: var(--text-3);
  line-height: 1.7;
  margin: 5px 0 2px;
}
.note b {
  color: var(--text-2);
}
.bad-line {
  font-size: 11.5px;
  color: var(--accent);
  margin-top: 4px;
}
.row {
  display: flex;
  align-items: center;
  gap: 7px;
  flex-wrap: wrap;
  font-size: 12px;
}
.row label {
  color: var(--text-3);
}
.row input {
  padding: 5px 8px;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  background: #fff;
  font-family: inherit;
  font-size: 12px;
  color: var(--text);
}
.num-in {
  width: 74px;
  text-align: right;
}
.num-in.wide {
  width: 92px;
}
tr.locked td {
  background: #fbfaf5;
}
.step-foot {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 14px;
  padding-top: 10px;
  border-top: 1px solid var(--line);
}
.step-foot .muted {
  font-size: 11px;
}
</style>
