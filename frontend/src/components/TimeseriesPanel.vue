<template>
  <div v-if="show" class="mask" @click.self="$emit('close')">
    <div class="modal ts-modal">
      <h3>
        时序数据 · 率定输入
        <span class="ts-sub">降雨 / 流量 / 蒸发 → 预报单元覆盖率检查</span>
      </h3>

      <div class="body">
        <!-- ============ ① 数据检查 ============ -->
        <div class="sub-sec">① 数据检查</div>

        <div class="stat-grid">
          <div class="k">降雨序列</div>
          <div class="v">{{ counts.rain }} 条 / {{ points.rain }} 点</div>
          <div class="k">流量序列</div>
          <div class="v">{{ counts.flow }} 条 / {{ points.flow }} 点</div>
          <div class="k">蒸发序列</div>
          <div class="v">{{ counts.evap }} 条 / {{ points.evap }} 点</div>
          <div class="k">数据时间范围</div>
          <div class="v">{{ spanText }}</div>
        </div>

        <div v-if="!coverage.available" class="hintbar warn-bar" style="margin-bottom: 10px">
          {{ coverage.reason }}：率定数据按预报单元组织，请先在顶栏完成「子流域划分」。
        </div>
        <template v-else>
          <div class="cov-line">
            <span class="tag" :class="covTagClass">
              可率定 {{ coverage.summary.calibratable }} / {{ coverage.summary.subbasin_count }} 个单元
            </span>
            <span class="muted">
              雨量齐备 {{ coverage.summary.rain_full }} 个 · 出口站有流量
              {{ coverage.summary.flow_covered }} 个 · 蒸发数据
              {{ coverage.summary.evap_available ? '已就绪' : '缺失' }}
            </span>
          </div>
          <div class="tbl-wrap cov-wrap">
            <table class="tbl">
              <thead>
                <tr>
                  <th style="width: 54px">单元</th>
                  <th style="width: 84px">名称</th>
                  <th style="width: 84px">面积 km²</th>
                  <th style="width: 92px">雨量覆盖</th>
                  <th style="width: 96px">出口测站</th>
                  <th>缺失/说明</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="r in coverage.rows" :key="r.code">
                  <td><span class="dot" :style="{ background: colorOf(r.code) }"></span>{{ r.code }}</td>
                  <td>{{ r.name }}</td>
                  <td class="num">{{ fmt(r.upstream_area_km2) }}</td>
                  <td class="num">
                    <span :class="r.rain_coverage >= 0.999 ? 'ok-t' : 'warn-t'">
                      {{ (r.rain_coverage * 100).toFixed(0) }}%
                    </span>
                    <span class="muted">（{{ r.rain_station_total }} 站）</span>
                  </td>
                  <td>{{ r.flow_available ? r.outlet_station : '—' }}</td>
                  <td class="small">
                    <span v-if="r.status === 'ok'" class="ok-t">数据齐备，可直接率定</span>
                    <span v-else class="warn-t">
                      缺：{{ (r.rain_missing || []).join('、') || '—' }}
                      <template v-if="!r.flow_available">；出口站无流量序列</template>
                      （将按「借用参数」处理）
                    </span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>

        <!-- ============ ② 导入 ============ -->
        <div class="sub-sec">② 导入时序 CSV</div>
        <div class="imp-row">
          <select v-model="form.kind">
            <option value="rain">降雨（mm，累计值）</option>
            <option value="flow">流量（m³/s）</option>
            <option value="evap">蒸发能力（mm）</option>
          </select>
          <select v-model="form.interval">
            <option value="">时段自动识别</option>
            <option value="1h">1 小时（聚合）</option>
            <option value="6h">6 小时（聚合）</option>
            <option value="12h">12 小时（聚合）</option>
            <option value="24h">日（聚合）</option>
          </select>
          <input v-model="form.station" type="text" placeholder="单列文件的站名（可选）" />
          <input ref="fileInput" type="file" multiple accept=".csv,.txt" @change="onPick" />
          <button class="btn primary" :disabled="!picked.length || !!state.busy" @click="doImport">
            导入 {{ picked.length ? `(${picked.length})` : '' }}
          </button>
        </div>
        <div class="hint">
          支持长表（站名,时间,值）、宽表（首列时间 + 各站列）与单列文件；UTF-8 / GBK 自动识别；
          缺测（空/-9999/—）自动计入缺测率。
        </div>

        <div class="demo-row">
          <button class="btn sm" :disabled="!state.project || !!state.busy" @click="doDemo">
            生成 3 年合成演示数据
          </button>
          <span class="muted">
            按当前划分的单元出口站生成降雨/流量/蒸发（含真值参数），可立即验证整条链路
          </span>
        </div>

        <!-- ============ ③ 已导入序列 ============ -->
        <div class="sub-sec">
          ③ 已导入序列
          <span class="muted" v-if="flatSeries.length">（{{ flatSeries.length }} 条）</span>
        </div>
        <div v-if="!flatSeries.length" class="empty-line">尚未导入任何序列。</div>
        <div v-else class="tbl-wrap lst-wrap">
          <table class="tbl">
            <thead>
              <tr>
                <th style="width: 62px">类型</th>
                <th style="width: 120px">站点 / 序列</th>
                <th style="width: 58px">时段</th>
                <th style="width: 168px">起止</th>
                <th style="width: 60px">点数</th>
                <th style="width: 96px">缺测率</th>
                <th style="width: 88px">来源</th>
                <th style="width: 96px">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="s in flatSeries" :key="s.kind + '/' + s.key">
                <td>{{ kindLabel(s.kind) }}</td>
                <td>{{ s.name || s.key }}</td>
                <td>{{ s.interval }}</td>
                <td class="small">{{ s.start }} ~ {{ s.end }}</td>
                <td class="num">{{ s.count }}</td>
                <td class="num">
                  <span :class="statusClass(s.status)">{{ (s.missing_rate * 100).toFixed(1) }}%</span>
                </td>
                <td class="small muted">{{ s.source || '—' }}</td>
                <td>
                  <button class="lnk" @click="preview(s)">预览</button>
                  <button class="lnk danger" @click="doDelete(s)">删除</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <!-- 曲线预览 -->
        <div v-if="previewData" class="pv">
          <div class="pv-head">
            <b>{{ previewData.entry.name || previewData.entry.key }}</b>
            <span class="muted">
              {{ previewData.entry.interval }} · {{ previewData.entry.count }} 点 · 缺测
              {{ (previewData.entry.missing_rate * 100).toFixed(1) }}%
            </span>
            <span class="fp-grow"></span>
            <button class="lnk" @click="previewData = null">收起</button>
          </div>
          <svg class="pv-chart" :viewBox="`0 0 ${pvW} ${pvH}`" preserveAspectRatio="none">
            <polyline :points="sparkPath" fill="none" stroke="#1e6fa8" stroke-width="1.2" />
            <line x1="0" :y1="pvH" :x2="pvW" :y2="pvH" stroke="#e2e8ef" stroke-width="1" />
          </svg>
          <div class="pv-axis">
            <span>{{ previewData.points[0] ? previewData.points[0][0] : '' }}</span>
            <span>
              峰值 {{ pvMax }} · 均值 {{ pvMean }}
            </span>
            <span>{{ previewData.points.length ? previewData.points[previewData.points.length - 1][0] : '' }}</span>
          </div>
        </div>

        <!-- 缺测区间 -->
        <div v-if="previewData && previewData.entry.gaps && previewData.entry.gaps.length" class="gaps">
          <div class="gaps-t">缺测区间（前 {{ previewData.entry.gaps.length }} 段）</div>
          <div v-for="(g, i) in previewData.entry.gaps" :key="i">
            · {{ g.start }} ~ {{ g.end }}（{{ g.steps }} 个时段）
          </div>
        </div>
      </div>

      <div class="foot">
        <button class="btn danger" style="margin-right: auto" :disabled="!flatSeries.length" @click="doClearAll">
          清空全部时序数据
        </button>
        <button class="btn" @click="$emit('close')">关闭</button>
        <button class="btn primary" :disabled="!state.project" @click="refresh">重新检查</button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import {
  clearTimeseries,
  deleteTimeseries,
  fetchSeries,
  importTimeseries,
  loadTimeseries,
  makeDemoTimeseries,
  state,
  subbasinColorOf,
  timeseriesSeries,
  toast
} from '../store'

const props = defineProps({ show: { type: Boolean, default: false } })
defineEmits(['close'])

const form = reactive({ kind: 'rain', interval: '', station: '' })
const picked = ref([])
const fileInput = ref(null)
const previewData = ref(null)

const KIND_LABELS = { rain: '降雨', flow: '流量', evap: '蒸发' }
function kindLabel(k) {
  return KIND_LABELS[k] || k
}
function fmt(v) {
  return v === null || v === undefined ? '—' : Number(v).toFixed(0)
}
function statusClass(s) {
  return s === 'ok' ? 'ok-t' : s === 'bad' ? 'bad-t' : 'warn-t'
}

const ts = computed(() => state.timeseries)
const coverage = computed(() => {
  const c = ts.value && ts.value.coverage
  return c || { available: false, reason: '尚未划分子流域', rows: [], summary: {} }
})
const covTagClass = computed(() => {
  const s = coverage.value.summary || {}
  if (!s.subbasin_count) return 'warn'
  return s.calibratable === s.subbasin_count ? 'ok' : 'warn'
})
const counts = computed(() => {
  const s = ts.value && ts.value.summary
  return (s && s.counts) || { rain: 0, flow: 0, evap: 0 }
})
const points = computed(() => {
  const s = ts.value && ts.value.summary
  return (s && s.points) || { rain: 0, flow: 0, evap: 0 }
})
const spanText = computed(() => {
  const s = ts.value && ts.value.summary
  const sp = s && s.span
  return sp && sp.start ? `${sp.start} ~ ${sp.end}` : '—'
})

const flatSeries = computed(() => {
  const d = timeseriesSeries.value || {}
  const out = []
  for (const kind of ['rain', 'flow', 'evap']) {
    for (const s of d[kind] || []) out.push({ ...s, kind })
  }
  return out
})

// 覆盖率表配色与地图单元面一致
function colorOf(code) {
  const list = (state.subbasins && state.subbasins.subbasins) || []
  const sb = list.find((x) => x.code === code)
  return sb ? subbasinColorOf(sb) : '#93a1b0'
}

// ---------------- 交互
function onPick(e) {
  picked.value = Array.from(e.target.files || [])
}

async function doImport() {
  if (!picked.value.length) return
  await importTimeseries(picked.value, {
    kind: form.kind,
    interval: form.interval,
    station: form.station
  })
  picked.value = []
  if (fileInput.value) fileInput.value.value = ''
}

async function doDemo() {
  try {
    await makeDemoTimeseries({ days: 1095, replace: true })
  } catch (e) {
    /* store 已提示 */
  }
}

async function doDelete(s) {
  if (!confirm(`删除序列「${s.name || s.key}」（${kindLabel(s.kind)}）？`)) return
  await deleteTimeseries(s.kind, s.key)
  if (previewData.value && previewData.value.entry.key === s.key) previewData.value = null
}

async function doClearAll() {
  if (!confirm('清空该项目下全部时序数据（降雨/流量/蒸发）？此操作不可恢复。')) return
  await clearTimeseries('')
  previewData.value = null
}

async function preview(s) {
  try {
    const d = await fetchSeries(s.kind, s.key, 900)
    previewData.value = d
  } catch (e) {
    toast('读取序列失败：' + e.message, 'err')
  }
}

async function refresh() {
  await loadTimeseries(true)
  previewData.value = null
  toast('已重新检查时序数据', 'ok')
}

// ---------------- 预览曲线几何
const pvW = 620
const pvH = 90
const pvVals = computed(() => {
  const pts = (previewData.value && previewData.value.points) || []
  return pts.map((p) => Number(p[1]) || 0)
})
const pvMax = computed(() => {
  const v = pvVals.value
  return v.length ? Math.max(...v).toFixed(1) : '—'
})
const pvMean = computed(() => {
  const v = pvVals.value
  return v.length ? (v.reduce((a, b) => a + b, 0) / v.length).toFixed(1) : '—'
})
const sparkPath = computed(() => {
  const v = pvVals.value
  if (!v.length) return ''
  const max = Math.max(...v) || 1
  const min = Math.min(...v, 0)
  const span = max - min || 1
  const n = v.length
  return v
    .map((val, i) => {
      const x = n === 1 ? 0 : (i / (n - 1)) * pvW
      const y = pvH - ((val - min) / span) * (pvH - 4) - 2
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
})

async function load() {
  if (!state.project) return
  await loadTimeseries(true)
}

onMounted(load)
watch(() => props.show, (v) => v && load())
watch(() => state.project && state.project.id, (v) => v && props.show && load())
</script>

<style scoped>
.ts-modal {
  width: 900px;
  max-width: 96vw;
}
.ts-modal h3 {
  display: flex;
  align-items: baseline;
  gap: 10px;
}
.ts-sub {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-3);
}
.stat-grid {
  display: grid;
  grid-template-columns: 110px 1fr 110px 1fr;
  gap: 4px 10px;
  padding: 9px 11px;
  margin-bottom: 10px;
  background: var(--panel-2);
  border: 1px solid var(--line);
  border-radius: 8px;
  font-size: 12px;
}
.stat-grid .k {
  color: var(--text-3);
}
.stat-grid .v {
  color: var(--text);
  font-weight: 600;
}
.cov-line {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
  font-size: 12px;
}
.cov-wrap {
  max-height: 168px;
}
.lst-wrap {
  max-height: 216px;
}
.tbl td.small,
.tbl td .small {
  font-size: 11px;
}
.tbl td.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.ok-t {
  color: var(--green);
  font-weight: 600;
}
.warn-t {
  color: var(--amber);
  font-weight: 600;
}
.bad-t {
  color: var(--accent);
  font-weight: 600;
}
.imp-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 6px;
}
.imp-row select,
.imp-row input[type='text'] {
  padding: 5px 8px;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  background: #fff;
}
.imp-row input[type='file'] {
  flex: 1 1 200px;
  font-size: 12px;
}
.demo-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  background: #fdf6e7;
  border: 1px solid #f0e0bc;
  border-radius: 7px;
  font-size: 11.5px;
}
.empty-line {
  padding: 12px;
  color: var(--text-3);
  font-size: 12px;
  text-align: center;
  border: 1px dashed var(--line-strong);
  border-radius: 7px;
}
.lnk {
  border: none;
  background: transparent;
  color: var(--primary);
  padding: 0 6px;
  font-size: 12px;
}
.lnk:hover {
  text-decoration: underline;
}
.lnk.danger {
  color: var(--accent);
}
.pv {
  margin-top: 10px;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 8px 10px;
  background: #fff;
}
.pv-head {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  margin-bottom: 6px;
}
.pv-chart {
  width: 100%;
  height: 90px;
  display: block;
  background: linear-gradient(#fbfcfe, #fff);
}
.pv-axis {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: var(--text-3);
  margin-top: 3px;
}
.gaps {
  margin-top: 8px;
  font-size: 11px;
  line-height: 1.7;
  color: var(--amber);
  max-height: 120px;
  overflow: auto;
}
.gaps-t {
  font-weight: 600;
  color: var(--text-2);
}
</style>
