<template>
  <div class="step">
    <!-- 雨情设置 -->
    <div class="sub-sec">
      ① 预见期与情景雨情
      <span class="muted">用当前项目参数（含已采纳的率定参数）；历史段用实测降雨连续演算热启动，预报段用情景降雨</span>
    </div>
    <div class="row">
      <label>预见期</label>
      <input v-model.number="form.horizon" type="number" min="1" max="3650" class="num-in" />
      <span class="muted">天</span>
      <label style="margin-left: 10px">雨情</label>
      <span class="seg">
        <button :class="{ on: form.mode === 'design' }" @click="form.mode = 'design'">设计雨型</button>
        <button :class="{ on: form.mode === 'series' }" @click="form.mode = 'series'">逐日序列</button>
      </span>
    </div>
    <div v-if="form.mode === 'design'" class="row" style="margin-top: 6px">
      <label>降雨总量</label>
      <input v-model.number="form.total_mm" type="number" min="1" step="10" class="num-in" />
      <span class="muted">mm</span>
      <label style="margin-left: 10px">降雨历时</label>
      <input v-model.number="form.duration" type="number" min="1" class="num-in" />
      <span class="muted">天（≤预见期）</span>
      <label style="margin-left: 10px">峰值位置</label>
      <input v-model.number="form.peak_pos" type="number" min="0.05" max="0.95" step="0.05" class="num-in" />
      <span class="muted">0~1（0.4 = 前期降雨）</span>
    </div>
    <div v-else class="row" style="margin-top: 6px">
      <label>逐日降雨</label>
      <input
        v-model="form.seriesText"
        class="series-in"
        placeholder="如：50, 40, 30, 20, 10（逗号/空格分隔，不足按 0 处理）"
      />
      <span class="muted">mm/天</span>
    </div>
    <div class="row" style="margin-top: 6px">
      <label>蒸发假设</label>
      <input v-model.number="form.evap" type="number" min="0" step="0.5" class="num-in" />
      <span class="muted">mm/天（留空 = 历史均值）</span>
      <span class="grow"></span>
      <button id="fc-run" class="btn primary" :disabled="!canRun" @click="runForecast">
        {{ running ? '预报中…' : '运行预报' }}
      </button>
    </div>
    <div class="note">
      情景预报基于实测时序末尾拼接未来降雨驱动模型：历史段沿用实测面雨量（状态连续），
      预报段各单元统一使用情景降雨。结果为<b>情景模拟</b>，非实时雨情接入；预见期越长、
      降雨不确定性越大。
    </div>

    <!-- 结果 -->
    <template v-if="result">
      <div class="sub-sec">
        ② 预报过程线
        <span class="grow"></span>
        <span class="pair">
          <span v-for="u in units" :key="u.code">
            <i>{{ u.code }}</i>
            <b :style="{ color: colorOf(u.code) }">{{ fmt(u.forecast && u.forecast.peak_q, 0) }}</b>
          </span>
        </span>
      </div>
      <div class="chart-box">
        <EChart :option="chartOption" :height="248" />
      </div>

      <div class="sub-sec">
        ③ 预报段洪峰摘要
        <span class="muted">{{ scenarioDesc }}</span>
      </div>
      <div class="tbl-wrap">
        <table class="tbl">
          <thead>
            <tr>
              <th style="width: 62px">单元</th>
              <th style="width: 110px">名称</th>
              <th style="width: 96px">出口站</th>
              <th class="num" style="width: 92px">预报洪峰</th>
              <th style="width: 142px">峰现时间</th>
              <th class="num" style="width: 92px">预报段均流</th>
              <th class="num" style="width: 96px">预报段径流深</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="u in units" :key="u.code">
              <td><span class="dot" :style="{ background: colorOf(u.code) }"></span>{{ u.code }}</td>
              <td class="small">{{ u.name || '—' }}</td>
              <td class="small">{{ u.outlet_station || '—' }}</td>
              <td class="num strong">{{ fmt(u.forecast && u.forecast.peak_q, 1) }} m³/s</td>
              <td class="small muted">{{ (u.forecast && u.forecast.peak_time) || '—' }}</td>
              <td class="num">{{ fmt(u.forecast && u.forecast.mean_q, 1) }} m³/s</td>
              <td class="num">{{ fmt(u.forecast && u.forecast.runoff_mm, 1) }} mm</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-if="(result.warnings || []).length" class="note warn-t">
        {{ result.warnings.join('；') }}
      </div>
    </template>
    <div v-else class="empty-line small">
      尚未运行预报。设好预见期与情景降雨后点「运行预报」。
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import EChart from './EChart.vue'
import { api } from '../../api'
import { state, subbasinColorOf, toast } from '../../store'

const form = reactive({
  horizon: 30,
  mode: 'design',
  total_mm: 100,
  duration: 7,
  peak_pos: 0.4,
  seriesText: '',
  evap: null
})

const running = ref(false)
const result = ref(null)
const activeCode = ref('')

onMounted(() => {
  // 回到本 Tab 时恢复上次预报结果
  if (!result.value && state.calib.fcResult) result.value = state.calib.fcResult
})

const units = computed(() => (result.value && result.value.units) || [])
const scenarioDesc = computed(() => (result.value && result.value.rain_scenario && result.value.rain_scenario.desc) || '')

const canRun = computed(() => {
  if (running.value || !state.project) return false
  if (form.mode === 'design') return Number(form.total_mm) > 0 && Number(form.horizon) >= 1
  return form.seriesText.trim().length > 0
})

async function runForecast() {
  const rain =
    form.mode === 'design'
      ? {
          mode: 'design',
          total_mm: Number(form.total_mm),
          duration_days: Number(form.duration) || 7,
          peak_pos: Number(form.peak_pos) || 0.4
        }
      : {
          mode: 'series',
          values: form.seriesText.split(/[,\n\s，]+/).filter((x) => x !== '').map(Number)
        }
  if (rain.mode === 'series' && (!rain.values.length || rain.values.some((v) => !Number.isFinite(v)))) {
    toast('逐日序列格式有误', 'warn')
    return
  }
  running.value = true
  try {
    const payload = { horizon_days: Number(form.horizon) || 30, rain }
    if (form.evap !== null && form.evap !== '') payload.evap_mm = Number(form.evap)
    const r = await api.calibrationForecast(state.project.id, payload)
    result.value = r
    state.calib.fcResult = r
    const list = r.units || []
    if (!list.some((u) => u.code === activeCode.value)) activeCode.value = (list[0] || {}).code || ''
    toast(`预报完成：${list.length} 个单元，预见期 ${r.forecast.days} 天`, 'ok', 3600)
  } catch (e) {
    toast(e.message || '预报失败', 'warn', 4200)
  } finally {
    running.value = false
  }
}

const chartOption = computed(() => {
  const r = result.value
  if (!r) return {}
  const u = units.value.find((x) => x.code === activeCode.value) || units.value[0]
  if (!u || !u.series) return {}
  const t = u.series.time || []
  const sim = u.series.sim || []
  const fStart = (r.forecast || {}).start || ''
  let fIdx = t.findIndex((x) => x >= fStart)
  if (fIdx < 0) fIdx = 0
  return {
    animation: false,
    grid: { left: 60, right: 16, top: 26, bottom: 52 },
    tooltip: {
      trigger: 'axis',
      valueFormatter: (v) => (v == null ? '—' : `${Number(v).toFixed(1)} m³/s`)
    },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 14, bottom: 6 }],
    xAxis: {
      type: 'category',
      data: t,
      axisLabel: { color: '#8a97a5', fontSize: 10 }
    },
    yAxis: {
      type: 'value',
      name: 'Q (m³/s)',
      scale: true,
      nameTextStyle: { color: '#8a97a5', fontSize: 10 },
      axisLabel: { color: '#8a97a5', fontSize: 10 },
      splitLine: { lineStyle: { color: '#f0f2f5' } }
    },
    series: [
      {
        name: '流量',
        type: 'line',
        showSymbol: false,
        data: sim,
        lineStyle: { color: '#1e6fa8', width: 1.6 },
        markLine: {
          symbol: 'none',
          silent: true,
          label: { formatter: '预报起点', color: '#c0392b', fontSize: 10 },
          lineStyle: { color: '#c0392b', type: 'dashed' },
          data: [{ xAxis: fIdx }]
        },
        markArea: {
          silent: true,
          itemStyle: { color: 'rgba(30, 111, 168, 0.07)' },
          data: [[{ xAxis: fIdx, name: '预报期' }, { xAxis: t.length - 1 }]]
        }
      }
    ]
  }
})

function colorOf(code) {
  const list = (state.subbasins && state.subbasins.subbasins) || []
  const sb = list.find((x) => x.code === code)
  return sb ? subbasinColorOf(sb) : '#93a1b0'
}
function fmt(v, n = 1) {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return '—'
  return Number(v).toFixed(n)
}
</script>

<style scoped>
.step {
  font-size: 12px;
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
.series-in {
  flex: 1;
  min-width: 260px;
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
  padding: 4px 12px;
  font-size: 12px;
  background: #fff;
  color: var(--text-3);
}
.seg button.on {
  background: var(--primary-soft);
  color: var(--primary);
  font-weight: 600;
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
.grow {
  flex: 1;
}
.strong {
  font-weight: 600;
  color: var(--primary);
}
.pair {
  display: inline-flex;
  gap: 10px;
}
.pair span {
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  line-height: 1.3;
}
.pair i {
  font-size: 10px;
  font-style: normal;
  color: var(--text-3);
}
.pair b {
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.chart-box {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 8px 10px 2px;
  background: #fff;
}
.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 4px;
}
</style>
