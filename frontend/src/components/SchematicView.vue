<template>
  <div class="sch-wrap">
    <!-- 空态 -->
    <div v-if="!w" class="sch-empty">
      <div class="big">暂无概化图</div>
      <div class="sub">请先在侧栏构建河网拓扑，然后点击「生成概化图」。</div>
      <button class="btn primary" :disabled="!state.topology" @click="buildSchematic({ reset: true })">
        {{ state.topology ? '生成概化图' : '尚未构建拓扑' }}
      </button>
    </div>

    <!-- 画布 -->
    <div
      v-else
      ref="wrapEl"
      class="sch-canvas"
      :class="{ panning: pan.on, linking: state.sch.tool === 'addEdge' }"
      @wheel.prevent="onWheel"
      @pointerdown="onBgDown"
    >
      <svg ref="svgEl" :viewBox="`${vb.x} ${vb.y} ${vb.w} ${vb.h}`" preserveAspectRatio="xMidYMid meet">
        <defs>
          <pattern id="schGrid" width="40" height="40" patternUnits="userSpaceOnUse">
            <path d="M 40 0 L 0 0 0 40" fill="none" stroke="#e8edf2" stroke-width="1" />
          </pattern>
          <marker id="arrowMain" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#2f8ac4" />
          </marker>
          <marker id="arrowExtra" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#9db7c9" />
          </marker>
          <!-- 按预报单元着色时，每种单元色配一个对应的箭头 -->
          <template v-if="sbStyle">
            <marker
              v-for="it in sbStyle.list"
              :key="it.code"
              :id="`sbArrow-${it.code}`"
              viewBox="0 0 10 10"
              refX="8"
              refY="5"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" :fill="it.color" />
            </marker>
          </template>
        </defs>

        <rect :x="vb.x" :y="vb.y" :width="vb.w" :height="vb.h" fill="url(#schGrid)" />

        <!-- 河段 -->
        <g>
          <template v-for="e in w.edges" :key="e.id">
            <polyline
              class="edge-hit"
              :points="ptsStr(e.points)"
              @pointerdown.stop="onElDown($event, 'edge', e)"
            />
            <polyline
              class="edge"
              :class="{ main: e.kind !== 'extra', extra: e.kind === 'extra', sel: isSel('edge', e.id), dim: edgeDim(e), focus: edgeFocus(e) }"
              :points="ptsStr(e.points)"
              :stroke="edgeStroke(e)"
              :marker-mid="edgeMarker(e)"
            />
            <text
              v-if="showLabels && e.name"
              class="edge-label"
              :class="{ dim: edgeDim(e) }"
              :x="midOf(e.points)[0]"
              :y="midOf(e.points)[1] - 8"
              @pointerdown.stop="onElDown($event, 'edge', e)"
            >{{ e.name }}</text>
          </template>
        </g>

        <!-- 湖泊 -->
        <g>
          <template v-for="lk in w.lakes" :key="lk.id">
            <polygon
              class="lake"
              :class="{ sel: isSel('lake', lk.id) }"
              :points="ptsStr(lk.points)"
              @pointerdown.stop="onElDown($event, 'lake', lk)"
            />
            <text
              v-if="showLabels && lk.name"
              class="lake-label"
              :x="lk.x"
              :y="lk.y + 4"
              @pointerdown.stop="onElDown($event, 'lake', lk)"
            >{{ lk.name }}</text>
          </template>
        </g>

        <!-- 站点 -->
        <g>
          <template v-for="s in w.stations" :key="s.id">
            <polygon
              v-if="s.type === 'hydro'"
              class="station hydro"
              :class="{ sel: isSel('station', s.id) }"
              :points="triPts(s.x, s.y)"
              @pointerdown.stop="onElDown($event, 'station', s)"
            />
            <rect
              v-else
              class="station rain"
              :class="{ sel: isSel('station', s.id) }"
              :x="s.x - 5" :y="s.y - 5" width="10" height="10"
              :transform="`rotate(45 ${s.x} ${s.y})`"
              @pointerdown.stop="onElDown($event, 'station', s)"
            />
            <text
              v-if="showLabels"
              class="station-label"
              :class="{ hydro: s.type === 'hydro' }"
              :x="s.x + (s.type === 'hydro' ? 0 : 9)"
              :y="s.y - 10"
              @pointerdown.stop="onElDown($event, 'station', s)"
            >{{ s.name || '站点' }}</text>
          </template>
        </g>

        <!-- 节点 -->
        <g>
          <template v-for="n in w.nodes" :key="n.id">
            <circle
              class="node"
              :class="[n.kind, { sel: isSel('node', n.id), src: n.kind === 'source' }] "
              :cx="n.x" :cy="n.y"
              :r="n.kind === 'outlet' ? 9 : n.kind === 'source' ? 7 : 5.5"
              @pointerdown.stop="onElDown($event, 'node', n)"
            />
            <text
              v-if="showLabels && n.kind !== 'junction'"
              class="node-label"
              :x="n.x" :y="n.y + (n.kind === 'outlet' ? 24 : -14)"
              text-anchor="middle"
            >{{ nodeLabel(n) }}</text>
          </template>
        </g>

        <!-- 加线段：预览连线 -->
        <line v-if="linkFrom && linkHover" class="link-preview" :x1="linkFrom.x" :y1="linkFrom.y" :x2="cursor.x" :y2="cursor.y" />
      </svg>

      <!-- 概化图编辑浮动工具条 -->
      <SchematicEditToolbar />

      <!-- 选中对象信息弹窗 -->
      <SelectionPanel context="schematic" />

      <!-- 视图工具条 -->
      <div class="sch-toolbar">
        <button class="btn sm" @click="zoom(1.25)">＋</button>
        <button class="btn sm" @click="zoom(0.8)">－</button>
        <button class="btn sm" @click="fitView(true)">适配</button>
        <span class="dirty" v-if="state.schDirty">● 未保存</span>
      </div>

      <div class="sch-hint">
        {{ hint }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import {
  state,
  addSchematicElements,
  buildSchematic,
  clearSubbasinFocus,
  markDirty,
  subbasinEdgeStyle,
  toast
} from '../store'
import SchematicEditToolbar from './SchematicEditToolbar.vue'
import SelectionPanel from './SelectionPanel.vue'

const wrapEl = ref(null)
const svgEl = ref(null)

const vb = reactive({ x: 0, y: 0, w: 1000, h: 700 })
const pan = reactive({ on: false, sx: 0, sy: 0, vx: 0, vy: 0 })
const cursor = reactive({ x: 0, y: 0 })
const drag = reactive({ on: false, group: null, id: null, last: null })
const linkFrom = ref(null)
const fitted = ref(false)

const w = computed(() => state.schematicWorking)
const showLabels = computed(() => state.sch.showLabels)
const isSel = (kind, id) => state.selection && state.selection.source === 'schematic' && state.selection.kind === kind && state.selection.id === id

// ---------------------------------------------------------------- 河段按预报单元着色
const UNMAPPED_EDGE_COLOR = '#9db7c9' // 不属于任何单元的河段（如边界外、未划分部分）
const SEL_EDGE_COLOR = '#e67e22'

const sbStyle = computed(() => (state.sch.colorBySubbasin ? subbasinEdgeStyle() : null))

/** 河段描边色；返回 null 时回落到 CSS 默认色。 */
function edgeStroke(e) {
  if (isSel('edge', e.id)) return SEL_EDGE_COLOR
  const s = sbStyle.value
  if (!s) return null
  const code = s.edge[e.id]
  return code ? s.colors[code] : UNMAPPED_EDGE_COLOR
}

/** 河段中段箭头（箭头颜色跟随单元配色）。 */
function edgeMarker(e) {
  if (w.value.edges.length >= 120) return undefined
  const s = sbStyle.value
  const code = s ? s.edge[e.id] : null
  if (code) return `url(#sbArrow-${code})`
  return e.kind === 'extra' ? 'url(#arrowExtra)' : 'url(#arrowMain)'
}

/** 聚焦单元时，其他单元的河段淡化（选中的河段始终保持可见）。 */
function edgeDim(e) {
  const f = state.sch.focusSubbasin
  if (!f || !sbStyle.value) return false
  if (isSel('edge', e.id)) return false
  return sbStyle.value.edge[e.id] !== f
}

/** 聚焦单元的河段加粗。 */
function edgeFocus(e) {
  const f = state.sch.focusSubbasin
  return !!f && !!sbStyle.value && sbStyle.value.edge[e.id] === f && !isSel('edge', e.id)
}

const hint = computed(() => {
  if (state.sch.tool === 'addNode') return '加点模式：在空白处单击添加新节点'
  if (state.sch.tool === 'addEdge') return linkFrom.value ? '加点模式：再单击一个目标节点完成连线（Esc 取消）' : '加线段模式：先单击起始节点'
  if (state.sch.tool === 'addLake') return '加面模式：单击空白处放置湖泊'
  return '拖动节点 / 河段 / 站点 / 湖泊调整位置，单击查看属性；空白处拖动平移，滚轮缩放'
})

// ---------------------------------------------------------------- 视图
/** 由目标范围算出适配视图的 viewBox（保持画布宽高比）。 */
function targetView(x0, y0, x1, y1, padRatio, padMin) {
  const el = wrapEl.value
  const cw = el && el.clientWidth ? el.clientWidth : 1200
  const ch = el && el.clientHeight ? el.clientHeight : 800
  const padX = (x1 - x0) * padRatio + padMin
  const padY = (y1 - y0) * padRatio + padMin
  const bw = Math.max(1, x1 - x0 + padX * 2)
  const bh = Math.max(1, y1 - y0 + padY * 2)
  const scale = Math.min(cw / bw, ch / bh)
  const vw = cw / scale
  const vh = ch / scale
  return {
    x: x0 - padX - (vw - bw) / 2,
    y: y0 - padY - (vh - bh) / 2,
    w: vw,
    h: vh
  }
}

// viewBox 补间动画
let animFrame = null
function animateTo(t, dur = 340) {
  if (animFrame) {
    cancelAnimationFrame(animFrame)
    animFrame = null
  }
  if (dur <= 0) {
    Object.assign(vb, t)
    return
  }
  const from = { x: vb.x, y: vb.y, w: vb.w, h: vb.h }
  const t0 = performance.now()
  const step = (now) => {
    const k = Math.min(1, (now - t0) / dur)
    const e = 1 - Math.pow(1 - k, 3)
    vb.x = from.x + (t.x - from.x) * e
    vb.y = from.y + (t.y - from.y) * e
    vb.w = from.w + (t.w - from.w) * e
    vb.h = from.h + (t.h - from.h) * e
    if (k < 1) animFrame = requestAnimationFrame(step)
    else animFrame = null
  }
  animFrame = requestAnimationFrame(step)
}

function fitView(animate = false) {
  if (!w.value || !w.value.bbox) return
  const el = wrapEl.value
  if (!el || !el.clientWidth || !el.clientHeight) {
    // 容器尚未挂载或还没有尺寸（视图刚切换），稍后重试
    setTimeout(() => fitView(animate), 160)
    return
  }
  const [x0, y0, x1, y1] = w.value.bbox
  const t = targetView(x0, y0, x1, y1, 0.08, 60)
  if (animate) animateTo(t)
  else {
    if (animFrame) {
      cancelAnimationFrame(animFrame)
      animFrame = null
    }
    Object.assign(vb, t)
  }
  fitted.value = true
}

/** 某单元在概化图中的河段包围盒；无对应河段时返回 null。 */
function subbasinBox(code) {
  const s = sbStyle.value || subbasinEdgeStyle()
  if (!s || !w.value) return null
  let x0 = Infinity
  let y0 = Infinity
  let x1 = -Infinity
  let y1 = -Infinity
  let n = 0
  for (const e of w.value.edges || []) {
    if (s.edge[e.id] !== code) continue
    for (const p of e.points || []) {
      if (!Number.isFinite(p[0]) || !Number.isFinite(p[1])) continue
      x0 = Math.min(x0, p[0])
      y0 = Math.min(y0, p[1])
      x1 = Math.max(x1, p[0])
      y1 = Math.max(y1, p[1])
      n++
    }
  }
  return n ? { x0, y0, x1, y1, count: n } : null
}

/** 定位到某单元的河段范围（带重试，容器未就绪时等待）。 */
function applySubbasinFocus(silent = false) {
  const el = wrapEl.value
  if (!el || !el.clientWidth || !el.clientHeight) {
    setTimeout(() => applySubbasinFocus(silent), 160)
    return
  }
  const code = state.sch.focusSubbasin
  if (!code) return
  const box = subbasinBox(code)
  if (!box) {
    // 该单元在概化图上没有河段，撤销聚焦以免整图被淡化
    clearSubbasinFocus()
    if (!silent) toast('该单元在概化图中没有对应河段', 'warn')
    return
  }
  animateTo(targetView(box.x0, box.y0, box.x1, box.y1, 0.35, 40))
}

/** 视图首次就绪：先适配全图；若已存在聚焦单元，则直接定位到该单元。 */
function initView() {
  const el = wrapEl.value
  if (!el || !el.clientWidth || !el.clientHeight) {
    setTimeout(initView, 160)
    return
  }
  fitView()
  if (state.sch.focusSubbasin) applySubbasinFocus(true)
  fitted.value = true
}

function zoom(f, cx, cy) {
  if (cx === undefined) {
    cx = vb.x + vb.w / 2
    cy = vb.y + vb.h / 2
  }
  vb.w /= f
  vb.h /= f
  vb.x = cx - (cx - vb.x) / f
  vb.y = cy - (cy - vb.y) / f
}

function onWheel(ev) {
  const el = wrapEl.value
  const r = el.getBoundingClientRect()
  const px = vb.x + ((ev.clientX - r.left) / r.width) * vb.w
  const py = vb.y + ((ev.clientY - r.top) / r.height) * vb.h
  zoom(ev.deltaY < 0 ? 1.12 : 1 / 1.12, px, py)
}

function svgPoint(ev) {
  const el = wrapEl.value
  const r = el.getBoundingClientRect()
  const scale = Math.min(r.width / vb.w, r.height / vb.h)
  const ox = (r.width - vb.w * scale) / 2
  const oy = (r.height - vb.h * scale) / 2
  return {
    x: vb.x + (ev.clientX - r.left - ox) / scale,
    y: vb.y + (ev.clientY - r.top - oy) / scale
  }
}

function onBgDown(ev) {
  if (ev.button !== 0) return
  const p = svgPoint(ev)
  cursor.x = p.x
  cursor.y = p.y

  // 工具模式：单击空白
  if (state.sch.tool === 'addNode') {
    addNodeAt(p)
    return
  }
  if (state.sch.tool === 'addLake') {
    addLakeAt(p)
    return
  }
  if (state.sch.tool === 'addEdge' && linkFrom.value) {
    linkFrom.value = null
    return
  }

  // 平移
  pan.on = true
  pan.sx = ev.clientX
  pan.sy = ev.clientY
  pan.vx = vb.x
  pan.vy = vb.y
  state.selection = null
  window.addEventListener('pointermove', onPanMove)
  window.addEventListener('pointerup', onPanUp)
}

function onPanMove(ev) {
  const el = wrapEl.value
  const r = el.getBoundingClientRect()
  const scale = Math.min(r.width / vb.w, r.height / vb.h)
  vb.x = pan.vx - (ev.clientX - pan.sx) / scale
  vb.y = pan.vy - (ev.clientY - pan.sy) / scale
}

function onPanUp() {
  pan.on = false
  window.removeEventListener('pointermove', onPanMove)
  window.removeEventListener('pointerup', onPanUp)
}

// ---------------------------------------------------------------- 拖拽元素
function onElDown(ev, group, item) {
  if (ev.button !== 0) return
  const p = svgPoint(ev)
  cursor.x = p.x
  cursor.y = p.y

  if (state.sch.tool === 'addEdge') {
    if (group !== 'node') return
    if (!linkFrom.value) {
      linkFrom.value = item
    } else if (linkFrom.value.id !== item.id) {
      addEdgeBetween(linkFrom.value, item)
      linkFrom.value = null
    }
    return
  }

  // 选中
  state.selection = {
    source: 'schematic',
    kind: group === 'node' ? 'node' : group,
    id: item.id,
    title: item.name || '',
    stype: group === 'station' ? item.type : undefined,
    props: elProps(group, item)
  }

  if (state.sch.tool !== 'select') return

  drag.on = true
  drag.group = group
  drag.id = item.id
  drag.last = p
  window.addEventListener('pointermove', onDragMove)
  window.addEventListener('pointerup', onDragUp)
}

function elProps(group, item) {
  const p = { ...(item.props || {}) }
  if (group === 'node') {
    p['类型'] = { source: '河源', junction: '汇流点', outlet: '出口' }[item.kind] || item.kind
    p['层级'] = item.level
    if (item.upstream_km) p['上游河长(km)'] = item.upstream_km
  } else if (group === 'edge') {
    p['长度(km)'] = item.length_km
    p['上游河长(km)'] = item.upstream_km
    p['级别'] = item.order
  } else if (group === 'station') {
    if (item.upstream_area_km2) p['上游面积(km²)'] = item.upstream_area_km2
    p['挂接状态'] = item.attached ? '已挂接河段' : '未挂接'
  } else if (group === 'lake') {
    if (item.area_km2) p['面积(km²)'] = item.area_km2
  }
  return p
}

function onDragMove(ev) {
  if (!drag.on) return
  const p = svgPoint(ev)
  const dx = p.x - drag.last.x
  const dy = p.y - drag.last.y
  drag.last = p

  const ww = w.value
  if (!ww) return

  if (drag.group === 'node') {
    const n = ww.nodes.find((x) => x.id === drag.id)
    if (!n) return
    n.x = +(n.x + dx).toFixed(2)
    n.y = +(n.y + dy).toFixed(2)
    for (const e of ww.edges) {
      if (e.from_node === drag.id) {
        e.points[0] = [n.x, n.y]
      }
      if (e.to_node === drag.id) {
        e.points[e.points.length - 1] = [n.x, n.y]
      }
    }
  } else if (drag.group === 'edge') {
    const e = ww.edges.find((x) => x.id === drag.id)
    if (!e) return
    for (const pt of e.points) {
      pt[0] += dx
      pt[1] += dy
    }
    // 端点保持粘在节点上
    const n0 = ww.nodes.find((x) => x.id === e.from_node)
    const n1 = ww.nodes.find((x) => x.id === e.to_node)
    if (n0) e.points[0] = [n0.x, n0.y]
    if (n1) e.points[e.points.length - 1] = [n1.x, n1.y]
    // 站点挂在该河段上的，跟随拐点中点大致移动
    for (const s of ww.stations) {
      if (s.edge_id === e.id && s.on_edge) {
        s.x = +(s.x + dx).toFixed(2)
        s.y = +(s.y + dy).toFixed(2)
        s.on_edge = [s.on_edge[0] + dx, s.on_edge[1] + dy]
      }
    }
  } else if (drag.group === 'station') {
    const s = ww.stations.find((x) => x.id === drag.id)
    if (!s) return
    s.x = +(s.x + dx).toFixed(2)
    s.y = +(s.y + dy).toFixed(2)
  } else if (drag.group === 'lake') {
    const lk = ww.lakes.find((x) => x.id === drag.id)
    if (!lk) return
    lk.x = +(lk.x + dx).toFixed(2)
    lk.y = +(lk.y + dy).toFixed(2)
    for (const pt of lk.points) {
      pt[0] += dx
      pt[1] += dy
    }
  }
  markDirty()
}

function onDragUp() {
  drag.on = false
  window.removeEventListener('pointermove', onDragMove)
  window.removeEventListener('pointerup', onDragUp)
}

// ---------------------------------------------------------------- 添加元素
let addSeq = 0
function newId(prefix) {
  addSeq += 1
  return `${prefix}_m${Date.now().toString(36)}${addSeq}`
}

async function addNodeAt(p) {
  await addSchematicElements({
    nodes: [
      {
        id: newId('N'),
        x: +p.x.toFixed(2),
        y: +p.y.toFixed(2),
        kind: 'junction',
        level: 0,
        geo: null,
        station_ids: [],
        edge_ids: []
      }
    ]
  })
  markDirty()
}

async function addEdgeBetween(n1, n2) {
  await addSchematicElements({
    edges: [
      {
        id: newId('E'),
        from_node: n1.id,
        to_node: n2.id,
        name: '',
        order: 1,
        kind: 'extra',
        points: [
          [n1.x, n1.y],
          [n2.x, n2.y]
        ],
        geo: null
      }
    ]
  })
  markDirty()
  toast('已添加线段（保存后持久化）', 'ok')
}

async function addLakeAt(p) {
  const r = 30
  const pts = []
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * Math.PI * 2
    pts.push([+(p.x + Math.cos(a) * r * (1 + (i % 2) * 0.25)).toFixed(2), +(p.y + Math.sin(a) * r * 0.7).toFixed(2)])
  }
  await addSchematicElements({
    lakes: [
      {
        id: newId('L'),
        name: '新湖泊',
        area_km2: null,
        edge_id: null,
        x: +p.x.toFixed(2),
        y: +p.y.toFixed(2),
        points: pts,
        geo: null,
        props: {}
      }
    ]
  })
  markDirty()
  toast('已添加湖泊（保存后持久化）', 'ok')
}

// ---------------------------------------------------------------- 渲染辅助
function ptsStr(points) {
  return points.map((p) => `${p[0]},${p[1]}`).join(' ')
}

function midOf(points) {
  if (!points || points.length < 2) return [0, 0]
  const i = Math.floor((points.length - 1) / 2)
  return [(points[i][0] + points[i + 1][0]) / 2, (points[i][1] + points[i + 1][1]) / 2]
}

function triPts(x, y) {
  return `${x - 7},${y + 6} ${x + 7},${y + 6} ${x},${y - 8}`
}

function nodeLabel(n) {
  if (n.kind === 'outlet') return '出口'
  if (n.kind === 'source') return '河源'
  return ''
}

const linkHover = computed(() => state.sch.tool === 'addEdge')

// ---------------------------------------------------------------- 生命周期
function onKey(ev) {
  if (ev.key === 'Escape') {
    linkFrom.value = null
    state.selection = null
  }
}

onMounted(() => {
  window.addEventListener('keydown', onKey)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  window.removeEventListener('pointermove', onDragMove)
  window.removeEventListener('pointerup', onDragUp)
  window.removeEventListener('pointermove', onPanMove)
  window.removeEventListener('pointerup', onPanUp)
  if (animFrame) {
    cancelAnimationFrame(animFrame)
    animFrame = null
  }
})

watch(
  () => state.schematicWorking,
  (nv) => {
    if (nv && !fitted.value) {
      requestAnimationFrame(() => initView())
    }
  },
  { immediate: true }
)

watch(
  () => state.project && state.project.id,
  () => {
    fitted.value = false
  }
)

// 侧栏 / 图例请求聚焦某个预报单元：定位到该单元的河段范围；code=null 表示回到适配视图
watch(
  () => state.sch.zoomToSubbasin,
  (req) => {
    if (!req) return
    requestAnimationFrame(() => {
      if (!w.value || !w.value.bbox) return
      if (!req.code) {
        fitView(true)
        return
      }
      applySubbasinFocus()
    })
  }
)
</script>

<style scoped>
.sch-wrap {
  position: relative;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: #fdfefe;
}
.sch-empty {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: #7a8a99;
}
.sch-empty .big {
  font-size: 18px;
  font-weight: 600;
  color: #4a5a68;
}
.sch-canvas {
  position: absolute;
  inset: 0;
  cursor: default;
  user-select: none;
}
.sch-canvas.panning {
  cursor: grabbing;
}
.sch-canvas.linking {
  cursor: crosshair;
}
svg {
  width: 100%;
  height: 100%;
  display: block;
}
.edge {
  fill: none;
  stroke: #2f8ac4;
  stroke-width: 2.6;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.edge.extra {
  stroke: #9db7c9;
  stroke-width: 2;
  stroke-dasharray: 7 4;
}
.edge.sel {
  stroke: #e67e22;
  stroke-width: 4;
}
.edge.dim {
  opacity: 0.16;
}
.edge.focus {
  stroke-width: 4.4;
}
.edge-label.dim {
  opacity: 0.25;
}
.edge-hit {
  fill: none;
  stroke: transparent;
  stroke-width: 14;
  cursor: move;
}
.edge-label {
  font-size: 11px;
  fill: #34647f;
  text-anchor: middle;
  pointer-events: none;
}
.node {
  fill: #5a6b7a;
  stroke: #fff;
  stroke-width: 1.6;
  cursor: move;
}
.node.source {
  fill: #1f7a9e;
}
.node.outlet {
  fill: #c0392b;
}
.node.sel {
  stroke: #e67e22;
  stroke-width: 2.4;
}
.node-label {
  font-size: 11px;
  fill: #6a7a88;
  pointer-events: none;
}
.station {
  cursor: move;
}
.station.hydro {
  fill: #c0392b;
  stroke: #fff;
  stroke-width: 1.4;
}
.station.rain {
  fill: #1f7a4d;
  stroke: #fff;
  stroke-width: 1.4;
}
.station.sel {
  stroke: #e67e22;
  stroke-width: 2.4;
}
.station-label {
  font-size: 11px;
  fill: #8a3a30;
  text-anchor: middle;
  pointer-events: none;
}
.station-label.hydro {
  fill: #c0392b;
}
.lake {
  fill: rgba(110, 192, 224, 0.45);
  stroke: #4aa3c7;
  stroke-width: 1.6;
  cursor: move;
}
.lake.sel {
  stroke: #e67e22;
  stroke-width: 2.6;
}
.lake-label {
  font-size: 12px;
  fill: #2a6a86;
  text-anchor: middle;
  pointer-events: none;
}
.link-preview {
  stroke: #e67e22;
  stroke-width: 2;
  stroke-dasharray: 6 4;
}
.sch-toolbar {
  position: absolute;
  top: 10px;
  right: 12px;
  display: flex;
  align-items: center;
  gap: 6px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid #dde5ec;
  border-radius: 8px;
  padding: 5px 8px;
  box-shadow: 0 2px 8px rgba(30, 50, 70, 0.08);
}
.dirty {
  color: #e67e22;
  font-size: 12px;
  margin-left: 4px;
}
.sch-hint {
  position: absolute;
  left: 12px;
  bottom: 10px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid #dde5ec;
  border-radius: 8px;
  padding: 5px 10px;
  font-size: 12px;
  color: #6a7a88;
  box-shadow: 0 2px 8px rgba(30, 50, 70, 0.08);
}
</style>
