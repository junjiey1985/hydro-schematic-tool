import { reactive, computed } from 'vue'
import { api } from './api'

export const state = reactive({
  ready: false,
  projects: [],
  project: null,
  layers: [],
  layerData: {},        // lid -> FeatureCollection | null（懒加载）
  topology: null,
  schematic: null,
  schematicWorking: null,
  schDirty: false,
  dem: null,
  subbasins: null,          // 子流域划分结果（含 subbasins / stats / rows）
  subOptions: null,         // /subbasins/options 返回的默认参数与候选控制断面
  tab: 'map',
  selection: null,      // {source:'map'|'schematic', kind, layerId, featureId, ...}
  busy: '',
  toast: { show: false, text: '', type: 'info' },
  // 地图设置
  map: {
    basemap: 'none',    // none（默认无底图） | amap（高德矢量） | amap_sat（高德影像）
    showTopology: true,
    showRelief: false,  // 高程晕渲默认关闭（勾选后显示，透明度滑杆随之出现）
    reliefOpacity: 0.75,
    showLabels: true,
    showSubbasins: true,
    subbasinLabels: true,
    subbasinOpacity: 0.32,
    focus: null,        // { extent, ts } 地图定位请求
    pickSubbasin: null, // { code, ts } 选中某个子流域单元的请求
    drawType: null,     // Point | LineString | Polygon
    drawTarget: null,   // 目标图层 id
    modify: false
  },
  // 概化图设置
  sch: {
    orientation: 'vertical',
    level_gap: 110,
    leaf_gap: 64,
    station_offset: 34,
    tool: 'select',
    showLabels: true,
    colorBySubbasin: true,  // 河段按所属预报单元着色
    focusSubbasin: null,    // 聚焦某个预报单元（其余河段淡化）
    zoomToSubbasin: null,   // { code, ts } 定位到某单元的河段范围；code=null 表示回到适配视图
    unmappedTone: 'gray'    // 未归属单元的河段：gray | blue
  },
  stats: { nodes: 0, edges: 0, stations: 0, lakes: 0, subbasins: 0 }
})

export const projectId = computed(() => (state.project ? state.project.id : null))

export const layerColor = (type) => {
  return (
    {
      river: '#2f8ac4',
      hydro_station: '#c0392b',
      rain_station: '#1f7a4d',
      lake: '#6ec0e0',
      boundary: '#8a97a5',
      subbasin: '#7f5fc4',
      control_point: '#b7791f',
      other: '#8a97a5'
    }[type] || '#8a97a5'
  )
}

export const layerTypeLabel = (type) =>
  ({
    river: '河流水系',
    hydro_station: '水文站',
    rain_station: '雨量站',
    lake: '湖泊水库',
    boundary: '流域边界',
    subbasin: '子流域',
    control_point: '控制断面',
    other: '其他'
  }[type] || '其他')

// 子流域分色板：按排水序/先后次序取色，便于在地图上区分预报单元
const SUBBASIN_PALETTE = [
  '#4f7fd4', '#e07a3f', '#4aa06a', '#b4568f', '#d8a63a',
  '#5aa8c4', '#8d6bc4', '#c05a5a', '#7a9e34', '#3f8f8a'
]
export const subbasinColor = (i, alpha = 1) => {
  const hex = SUBBASIN_PALETTE[((i % SUBBASIN_PALETTE.length) + SUBBASIN_PALETTE.length) % SUBBASIN_PALETTE.length]
  if (alpha >= 1) return hex
  const n = parseInt(hex.slice(1), 16)
  const r = (n >> 16) & 255
  const g = (n >> 8) & 255
  const b = n & 255
  return `rgba(${r},${g},${b},${alpha})`
}

let toastTimer = null
export function toast(text, type = 'info', ms = 2600) {
  state.toast = { show: true, text, type }
  if (toastTimer) clearTimeout(toastTimer)
  toastTimer = setTimeout(() => (state.toast.show = false), ms)
}

export async function withBusy(label, fn) {
  state.busy = label
  try {
    return await fn()
  } catch (e) {
    toast(e.message || String(e), 'err', 5200)
    throw e
  } finally {
    state.busy = ''
  }
}

// ---------------------------------------------------------------- 项目
export async function loadProjects() {
  const r = await api.listProjects()
  state.projects = r.projects || []
}

export async function bootstrap() {
  state.ready = false
  try {
    await loadProjects()
    if (state.projects.length) {
      await openProject(state.projects[0].id)
    }
  } catch (e) {
    toast('初始化失败：' + e.message, 'err')
  }
  state.ready = true
}

export async function createProject(name, description) {
  const p = await api.createProject(name, description)
  await loadProjects()
  await openProject(p.id)
  toast('项目已创建', 'ok')
  return p
}

export async function importSamples(payload = {}) {
  return withBusy('正在导入示例流域（拷贝图层与 DEM → 构建拓扑 → 生成概化图）…', async () => {
    const r = await api.importSamples(payload)
    await loadProjects()
    await openProject(r.project.id)
    toast('示例流域数据已导入', 'ok')
  })
}

export async function openProject(pid) {
  return withBusy('正在载入项目…', async () => {
    const same = state.project && state.project.id === pid
    state.project = await api.getProject(pid)
    if (!same) state.layerData = {} // 换项目才清空；重复打开保留缓存
    state.selection = null
    state.topology = null
    state.schematic = null
    state.schematicWorking = null
    state.schDirty = false
    state.subbasins = null
    state.dem = state.project.dem || null
    await refreshLayers()
    await loadAnalysis()
    // 显式补齐要素数据：重复打开时图层列表 watch（字符串比较）不会触发，
    // 若此处不加载，地图将只剩拓扑图层
    await loadAllLayerData()
  })
}

export async function removeProject(pid) {
  await api.deleteProject(pid)
  await loadProjects()
  if (state.project && state.project.id === pid) {
    state.project = null
    state.layers = []
    state.layerData = {}
    state.topology = null
    state.schematic = null
    state.schematicWorking = null
    state.subbasins = null
  }
  if (state.projects.length) await openProject(state.projects[0].id)
  toast('项目已删除', 'ok')
}

// ---------------------------------------------------------------- 图层
export async function refreshLayers() {
  if (!projectId.value) return
  const r = await api.listLayers(projectId.value)
  state.layers = r.layers || []
  // 清理已删除图层的数据
  const ids = new Set(state.layers.map((l) => l.id))
  for (const k of Object.keys(state.layerData)) if (!ids.has(k)) delete state.layerData[k]
}

export async function loadLayerData(lid, force = false) {
  if (!force && state.layerData[lid]) return state.layerData[lid]
  const r = await api.getLayer(projectId.value, lid)
  state.layerData[lid] = r.geojson
  return r.geojson
}

export async function loadAllLayerData() {
  await Promise.all(state.layers.filter((l) => l.visible).map((l) => loadLayerData(l.id).catch(() => null)))
  // 换新对象触发 MapView 对 state.layerData 的 watch（原地赋值不会触发重建）
  state.layerData = { ...state.layerData }
}

export async function toggleLayerVisible(layer) {
  layer.visible = !layer.visible
  await api.updateLayer(projectId.value, layer.id, { visible: layer.visible })
  if (layer.visible) await loadLayerData(layer.id)
}

export async function deleteLayer(layer) {
  await api.deleteLayer(projectId.value, layer.id)
  const wasRiver = layer.type === 'river' || layer.type === 'hydro_station' || layer.type === 'rain_station' || layer.type === 'lake'
  await refreshLayers()
  await loadAllLayerData()
  if (wasRiver && state.topology) {
    state.topology = null
    state.schematic = null
    state.schematicWorking = null
  } else {
    await loadAnalysis()
  }
  toast('图层已删除', 'ok')
}

export async function renameLayer(layer, name) {
  await api.updateLayer(projectId.value, layer.id, { name })
  await refreshLayers()
}

export async function uploadShp(files, opts) {
  return withBusy('正在导入 SHP…', async () => {
    const r = await api.uploadShp(projectId.value, files, opts)
    await refreshLayers()
    await loadAllLayerData()
    toast(`导入完成，共 ${r.results.filter((x) => x.ok).length} 个图层`, 'ok')
    return r
  })
}

export async function saveDrawnFeatures(layerId, features) {
  await api.addFeatures(projectId.value, layerId, features)
  await loadLayerData(layerId, true)
  await refreshLayers()
}

// ---------------------------------------------------------------- 分析
export async function loadAnalysis() {
  if (!projectId.value) return
  const [topo, sch, sub] = await Promise.all([
    api.getTopology(projectId.value).catch(() => null),
    api.getSchematic(projectId.value).catch(() => null),
    api.getSubbasins(projectId.value).catch(() => null)
  ])
  state.topology = topo && topo.ok ? topo : null
  state.schematic = sch && sch.ok ? sch : null
  state.schematicWorking = state.schematic ? JSON.parse(JSON.stringify(state.schematic)) : null
  state.schDirty = false
  state.subbasins = sub && sub.exists ? sub : null
  updateStats()
}

export function updateStats() {
  const t = state.topology
  state.stats = {
    nodes: t ? t.nodes.length : 0,
    edges: t ? t.edges.length : 0,
    stations: t ? t.stations.length : 0,
    lakes: t ? t.lakes.length : 0,
    subbasins: state.subbasins ? state.subbasins.subbasins.length : 0
  }
}

export async function buildTopology(options) {
  return withBusy('正在构建河网拓扑关系…', async () => {
    const t = await api.buildTopology(projectId.value, { options: options || {}, use_dem: true })
    state.topology = t
    updateStats()
    await buildSchematic({ silent: true })
    toast(
      `拓扑构建完成：节点 ${t.stats.node_count}、河段 ${t.stats.edge_count}、出口 ${t.stats.outlet_count}`,
      'ok'
    )
    return t
  })
}

export async function buildSchematic({ reset = false, silent = false } = {}) {
  return withBusy(silent ? '' : '正在生成概化图…', async () => {
    const payload = { options: { ...state.sch }, keep_manual: !reset }
    const s = reset
      ? await api.resetSchematic(projectId.value, payload)
      : await api.buildSchematic(projectId.value, payload)
    state.schematic = s
    state.schematicWorking = JSON.parse(JSON.stringify(s))
    state.schDirty = false
    if (!silent) toast('概化图已生成', 'ok')
    return s
  })
}

export function collectSchematicEdits() {
  const w = state.schematicWorking
  if (!w) return {}
  const nodes = {}
  for (const n of w.nodes) nodes[n.id] = { x: n.x, y: n.y }
  const edges = {}
  for (const e of w.edges) edges[e.id] = { points: e.points }
  const stations = {}
  for (const s of w.stations) stations[s.id] = { x: s.x, y: s.y, name: s.name, type: s.type }
  const lakes = {}
  for (const l of w.lakes) lakes[l.id] = { x: l.x, y: l.y, points: l.points, name: l.name }
  return { nodes, edges, stations, lakes }
}

export async function saveSchematic() {
  if (!state.schematicWorking) return
  return withBusy('正在保存概化图…', async () => {
    const edits = collectSchematicEdits()
    const r = await api.editSchematic(projectId.value, edits)
    state.schematic = r.schematic
    state.schematicWorking = JSON.parse(JSON.stringify(r.schematic))
    state.schDirty = false
    toast('概化图已保存', 'ok')
  })
}

export async function addSchematicElements(payload) {
  return withBusy('正在添加…', async () => {
    const r = await api.editSchematic(projectId.value, { add: payload })
    state.schematic = r.schematic
    state.schematicWorking = JSON.parse(JSON.stringify(r.schematic))
    state.schDirty = false
    return r.schematic
  })
}

export async function removeSchematicElement(group, id) {
  return withBusy('正在删除…', async () => {
    const r = await api.editSchematic(projectId.value, { remove: { [group]: [id] } })
    state.schematic = r.schematic
    state.schematicWorking = JSON.parse(JSON.stringify(r.schematic))
    state.schDirty = false
  })
}

export function markDirty() {
  state.schDirty = true
}

// ---------------------------------------------------------------- DEM
export async function uploadDem(file) {
  return withBusy('正在上传并解析 DEM…', async () => {
    const r = await api.uploadDem(projectId.value, file)
    state.dem = r.dem
    state.project.dem = r.dem
    toast('DEM 上传成功', 'ok')
    return r
  })
}

export async function extractFromDem(payload) {
  return withBusy('正在从 DEM 提取河网（填洼 / 流向 / 汇流累积 / 矢量化）…', async () => {
    const r = await api.extractDem(projectId.value, payload)
    await refreshLayers()
    await loadAllLayerData()
    toast(`河网提取完成：${r.segment_count} 个河段，总长 ${r.total_length_km} km`, 'ok')
    return r
  })
}

export async function deleteDem() {
  await api.deleteDem(projectId.value)
  state.dem = null
  if (state.project) state.project.dem = null
  state.subbasins = null
  toast('DEM 已移除', 'ok')
}

// ---------------------------------------------------------------- 子流域 / 预报单元
export const subbasinLayer = computed(() => state.layers.find((l) => l.type === 'subbasin') || null)
export const controlPointLayer = computed(() => state.layers.find((l) => l.type === 'control_point') || null)

/** 子流域列表（按排水序排列，即上游 → 下游） */
export const subbasinList = computed(() => (state.subbasins ? state.subbasins.subbasins || [] : []))
export const subbasinStats = computed(() => (state.subbasins ? state.subbasins.stats || {} : {}))

export async function loadSubbasins() {
  if (!projectId.value) return null
  const r = await api.getSubbasins(projectId.value).catch(() => null)
  state.subbasins = r && r.exists ? r : null
  updateStats()
  return state.subbasins
}

export async function loadSubbasinOptions(force = false) {
  if (!projectId.value) return null
  if (!force && state.subOptions) return state.subOptions
  state.subOptions = await api.subbasinOptions(projectId.value).catch(() => null)
  return state.subOptions
}

export async function delineateSubbasins(payload = {}) {
  return withBusy('正在划分子流域（控制断面吸附 → 汇流追踪 → 面积与雨量权重统计）…', async () => {
    const r = await api.delineateSubbasins(projectId.value, { save: true, ...payload })
    const full = await api.getSubbasins(projectId.value).catch(() => null)
    state.subbasins = full && full.exists ? full : { ...r, exists: true }
    await refreshLayers()
    await loadAllLayerData()
    if (state.map.showSubbasins) state.map.showSubbasins = true
    updateStats()
    toast(
      `划分完成：${r.stats.subbasin_count} 个预报单元，合计 ${r.stats.total_area_km2} km²`,
      'ok',
      4200
    )
    return state.subbasins
  })
}

export async function renameSubbasin(code, name) {
  const r = await api.patchSubbasin(projectId.value, code, { name })
  state.subbasins = { ...r, exists: true }
  const l = subbasinLayer.value
  if (l) await loadLayerData(l.id, true).catch(() => null)
  toast('名称已更新', 'ok')
}

export async function clearSubbasins() {
  return withBusy('正在清除子流域划分…', async () => {
    await api.clearSubbasins(projectId.value)
    state.subbasins = null
    await refreshLayers()
    await loadAllLayerData()
    updateStats()
    toast('已清除子流域划分', 'ok')
  })
}

/** 确保存在「控制断面」图层，供手工划分使用；返回该图层。 */
export async function ensureControlPointLayer() {
  let layer = controlPointLayer.value
  if (layer) return layer
  const info = await api.createLayer(projectId.value, {
    name: '控制断面',
    type: 'control_point',
    features: [],
    source: 'draw'
  })
  await refreshLayers()
  layer = state.layers.find((l) => l.id === info.id) || info
  state.layerData[layer.id] = { type: 'FeatureCollection', features: [] }
  return layer
}

/** 让地图定位到某个子流域（或任意范围）。 */
export function focusExtent(extent) {
  if (!extent || extent.some((v) => v === null || v === undefined || Number.isNaN(v))) return
  state.map.focus = { extent: [...extent], ts: Date.now() }
}

/** 在地图上选中某个子流域单元（定位 + 高亮 + 属性面板）。 */
export function pickSubbasin(code) {
  if (!code) return
  state.map.pickSubbasin = { code, ts: Date.now() }
}

/**
 * 在概化图中聚焦某个预报单元：着色开关自动打开，河段高亮 + 其余淡化，
 * 并请求把视图定位到该单元的河段范围（再次调用同一单元则退出聚焦并回到适配视图）。
 */
export function focusSubbasinInSchematic(code) {
  if (!code) return
  state.sch.colorBySubbasin = true
  const next = state.sch.focusSubbasin === code ? null : code
  state.sch.focusSubbasin = next
  state.sch.zoomToSubbasin = { code: next, ts: Date.now() }
}

/** 退出概化图的单元聚焦（不改变当前视图）。 */
export function clearSubbasinFocus() {
  state.sch.focusSubbasin = null
  state.sch.zoomToSubbasin = null
}

/** 某个单元的显示色（与地图渲染保持一致）。 */
export function subbasinColorOf(sb) {
  return subbasinColor(Math.max(0, Number(sb && sb.order_index ? sb.order_index : 1) - 1))
}

/**
 * 概化图河段着色表：由「单元 → 河段」映射反推「河段 → 单元配色」。
 * 返回 { list:[{code,name,color,area_km2}], colors:{code:hex}, edge:{edgeId:code} }；无子流域时返回 null。
 */
export function subbasinEdgeStyle() {
  const d = state.subbasins
  if (!d) return null
  const list = (d.subbasins || []).map((sb) => ({
    code: sb.code,
    name: sb.name,
    color: subbasinColorOf(sb),
    area_km2: sb.area_km2,
    order_index: sb.order_index
  }))
  const colors = {}
  for (const it of list) colors[it.code] = it.color
  const edge = {}
  for (const [eid, code] of Object.entries(d.edge_subbasin || {})) {
    if (code && colors[code]) edge[eid] = code
  }
  return { list, colors, edge }
}

/** 从 GeoJSON 几何求 bbox（支持 Polygon / MultiPolygon）。 */
export function geomExtent(geom) {
  if (!geom) return null
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity
  const walk = (c) => {
    if (typeof c[0] === 'number') {
      if (c[0] < minx) minx = c[0]
      if (c[0] > maxx) maxx = c[0]
      if (c[1] < miny) miny = c[1]
      if (c[1] > maxy) maxy = c[1]
      return
    }
    for (const x of c) walk(x)
  }
  if (geom.coordinates) walk(geom.coordinates)
  if (!Number.isFinite(minx)) return null
  return [minx, miny, maxx, maxy]
}
