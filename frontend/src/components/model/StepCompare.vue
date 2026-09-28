<template>
  <div class="step">
    <!-- ① 对比设置 -->
    <div class="sub-sec">
      ① 对比设置
      <span class="muted">各模型用相同预算与分期独立链式率定；参数互不通用，只比过程线指标</span>
    </div>
    <div class="mchips">
      <button
        v-for="m in models"
        :key="m.key"
        class="mchip"
        :class="{ on: selected.includes(m.key) }"
        :disabled="running"
        @click="toggleModel(m.key)"
      >
        {{ m.name }}
        <i>{{ m.n_calib }} 参</i>
      </button>
    </div>
    <div class="row" style="margin-top: 6px">
      <label>每单元评估次数</label>
      <input id="cmp-evals" v-model.number="form.maxEvals" type="number" min="60" max="5000" step="100" class="num-in" />
      <label style="margin-left: 10px">验证期占比</label>
      <input id="cmp-split" v-model.number="form.split" type="number" min="0.3" max="0.95" step="0.05" class="num-in" />
      <span class="grow"></span>
      <button v-if="!running" id="cmp-run" class="btn primary" :disabled="!canRun" @click="run">
        {{ starting ? '启动中…' : '运行对比' }}
      </button>
      <button v-else class="btn" @click="onStop">终止</button>
    </div>
    <div class="note">
      实测流量为共同目标。若观测由某模型生成（演示数据即如此），该模型拟合最好属预期——
      其余模型与它的差距就是「模型结构不匹配」的代价。高 NSE 不等于参数可唯一辨识（参数补偿效应），
      参数个体请配合「结果」页的 GLUE 置信带解读。
    </div>

    <!-- ② 进度 -->
    <div v-if="doc && ['running', 'queued'].includes(doc.status)" class="sub-sec">
      ② 进度
      <span class="muted">已完成 {{ doneCount }}/{{ modelKeys.length }} 个模型 · 已耗时 {{ fmtS(doc.elapsed_s) }}</span>
      <span class="grow"></span>
      <span class="muted small">cid {{ doc.cid }}</span>
    </div>
    <div v-if="doc && ['running', 'queued'].includes(doc.status)" class="mchips">
      <div v-for="(m, k) in doc.models" :key="k" class="mstat" :class="m.status">
        <b>{{ m.name }}</b>
        <span v-if="m.status === 'running' && m.progress">
          {{ m.progress.unit || '…' }} · 第 {{ m.progress.gen || 0 }} 代 · {{ m.progress.evals || 0 }} 次
        </span>
        <span v-else-if="m.status === 'done'">NSE {{ fmtN(m.calib_nse_range[0]) }}~{{ fmtN(m.calib_nse_range[1]) }}</span>
        <span v-else>{{ statusText(m.status) }}</span>
      </div>
    </div>

    <!-- ③ 结果 -->
    <template v-if="doc && !['running', 'queued'].includes(doc.status)">
      <div class="sub-sec">
        ③ 对比结果
        <span class="muted">
          {{ statusText(doc.status) }} · 总耗时 {{ fmtS(doc.elapsed_s) }} ·
          实测目标：{{ (doc.config && doc.config.max_evals) || '—' }} evals/单元
        </span>
        <span class="grow"></span>
        <a v-if="doc.status === 'done'" class="lnk" :href="exportUrl" download>导出指标 CSV</a>
      </div>
      <div class="tbl-wrap">
        <table class="tbl">
          <thead>
            <tr>
              <th style="width: 150px">模型</th>
              <th class="num">率定期 NSE（最差~最优）</th>
              <th class="num">验证期 NSE（最差~最优）</th>
              <th class="num" style="width: 86px">耗时</th>
              <th style="width: 76px">状态</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="k in modelKeys" :key="k">
              <td><span class="dot" :style="{ background: colorOf(k) }"></span>{{ (doc.models[k] || {}).name }}</td>
              <td class="num">{{ nseRange((doc.models[k] || {}).calib_nse_range) }}</td>
              <td class="num">{{ nseRange((doc.models[k] || {}).valid_nse_range) }}</td>
              <td class="num">{{ fmtS((doc.models[k] || {}).elapsed_s) }}</td>
              <td><span :class="(doc.models[k] || {}).status === 'done' ? 'ok-t' : 'warn-t'">{{ statusText((doc.models[k] || {}).status) }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-if="barOption" class="chart-box">
        <EChart :option="barOption" :height="200" />
      </div>
      <div v-if="failedModels.length" class="note warn">
        {{ failedModels }} 率定失败：{{ (doc.models[failedModels.split('、')[0]] || {}).error || '详见后端日志' }}
      </div>
    </template>

    <!-- ④ 历次对比 -->
    <div class="sub-sec">
      ④ 历次对比
      <span class="grow"></span>
      <button class="lnk" @click="reloadRuns">刷新</button>
    </div>
    <div v-if="!(cmp.runs || []).length" class="muted small" style="padding: 4px 2px">
      还没有对比任务。选择模型后点「运行对比」。
    </div>
    <div v-else class="hist">
      <button
        v-for="r in cmp.runs"
        :key="r.cid"
        class="hrow"
        :class="{ on: r.cid === cmp.cid }"
        @click="open(r.cid)"
      >
        <span class="dot" :class="r.status === 'done' ? 'ok' : r.status === 'running' ? 'pulse' : ''"></span>
        <b>{{ fmtT(r.created_at) }}</b>
        <span class="muted small">{{ Object.keys(r.models || {}).length }} 模型 · {{ r.config && r.config.max_evals }} evals/单元 ·
          {{ statusText(r.status) }}{{ r.elapsed_s ? ` · ${fmtS(r.elapsed_s)}` : '' }}</span>
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import EChart from './EChart.vue'
import { api } from '../../api'
import {
  loadCompareRuns,
  openCompare,
  pollCompare,
  startCompare,
  state,
  stopCompare,
  toast
} from '../../store'

const COLORS = { xaj: '#2563eb', gr4j: '#d97706', tank: '#059669', hbv: '#dc2626' }

const form = reactive({ maxEvals: 400, split: 0.7 })
const selected = ref([])
const starting = ref(false)

const cmp = computed(() => state.calib.cmp)
const doc = computed(() => state.calib.cmp.doc)
const models = computed(() => state.models || [])
const modelKeys = computed(() => (doc.value && Object.keys(doc.value.models)) || [])

onMounted(async () => {
  if (!selected.value.length) selected.value = models.value.map((m) => m.key)
  await loadCompareRuns()
  // 上次会话若有运行中的任务（如刷新页面），恢复轮询
  const d = state.calib.cmp.doc
  if (d && ['running', 'queued'].includes(d.status) && !state.calib.cmp.poll) {
    state.calib.cmp.poll = setInterval(() => pollCompare(), 2000)
  }
})

const running = computed(() => !!doc.value && ['running', 'queued'].includes(doc.value.status))
const canRun = computed(() => selected.value.length >= 2 && !running.value && !starting.value)
const doneCount = computed(() =>
  modelKeys.value.filter((k) => ['done', 'stopped', 'failed'].includes((doc.value.models[k] || {}).status)).length
)
const failedModels = computed(() =>
  modelKeys.value.filter((k) => (doc.value.models[k] || {}).status === 'failed').join('、')
)

function toggleModel(k) {
  const i = selected.value.indexOf(k)
  if (i >= 0) selected.value.splice(i, 1)
  else selected.value.push(k)
}

async function run() {
  starting.value = true
  try {
    await startCompare({
      models: [...selected.value],
      max_evals: Number(form.maxEvals) || 400,
      split: Number(form.split) || 0.7
    })
    toast('对比已启动，逐模型率定中…', 'ok', 3000)
  } catch (e) {
    toast(e.message || '启动失败', 'warn', 4200)
  } finally {
    starting.value = false
  }
}

async function onStop() {
  try {
    await stopCompare()
  } catch (e) {
    toast(e.message || '终止失败', 'warn')
  }
}

async function open(cid) {
  await openCompare(cid)
}

async function reloadRuns() {
  await loadCompareRuns(true)
}

function statusText(s) {
  return (
    {
      queued: '排队中',
      running: '率定中',
      done: '已完成',
      stopped: '已终止',
      failed: '失败',
      not_run: '未运行',
      pending: '待运行'
    }[s] || s || '—'
  )
}

function fmtS(v) {
  return v == null ? '—' : `${Number(v).toFixed(1)}s`
}

function fmtN(v) {
  return v == null ? '—' : Number(v).toFixed(3)
}

function fmtT(t) {
  return t ? String(t).replace('T', ' ').slice(5, 16) : '—'
}

function nseRange(r) {
  if (!r || r[0] == null) return '—'
  return `${Number(r[0]).toFixed(3)} ~ ${Number(r[1]).toFixed(3)}`
}

function colorOf(k) {
  return COLORS[k] || '#8a94a3'
}

const exportUrl = computed(() => {
  const pid = state.project && state.project.id
  return doc.value && pid ? api.calibrationCompareExportUrl(pid, doc.value.cid) : '#'
})

// 率定期最差单元 NSE 条形对比（只画有指标的模型）
const barOption = computed(() => {
  if (!doc.value) return null
  const rows = modelKeys.value
    .map((k) => ({ k, m: doc.value.models[k] || {} }))
    .filter((x) => x.m.calib_nse_range && x.m.calib_nse_range[0] != null)
    .map((x) => ({ name: x.m.name, value: Number(x.m.calib_nse_range[0].toFixed(4)), color: colorOf(x.k) }))
  if (!rows.length) return null
  return {
    grid: { left: 8, right: 40, top: 6, bottom: 6, containLabel: true },
    xAxis: { type: 'value', min: 0.5, max: 1, axisLabel: { fontSize: 10 } },
    yAxis: { type: 'category', data: rows.map((r) => r.name), axisLabel: { fontSize: 11 } },
    tooltip: { trigger: 'axis' },
    series: [
      {
        type: 'bar',
        data: rows.map((r) => ({ value: r.value, itemStyle: { color: r.color, borderRadius: 3 } })),
        barWidth: 14,
        label: { show: true, position: 'right', fontSize: 10, formatter: (p) => Number(p.value).toFixed(3) }
      }
    ]
  }
})
</script>

<style scoped>
.step {
  font-size: 12px;
}
.row {
  display: flex;
  align-items: center;
  gap: 6px;
}
.row label {
  color: var(--text-2);
  white-space: nowrap;
}
.num-in {
  width: 84px;
}
.mchips {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin: 6px 0;
}
.mchip {
  border: 1px solid var(--border-1, #ddd);
  border-radius: 14px;
  background: var(--bg-2, #fafafa);
  padding: 4px 12px;
  font-size: 12px;
  cursor: pointer;
  color: var(--text-1, #333);
}
.mchip i {
  font-style: normal;
  font-size: 10px;
  color: var(--text-3, #999);
  margin-left: 4px;
}
.mchip.on {
  border-color: var(--accent, #3b82f6);
  background: var(--accent-weak, rgba(59, 130, 246, 0.08));
}
.mchip:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.mstat {
  border: 1px solid var(--border-1, #ddd);
  border-radius: 8px;
  padding: 5px 10px;
  font-size: 11px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 150px;
}
.mstat.running {
  border-color: var(--accent, #3b82f6);
}
.mstat.done {
  border-color: #22c55e66;
}
.mstat.failed {
  border-color: #ef444466;
}
.mstat span {
  color: var(--text-3, #999);
}
.tbl-wrap {
  max-height: 240px;
  overflow: auto;
  margin: 6px 0;
}
.chart-box {
  margin: 8px 0;
  border: 1px solid var(--border-1, #e5eaf0);
  border-radius: 8px;
  padding: 6px;
}
.hist {
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 180px;
  overflow: auto;
}
.hrow {
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid transparent;
  background: transparent;
  border-radius: 6px;
  padding: 5px 8px;
  cursor: pointer;
  font-size: 12px;
  text-align: left;
  color: var(--text-1, #333);
}
.hrow:hover {
  background: var(--bg-2, #f3f4f6);
}
.hrow.on {
  border-color: var(--accent, #3b82f6);
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--text-3, #999);
  display: inline-block;
}
.dot.ok {
  background: #22c55e;
}
.dot.pulse {
  background: var(--accent, #3b82f6);
  animation: pulse 1.2s infinite;
}
@keyframes pulse {
  50% {
    opacity: 0.35;
  }
}
.note {
  margin: 8px 0;
  font-size: 11px;
  color: var(--text-2, #666);
  background: var(--bg-2, #f8fafc);
  border: 1px solid var(--border-1, #e5eaf0);
  border-radius: 8px;
  padding: 8px 10px;
  line-height: 1.7;
}
.note.warn {
  background: #fffbeb;
  border-color: #fde68a;
}
.ok-t {
  color: #16a34a;
}
.warn-t {
  color: #d97706;
}
</style>
