<template>
  <div class="step">
    <!-- 无任务 -->
    <div v-if="!rid" class="empty-line">
      还没有率定任务。到「② 配置」设好参数与评估次数后点「启动率定」，这里会实时显示进度与收敛曲线。
    </div>

    <template v-else>
      <div class="sub-sec">
        ① 当前任务
        <span class="muted mono">{{ rid }}</span>
        <span class="grow"></span>
        <span class="badge" :class="stateClass">{{ stateText }}</span>
      </div>

      <div class="cards">
        <div class="card">
          <div class="ck">当前单元</div>
          <div class="cv">{{ cur.unit || '—' }}</div>
          <div class="cs">{{ doneList.length }} / {{ plan.length }} 单元已完成</div>
        </div>
        <div class="card">
          <div class="ck">代数 / 评估次数</div>
          <div class="cv">{{ cur.gen || 0 }} <i>/ {{ cur.evals || 0 }}</i></div>
          <div class="cs">{{ (cur.free_keys || []).length }} 个自由参数</div>
        </div>
        <div class="card">
          <div class="ck">当前最优目标 F</div>
          <div class="cv">{{ fmt(cur.best_f, 5) }}</div>
          <div class="cs">越小越好（0 为完美）</div>
        </div>
        <div class="card">
          <div class="ck">已用时</div>
          <div class="cv">{{ cur.elapsed_s == null ? '—' : cur.elapsed_s }} <i>s</i></div>
          <div class="cs">共 {{ totalEvals }} 次评估 · 约 {{ msPerEval }} ms/次</div>
        </div>
        <div class="card">
          <div class="ck">阶段</div>
          <div class="cv sm">{{ phaseText }}</div>
          <div class="cs">{{ cur.reason || (cur.unit ? '单元 ' + cur.unit : '—') }}</div>
        </div>
      </div>

      <!-- 单元进度清单 -->
      <div v-if="plan.length" class="units">
        <div v-for="c in plan" :key="c" class="unit-chip" :class="unitState(c)">
          <span class="dot" :style="{ background: colorOf(c) }"></span>
          {{ c }}
          <b>{{ unitLabel(c) }}</b>
        </div>
      </div>

      <div v-if="error" class="err-box">
        <b>任务出错：</b>{{ error }}
        <div v-if="cur.traceback" class="tb">{{ cur.traceback }}</div>
      </div>

      <!-- 收敛曲线 -->
      <div class="sub-sec">
        ② 收敛曲线
        <span class="muted">SCE-UA 目标函数逐代最优值；点击图例可只看单个单元</span>
        <span class="grow"></span>
        <span class="muted">{{ curveInfo }}</span>
      </div>
      <div class="chart-box">
        <EChart :option="convOption" :height="228" />
      </div>

      <div class="step-foot">
        <span class="muted">
          <template v-if="running">率定在后台线程执行，可关闭此窗口，稍后回来查看</template>
          <template v-else-if="finished && hasResult">任务已结束，结果已就绪</template>
          <template v-else>{{ stateText }}</template>
        </span>
        <span class="grow"></span>
        <button v-if="running" class="btn danger" @click="doStop">终止任务</button>
        <button
          v-if="hasResult"
          class="btn primary"
          @click="$emit('goto', 'result', { view: 'calib' })"
        >
          查看率定结果
        </button>
      </div>
    </template>

    <!-- 历次任务（无当前任务时也显示） -->
    <div class="sub-sec">
      ③ 历次任务
      <span class="muted">共 {{ runs.length }} 次（新→旧）；点击行可载入其结果</span>
      <span class="grow"></span>
      <button class="lnk" @click="reloadRuns">刷新</button>
    </div>
    <div v-if="!runs.length" class="empty-line small">暂无记录</div>
    <div v-else class="tbl-wrap runs-wrap">
      <table class="tbl">
        <thead>
          <tr>
            <th style="width: 92px">任务</th>
            <th style="width: 76px">状态</th>
            <th style="width: 142px">开始</th>
            <th class="num" style="width: 62px">用时</th>
            <th class="num" style="width: 70px">评估</th>
            <th style="width: 96px">已完成单元</th>
            <th class="num" style="width: 86px">最优 F</th>
            <th style="width: 74px">参数</th>
            <th>结果</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="r in runs"
            :key="r.run_id"
            class="row-click"
            :class="{ on: r.run_id === rid }"
            @click="openRun(r)"
          >
            <td class="mono small">{{ String(r.run_id).slice(0, 8) }}</td>
            <td class="small">
              <span :class="runStatusClass(r)">{{ runStatusText(r) }}</span>
            </td>
            <td class="small muted">{{ (r.started_at || '').replace('T', ' ').slice(0, 16) }}</td>
            <td class="num">{{ r.elapsed_s == null ? '—' : r.elapsed_s }}</td>
            <td class="num">{{ r.evals || 0 }}</td>
            <td class="small muted">
              {{ (r.units_done || []).length }} / {{ planCount(r) }}
            </td>
            <td class="num">{{ fmt(r.best_f, 5) }}</td>
            <td class="small muted">{{ r.config && r.config.max_evals }}</td>
            <td class="small">
              <span v-if="r.has_result" class="ok-t">已生成</span>
              <span v-else class="muted">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import EChart from './EChart.vue'
import { convergenceOption } from '../../charts'
import { loadCalibrationRuns, pollCalibration, state, stopCalibration, subbasinColorOf } from '../../store'

defineEmits(['goto'])

const rid = computed(() => state.calib.rid || '')
const cur = computed(() => state.calib.status || {})
const runs = computed(() => state.calib.runs || [])
const result = computed(() => state.calib.result || null)
const convCurve = computed(() => (state.calib.convergence && state.calib.convergence.curve) || [])
const error = computed(() => state.calib.error || cur.value.error || '')

// ---------------------------------------------------------------- 状态判定
const running = computed(() => {
  const ph = cur.value.phase
  return cur.value.status === 'running' && ph !== 'finished' && ph !== 'failed'
})
const finished = computed(
  () => cur.value.phase === 'finished' || cur.value.phase === 'failed' || ['done', 'stopped', 'failed'].includes(cur.value.status)
)
const hasResult = computed(() => !!result.value)

const PHASE_TEXT = {
  queued: '排队中',
  prepare: '准备数据',
  start: '开始率定',
  run: '率定中',
  done: '单元完成',
  skip: '跳过单元',
  stopped: '已终止',
  finished: '已结束',
  failed: '失败'
}
const phaseText = computed(() => PHASE_TEXT[cur.value.phase] || cur.value.phase || '—')

const stateText = computed(() => {
  if (running.value) return '运行中'
  const s = cur.value.status
  if (s === 'done') return '已完成'
  if (s === 'stopped') return '已终止'
  if (s === 'failed') return '失败'
  if (finished.value) return '已结束'
  return '等待中'
})
const stateClass = computed(() => {
  if (running.value) return 'run'
  const s = cur.value.status
  if (s === 'failed') return 'bad'
  if (s === 'stopped') return 'warn'
  return 'ok'
})

const plan = computed(() => {
  const p = cur.value.units_plan || (state.calib.convergence && state.calib.convergence.units_plan) || []
  if (p.length) return p
  return ((state.calibration && state.calibration.units) || []).map((u) => u.code)
})

const doneList = computed(() => {
  const d = cur.value.units_done
  if (d && d.length) return d
  const units = (result.value && result.value.units) || []
  return units.filter((u) => u.status !== 'not_run').map((u) => u.code)
})

function unitState(c) {
  if (doneList.value.includes(c)) return 'done'
  if (running.value && cur.value.unit === c) return 'on'
  return 'idle'
}
function unitLabel(c) {
  const s = unitState(c)
  return s === 'done' ? '完成' : s === 'on' ? '进行中' : '待运行'
}
function colorOf(code) {
  const sb = ((state.calibration && state.calibration.units) || []).find((u) => u.code === code)
  return sb ? subbasinColorOf(sb) : '#93a1b0'
}

// ---------------------------------------------------------------- 收敛曲线
const totalEvals = computed(() => {
  const c = convCurve.value
  if (!c.length) return 0
  return c.reduce((a, s) => {
    const pts = s.points || []
    return a + (pts.length ? Number(pts[pts.length - 1].evals || 0) : 0)
  }, 0)
})
const msPerEval = computed(() => {
  const e = totalEvals.value
  const t = cur.value.elapsed_s
  if (!e || t == null) return '—'
  return Math.round((t * 1000) / e)
})
const curveInfo = computed(() => {
  const c = convCurve.value
  if (!c.length) return running.value ? '等待第一代…' : '暂无曲线'
  const gens = c.reduce((a, s) => a + (s.points || []).length, 0)
  return `${c.length} 个单元 · 共 ${gens} 代`
})
const convOption = computed(() => convergenceOption({ curve: convCurve.value }))

// ---------------------------------------------------------------- 操作
function fmt(v, n = 3) {
  if (v == null || !Number.isFinite(Number(v))) return '—'
  return Number(v).toFixed(n)
}
function planCount(r) {
  const u = (r.config && r.config.units) || []
  return u.length || plan.value.length
}
function runStatusText(r) {
  if (r.phase === 'failed' || r.status === 'failed') return '失败'
  if (r.status === 'running') return '运行中'
  if (r.status === 'stopped') return '已终止'
  if (r.status === 'done') return '已完成'
  return '—'
}
function runStatusClass(r) {
  const t = runStatusText(r)
  if (t === '失败') return 'bad-t'
  if (t === '运行中') return 'warn-t'
  if (t === '已完成') return 'ok-t'
  return 'muted'
}

async function reloadRuns() {
  await loadCalibrationRuns()
}

/** 点击历次任务行：载入该次任务的进度与结果。 */
async function openRun(r) {
  if (r.run_id === rid.value && (result.value || running.value)) return
  state.calib.rid = r.run_id
  state.calib.status = null
  state.calib.convergence = null
  state.calib.result = null
  state.calib.error = ''
  await pollCalibration()
  // 若该任务仍在运行，恢复轮询
  if (!state.calib.poll && running.value) {
    state.calib.poll = setInterval(() => pollCalibration(), 1200)
  }
}

async function doStop() {
  if (!confirm('确定终止当前率定任务？已完成单元的参数会保留。')) return
  await stopCalibration()
}
</script>

<style scoped>
.step {
  font-size: 12px;
}
.cards {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 7px;
  margin-bottom: 12px;
}
.card {
  padding: 8px 10px;
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
  font-size: 17px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.card .cv.sm {
  font-size: 13px;
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
.mono {
  font-family: var(--mono, ui-monospace, monospace);
}
.grow {
  flex: 1;
}

/* 状态徽标 */
.badge {
  padding: 1px 8px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 600;
}
.badge.ok {
  background: #eaf6ef;
  color: var(--green);
}
.badge.warn {
  background: #fdf6e7;
  color: var(--amber);
}
.badge.bad {
  background: #fdf1ef;
  color: var(--accent);
}
.badge.run {
  background: var(--primary-soft);
  color: var(--primary);
  animation: runpulse 1.4s ease-in-out infinite;
}
@keyframes runpulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.4;
  }
}

/* 单元进度 chips */
.units {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 9px;
}
.unit-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border: 1px solid var(--line-strong);
  border-radius: 20px;
  background: #fff;
  color: var(--text-2);
  font-size: 11.5px;
}
.unit-chip .dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
}
.unit-chip b {
  font-weight: 600;
  color: var(--text-3);
}
.unit-chip.done {
  border-color: #c6e6d4;
  background: #f5fbf7;
}
.unit-chip.done b {
  color: var(--green);
}
.unit-chip.on {
  border-color: var(--primary);
  background: var(--primary-soft);
  color: var(--primary);
}
.unit-chip.on b {
  color: var(--primary);
}

/* 错误框 */
.err-box {
  padding: 7px 10px;
  border: 1px solid #eccac5;
  border-radius: 8px;
  background: #fdf1ef;
  color: var(--accent);
  font-size: 11.5px;
  margin-bottom: 10px;
}
.err-box .tb {
  margin-top: 5px;
  font-family: var(--mono, ui-monospace, monospace);
  font-size: 10.5px;
  white-space: pre-wrap;
  max-height: 120px;
  overflow: auto;
  color: var(--text-2);
}

.chart-box {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 8px 10px 2px;
  background: #fff;
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

/* 历次任务表 */
.runs-wrap {
  max-height: 208px;
  overflow: auto;
}
.row-click {
  cursor: pointer;
}
.row-click:hover td {
  background: var(--primary-soft);
}
.row-click.on td {
  background: #eef5fb;
}
</style>
