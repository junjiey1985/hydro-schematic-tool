<template>
  <div class="sidebar">
    <!-- ================= 图层 ================= -->
    <div class="sec grow">
      <div class="sec-head" @click="tog('layers')">
        <span>图层</span>
        <span class="chev">{{ ui.layers ? '▾' : '▸' }} {{ state.layers.length }}</span>
      </div>
      <div class="sec-body layers" v-show="ui.layers">
        <div v-if="!state.layers.length" class="muted" style="padding: 6px 2px">暂无图层</div>
        <div class="layer-list">
          <div
            v-for="l in state.layers"
            :key="l.id"
            class="layer"
            :class="{ on: isActive(l) }"
            @click="activate(l)"
          >
            <span class="dot" :style="{ background: layerColor(l.type) }"></span>
            <span class="eye" :class="{ off: !l.visible }" @click.stop="toggleLayerVisible(l)" :title="l.visible ? '隐藏' : '显示'">
              {{ l.visible ? '◉' : '○' }}
            </span>
            <span class="nm" :title="l.name">{{ l.name }}</span>
            <span class="tag" style="font-size: 10px">{{ l.feature_count }}</span>
            <span class="eye" title="删除图层" @click.stop="askDeleteLayer(l)">✕</span>
          </div>
        </div>

        <div class="btn-row layers-foot">
          <button class="btn sm block" @click="$emit('open-upload')">导入 SHP</button>
        </div>
      </div>
    </div>

    <!-- ================= 拓扑 / 概化图 ================= -->
    <div class="sec">
      <div class="sec-head" @click="tog('topo')">
        <span>拓扑关系与概化图</span>
        <span class="chev">{{ ui.topo ? '▾' : '▸' }}</span>
      </div>
      <div class="sec-body" v-show="ui.topo">
        <div class="btn-row" style="margin-bottom: 8px">
          <button class="btn sm primary" @click="$emit('open-topo-options')">构建拓扑关系</button>
          <button class="btn sm" :disabled="!state.topology" @click="genSchematic">生成概化图</button>
        </div>
        <div class="btn-row" style="margin-bottom: 10px">
          <button class="btn sm" :disabled="!state.schematic" @click="resetSchematic">重新布局</button>
          <a class="btn sm" :class="{ disabled: !state.schematic }" :href="svgHref" download="水系概化图.svg" @click="onSvgClick">
            导出 SVG
          </a>
        </div>

        <div v-if="state.topology" class="stat-grid" style="margin-bottom: 8px">
          <div class="k">河段</div><div class="v">{{ state.topology.stats.edge_count }}</div>
          <div class="k">节点</div><div class="v">{{ state.topology.stats.node_count }}</div>
          <div class="k">汇流点 / 河源 / 出口</div>
          <div class="v">
            {{ state.topology.stats.junction_count }} / {{ state.topology.stats.source_count }} /
            {{ state.topology.stats.outlet_count }}
          </div>
          <div class="k">最大河流级别</div><div class="v">{{ state.topology.stats.max_order }}</div>
          <div class="k">河网总长</div><div class="v">{{ state.topology.stats.total_length_km }} km</div>
          <div class="k">流向判定</div><div class="v">{{ methodLabel }}</div>
          <div class="k">站点挂接</div>
          <div class="v">{{ state.topology.stats.station_attached }} / {{ state.topology.stats.station_count }}</div>
        </div>

        <div
          v-for="(w, i) in topoWarnings"
          :key="i"
          class="hintbar"
          style="margin-bottom: 6px; color: #b7791f; border-color: #f0e0bc; background: #fdf6e7"
        >
          {{ w }}
        </div>

        <div v-if="state.schematic" class="stat-grid">
          <div class="k">概化河段</div><div class="v">{{ state.schematic.edges.length }}</div>
          <div class="k">概化节点</div><div class="v">{{ state.schematic.nodes.length }}</div>
          <div class="k">站点符号</div><div class="v">{{ state.schematic.stations.length }}</div>
          <div class="k">湖泊/水库</div><div class="v">{{ state.schematic.lakes.length }}</div>
        </div>
      </div>
    </div>


    <!-- ================= 预报单元（子流域） ================= -->
    <div class="sec" v-if="state.subbasins">
      <div class="sec-head" @click="tog('sub')">
        <span>预报单元（子流域）</span>
        <span class="chev">{{ ui.sub ? '▾' : '▸' }} {{ subList.length }}</span>
      </div>
      <div class="sec-body" v-show="ui.sub">
        <div class="stat-grid" style="margin-bottom: 8px">
          <div class="k">单元数</div><div class="v">{{ subList.length }}</div>
          <div class="k">合计面积</div><div class="v">{{ fmt(subStats.total_area_km2, 1) }} km²</div>
          <div class="k">最大汇流级</div><div class="v">{{ subStats.max_order ?? '—' }}</div>
          <div class="k">划分方式</div><div class="v">{{ subModeLabel }}</div>
        </div>

        <div class="sub-list">
          <div
            v-for="sb in subList"
            :key="sb.code"
            class="sub-row"
            :class="{ on: state.tab === 'schematic' && state.sch.focusSubbasin === sb.code }"
            @click="pick(sb)"
          >
            <span class="sdot" :style="{ background: subbasinColorOf(sb) }"></span>
            <span class="scode">{{ sb.code }}</span>
            <span class="sname" :title="`${sb.name}｜上游面积 ${fmt(sb.upstream_area_km2, 1)} km²`">
              {{ sb.name }}
            </span>
            <span class="sarea">{{ fmt(sb.area_km2, 1) }}</span>
          </div>
        </div>

        <div class="hintbar" style="margin-top: 8px">
          {{ pickHint }}
        </div>

        <div class="btn-row" style="margin-top: 8px">
          <button class="btn sm block" @click="$emit('open-subbasin')">参数 / 重新划分 / 导出…</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, reactive } from 'vue'
import {
  state,
  buildSchematic,
  deleteLayer,
  focusSubbasinInSchematic,
  layerColor,
  pickSubbasin,
  subbasinColorOf,
  toast
} from '../store'

const emit = defineEmits(['open-upload', 'open-topo-options', 'open-subbasin'])

const ui = reactive({ layers: true, topo: true, sub: true })

function tog(k) {
  ui[k] = !ui[k]
}

// ---------------------------------------------------------------- 预报单元
const subList = computed(() => (state.subbasins ? state.subbasins.subbasins || [] : []))
const subStats = computed(() => (state.subbasins ? state.subbasins.stats || {} : {}))
const subModeLabel = computed(
  () =>
    ({ station: '水文站', junction: '汇流节点', manual: '手工断面' }[subStats.value.mode] ||
    subStats.value.mode ||
    '—')
)

function fmt(v, n = 2) {
  return v === null || v === undefined ? '—' : Number(v).toFixed(n)
}

function pick(sb) {
  // 概化图视图：聚焦该单元的河段（不跳转地图）；再次点击退出聚焦
  if (state.tab === 'schematic') {
    if (!state.schematic) {
      toast('请先生成概化图', 'err')
      return
    }
    focusSubbasinInSchematic(sb.code)
    return
  }
  // 地图视图：定位并选中该单元面
  state.tab = 'map'
  setTimeout(() => pickSubbasin(sb.code), 100)
}

const pickHint = computed(() =>
  state.tab === 'schematic'
    ? '点击某行聚焦该单元的河段（自动开启着色、其余河段淡化并定位到该单元），再次点击退出聚焦。'
    : '面积单位 km²；点击某行可在地图上定位并选中该单元。上游面积＝该单元以上累计集水面积（率定时的控制面积）。'
)

const methodLabel = computed(
  () =>
    ({ dem: 'DEM 高程', elevation_attribute: '高程属性', digitized: '数字化方向' }[state.topology?.method] ||
    state.topology?.method ||
    '—')
)
const topoWarnings = computed(() => (state.topology && state.topology.warnings) || [])
const svgHref = computed(() => (state.project ? `/api/projects/${state.project.id}/schematic/export.svg` : '#'))

function isActive(l) {
  return state.map.drawTarget === l.id
}

function activate(l) {
  state.map.drawTarget = l.id
}

async function askDeleteLayer(l) {
  if (!confirm(`确定删除图层「${l.name}」及其全部要素吗？`)) return
  await deleteLayer(l)
}

async function genSchematic() {
  await buildSchematic({ reset: true })
}

async function resetSchematic() {
  await buildSchematic({ reset: true })
}

function onSvgClick(e) {
  if (!state.schematic) {
    e.preventDefault()
    toast('请先生成概化图', 'err')
  }
}
</script>
