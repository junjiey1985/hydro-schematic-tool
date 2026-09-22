<template>
  <div class="step">
    <div v-if="!hasSeries" class="empty-line">
      尚无时序数据。率定需要降雨、蒸发与各单元出口的实测流量 —— 可先一键生成 3 年合成演示数据（OSSE：
      "实测"流量由真值参数跑本工具模型生成，便于验证率定效果）。
    </div>

    <template v-else>
      <!-- 总览 -->
      <div class="cards">
        <div class="card">
          <div class="ck">降雨序列</div>
          <div class="cv">{{ counts.rain || 0 }} <i>条</i></div>
          <div class="cs">{{ points.rain || 0 }} 个值</div>
        </div>
        <div class="card">
          <div class="ck">流量序列</div>
          <div class="cv">{{ counts.flow || 0 }} <i>条</i></div>
          <div class="cs">{{ points.flow || 0 }} 个值</div>
        </div>
        <div class="card">
          <div class="ck">蒸发序列</div>
          <div class="cv" :class="counts.evap ? '' : 'bad-t'">{{ counts.evap || 0 }} <i>条</i></div>
          <div class="cs">{{ points.evap || 0 }} 个值</div>
        </div>
        <div class="card">
          <div class="ck">时段跨度</div>
          <div class="cv sm">{{ span.start ? span.start.slice(0, 10) : '—' }}</div>
          <div class="cs">至 {{ span.end ? span.end.slice(0, 10) : '—' }}</div>
        </div>
        <div class="card">
          <div class="ck">可率定单元</div>
          <div class="cv" :class="covSum.calibratable ? 'ok-t' : 'warn-t'">
            {{ covSum.calibratable || 0 }} <i>/ {{ covSum.subbasin_count || unitCount }}</i>
          </div>
          <div class="cs">其余借用下游参数</div>
        </div>
      </div>

      <div class="sub-sec">
        ① 逐单元覆盖率
        <span class="muted">率定按预报单元组织：面雨量站是否齐备 + 出口站是否有实测流量</span>
      </div>
      <div v-if="!covRows.length" class="empty-line">
        所在项目尚未划分子流域，覆盖率无从核对 —— 请先在顶栏完成「子流域划分」。
      </div>
      <div v-else class="tbl-wrap">
        <table class="tbl">
          <thead>
            <tr>
              <th style="width: 62px">单元</th>
              <th style="width: 104px">名称</th>
              <th class="num">单元面积</th>
              <th class="num">控制面积</th>
              <th style="width: 96px">出口站</th>
              <th class="num" style="width: 96px">面雨量覆盖</th>
              <th class="num" style="width: 78px">雨量站</th>
              <th style="width: 120px">缺测站</th>
              <th class="num" style="width: 76px">实测流量</th>
              <th class="num" style="width: 66px">蒸发</th>
              <th style="width: 88px">结论</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in covRows" :key="r.code">
              <td>
                <span class="dot" :style="{ background: colorOf(r.code) }"></span>{{ r.code }}
              </td>
              <td class="small">{{ r.name || '—' }}</td>
              <td class="num">{{ fmt(r.area_km2, 1) }}</td>
              <td class="num muted">{{ fmt(r.upstream_area_km2, 1) }}</td>
              <td class="small">{{ r.outlet_station || '—' }}</td>
              <td class="num" :class="r.rain_coverage >= 1 ? 'ok-t' : 'warn-t'">
                {{ fmt((r.rain_coverage || 0) * 100, 0) }}%
              </td>
              <td class="num">{{ r.rain_station_total || 0 }}</td>
              <td class="small muted">
                {{ (r.rain_missing || []).length ? r.rain_missing.join('、') : '—' }}
              </td>
              <td class="num" :class="r.flow_available ? 'ok-t' : 'warn-t'">
                {{ r.flow_available ? '有' : '无' }}
              </td>
              <td class="num" :class="r.evap_available ? 'ok-t' : 'warn-t'">
                {{ r.evap_available ? '有' : '无' }}
              </td>
              <td class="small">
                <span :class="statusClass(r.status)">{{ statusText(r.status) }}</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="note">
        结论判定：出口站有实测流量 → <b>可率定</b>（独立优化该单元参数）；无流量 → <b>借用参数</b>
        （不独立率定，最终参数借用直接下游已率定单元）。面雨量覆盖率 = 该单元泰森多边形内已挂接雨量站的权重占比。
      </div>

      <!-- 序列清单 -->
      <div class="sub-sec">
        ② 序列清单
        <span class="muted">缺测率 > 5% 的序列已标黄（率定按缺测对剔除，不插补）</span>
      </div>
      <div v-for="k in kinds" :key="k.key" class="grp">
        <div class="grp-t">
          {{ k.label }}
          <span class="muted">{{ (series[k.key] || []).length }} 条</span>
        </div>
        <div v-if="!(series[k.key] || []).length" class="empty-line small">无</div>
        <div v-else class="tbl-wrap">
          <table class="tbl">
            <thead>
              <tr>
                <th style="width: 92px">编号</th>
                <th style="width: 116px">名称</th>
                <th style="width: 82px">时段</th>
                <th class="num" style="width: 74px">点数</th>
                <th class="num" style="width: 74px">应有</th>
                <th class="num" style="width: 84px">缺测率</th>
                <th style="width: 168px">缺测区间</th>
                <th>来源</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="s in series[k.key]" :key="s.key">
                <td class="mono small">{{ s.key }}</td>
                <td class="small">{{ s.name || '—' }}</td>
                <td class="small muted">{{ s.interval || '—' }}</td>
                <td class="num">{{ s.count }}</td>
                <td class="num muted">{{ s.expected }}</td>
                <td class="num" :class="(s.missing_rate || 0) > 0.05 ? 'warn-t' : 'ok-t'">
                  {{ fmt((s.missing_rate || 0) * 100, 1) }}%
                </td>
                <td class="small muted">
                  {{ (s.gaps || []).length ? gapText(s.gaps) : '—' }}
                </td>
                <td class="small muted">{{ s.source || '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>

    <div class="step-foot">
      <span class="muted">
        <template v-if="covSum.borrow">
          有 {{ covSum.borrow }} 个单元无出口实测流量，率定时将借用下游参数
        </template>
        <template v-else-if="covSum.calibratable">全部 {{ covSum.calibratable }} 个单元均可独立率定</template>
      </span>
      <span class="grow"></span>
      <button class="btn" @click="makeDemo">生成演示数据</button>
      <button class="btn primary" :disabled="!canNext" @click="$emit('goto', 'config')">
        下一步：配置参数
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { makeDemoTimeseries, loadTimeseries, state, subbasinColorOf } from '../../store'

defineEmits(['goto'])

const ts = computed(() => state.timeseries || {})
const series = computed(() => ts.value.series || {})
const summary = computed(() => ts.value.summary || {})
const counts = computed(() => summary.value.counts || {})
const points = computed(() => summary.value.points || {})
const span = computed(() => summary.value.span || {})
const cov = computed(() => ts.value.coverage || {})
const covRows = computed(() => cov.value.rows || [])
const covSum = computed(() => cov.value.summary || {})
const unitCount = computed(() => ((state.subbasins && state.subbasins.subbasins) || []).length)

const kinds = [
  { key: 'rain', label: '降雨' },
  { key: 'flow', label: '流量' },
  { key: 'evap', label: '蒸发' }
]

const hasSeries = computed(() => !!(counts.value.rain || counts.value.flow || counts.value.evap))
const canNext = computed(() => !!(counts.value.rain && counts.value.evap) && unitCount.value > 0)

function colorOf(code) {
  const list = (state.subbasins && state.subbasins.subbasins) || []
  const sb = list.find((x) => x.code === code)
  return sb ? subbasinColorOf(sb) : '#93a1b0'
}
function statusText(s) {
  return { ok: '可率定', borrow: '借用参数', no_rain: '缺面雨量', no_evap: '缺蒸发' }[s] || s || '—'
}
function statusClass(s) {
  return s === 'ok' ? 'ok-t' : 'warn-t'
}
function gapText(gaps) {
  return gaps
    .slice(0, 3)
    .map((g) => `${String(g.start).slice(0, 10)}~${String(g.end).slice(0, 10)}`)
    .join('、')
}
function fmt(v, n = 2) {
  if (v === null || v === undefined || !Number.isFinite(Number(v))) return '—'
  return Number(v).toFixed(n)
}
async function makeDemo() {
  await makeDemoTimeseries({ years: 3 })
  await loadTimeseries(true)
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
.grp {
  margin-bottom: 10px;
}
.grp-t {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--text-2);
  margin: 8px 0 4px;
}
.grp-t .muted {
  font-weight: 400;
  margin-left: 6px;
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
.mono {
  font-family: var(--mono, ui-monospace, monospace);
}
.grow {
  flex: 1;
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
