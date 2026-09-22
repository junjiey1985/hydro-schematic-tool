<template>
  <div v-if="show" class="mask" @click.self="$emit('close')">
    <div class="modal mc-modal">
      <h3>
        模型模拟与参数率定
        <span class="mc-sub">
          新安江三水源 + 马斯京根 · 逐单元演算区间产流 → 沿上下游链演算至各站出口 · SCE-UA 链式率定
        </span>
      </h3>

      <div class="tabs">
        <button
          v-for="t in tabs"
          :key="t.key"
          class="tab"
          :class="{ on: tab === t.key }"
          @click="selectTab(t.key)"
        >
          <span class="tn">{{ t.no }}</span>
          {{ t.label }}
          <span v-if="t.badge" class="tbadge" :class="t.badgeClass"></span>
        </button>
      </div>

      <div class="body">
        <div v-if="blockReason" class="hintbar warn-bar">{{ blockReason }}</div>

        <template v-else>
          <StepCheck v-if="tab === 'check'" @goto="go" />
          <StepConfig v-else-if="tab === 'config'" ref="cfgRef" @goto="go" @started="tab = 'run'" />
          <StepRun v-else-if="tab === 'run'" @goto="go" />
          <StepResult v-else :view="resultView" @goto="go" />
        </template>
      </div>

      <div class="foot">
        <span class="muted" style="margin-right: auto">
          <template v-if="state.calib.status && state.calib.status.status === 'running'">
            率定任务在后台运行中（{{ state.calib.rid }}）—— 可关闭窗口，回到「运行」查看进度
          </template>
          <template v-else-if="state.simResult">
            参数集按单元持久化；「结果」页可对比模拟与率定两套过程线
          </template>
        </span>
        <button class="btn" :disabled="!!state.busy" @click="reload">重新载入</button>
        <button class="btn primary" @click="$emit('close')">关闭</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'
import StepCheck from './model/StepCheck.vue'
import StepConfig from './model/StepConfig.vue'
import StepRun from './model/StepRun.vue'
import StepResult from './model/StepResult.vue'
import {
  loadCalibrationParams,
  loadCalibrationRuns,
  loadTimeseries,
  state
} from '../store'

const props = defineProps({ show: { type: Boolean, default: false } })
defineEmits(['close'])

const tab = ref('config')
const resultView = ref('sim')
const cfgRef = ref(null)

const running = computed(() => {
  const st = state.calib.status
  return !!(st && st.status === 'running' && st.phase !== 'finished')
})

const cov = computed(() => {
  const c = state.calibration
  if (!c) return null
  const t = state.timeseries || {}
  return (t.coverage && t.coverage.summary) || null
})

const tabs = computed(() => [
  {
    key: 'check',
    no: '①',
    label: '数据检查',
    badge: checkBadge.value.show,
    badgeClass: checkBadge.value.cls
  },
  { key: 'config', no: '②', label: '配置' },
  {
    key: 'run',
    no: '③',
    label: '运行',
    badge: running.value,
    badgeClass: running.value ? 'pulse' : 'ok'
  },
  {
    key: 'result',
    no: '④',
    label: '结果',
    badge: !!(state.simResult || state.calib.result),
    badgeClass: state.calib.result ? 'ok' : 'idle'
  }
])

const checkBadge = computed(() => {
  const s = (state.timeseries && state.timeseries.summary) || {}
  const c = s.counts || {}
  const sm = cov.value || {}
  if (!c.rain || !c.evap || !c.flow) return { show: true, cls: 'warn' }
  if (sm.borrow) return { show: true, cls: 'warn' }
  return { show: true, cls: 'ok' }
})

const blockReason = computed(() => {
  if (!state.project) return '尚未打开项目。'
  const units = (state.calibration && state.calibration.units) || []
  if (state.calibration && !units.length) {
    return '尚未划分子流域：模型按预报单元组织，请先在顶栏完成「子流域划分」。'
  }
  if (state.calibration && !units.length) return '尚未划分子流域。'
  return ''
})

function go(target, opts = {}) {
  if (target === 'result') resultView.value = opts.view || 'sim'
  tab.value = target
}

/** 点「结果」时自动选到有数据的那个数据源，避免看到空态。 */
function selectTab(key) {
  if (key === 'result') {
    if (state.calib.result) resultView.value = 'calib'
    else if (state.simResult) resultView.value = 'sim'
    else resultView.value = 'sim'
  }
  tab.value = key
}

async function reload() {
  await loadCalibrationParams(true)
  await loadTimeseries(true)
  await loadCalibrationRuns()
  if (cfgRef.value && cfgRef.value.reload) await cfgRef.value.reload()
}

watch(
  () => props.show,
  async (v) => {
    if (!v) return
    await loadCalibrationParams(true)
    await loadTimeseries(false)
    await loadCalibrationRuns()
  }
)
</script>

<style scoped>
.mc-modal {
  width: 1020px;
  max-width: 97vw;
}
.mc-modal h3 {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}
.mc-sub {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-3);
}
.tabs {
  display: flex;
  gap: 2px;
  padding: 0 18px;
  border-bottom: 1px solid var(--line);
}
.tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  border: none;
  border-bottom: 2px solid transparent;
  border-radius: 0;
  background: transparent;
  color: var(--text-3);
  font-size: 13px;
}
.tab:hover {
  color: var(--text-2);
}
.tab.on {
  color: var(--primary);
  border-bottom-color: var(--primary);
  font-weight: 600;
}
.tab .tn {
  font-size: 12px;
  opacity: 0.8;
}
.tbadge {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--line-strong);
}
.tbadge.ok {
  background: #63b76a;
}
.tbadge.warn {
  background: #e0a83c;
}
.tbadge.idle {
  background: var(--line-strong);
}
.tbadge.pulse {
  background: var(--primary);
  animation: tbpulse 1.2s ease-in-out infinite;
}
@keyframes tbpulse {
  0%,
  100% {
    opacity: 1;
  }
  50% {
    opacity: 0.25;
  }
}
</style>
