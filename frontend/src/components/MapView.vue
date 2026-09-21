<template>
  <div class="map-wrap">
    <div ref="el" class="map"></div>

    <!-- 手工绘制浮动工具条 -->
    <MapDrawToolbar />

    <!-- 选中对象信息弹窗 -->
    <SelectionPanel context="map" />

    <!-- 地图控件 -->
    <div class="map-ctrl tr">
      <div class="ctl-row">
        <span class="ctl-lab">底图</span>
        <select v-model="state.map.basemap" @change="syncBase">
          <option v-for="b in BASEMAPS" :key="b.value" :value="b.value">{{ b.label }}</option>
        </select>
      </div>
      <div class="ctl-note" v-if="state.map.basemap !== 'none'">
        底图 © 高德地图（GCJ-02，要素已自动纠偏对齐）
      </div>
      <label class="chk" v-if="state.dem">
        <input type="checkbox" v-model="state.map.showRelief" @change="syncRelief" /> 高程晕渲
      </label>
      <div class="ctl-row" v-if="state.dem && state.map.showRelief">
        <span class="ctl-lab">透明</span>
        <input
          type="range"
          min="0.15"
          max="1"
          step="0.05"
          v-model.number="state.map.reliefOpacity"
          @input="syncRelief"
        />
      </div>
      <label class="chk">
        <input type="checkbox" v-model="state.map.showTopology" /> 拓扑关系图层
      </label>
      <template v-if="hasSubbasins">
        <label class="chk">
          <input type="checkbox" v-model="state.map.showSubbasins" /> 子流域单元
        </label>
        <label class="chk" v-if="state.map.showSubbasins">
          <input type="checkbox" v-model="state.map.subbasinLabels" /> 单元编号与面积
        </label>
        <div class="ctl-row" v-if="state.map.showSubbasins">
          <span class="ctl-lab">透明</span>
          <input
            type="range"
            min="0.05"
            max="0.75"
            step="0.03"
            v-model.number="state.map.subbasinOpacity"
          />
        </div>
      </template>
      <label class="chk">
        <input type="checkbox" v-model="state.map.showLabels" /> 站点名称
      </label>
    </div>

    <div class="map-ctrl bl">
      <div class="legend-title">图例</div>
      <div class="legend-row"><i class="sw river"></i>河流水系（线宽=级别）</div>
      <div class="legend-row"><i class="sw hydro"></i>水文站</div>
      <div class="legend-row"><i class="sw rain"></i>雨量站</div>
      <div class="legend-row"><i class="sw lake"></i>湖泊 / 水库</div>
      <div class="legend-row"><i class="sw node"></i>汇流节点</div>
      <div class="legend-row"><i class="sw outlet"></i>流域出口</div>
      <div class="legend-row"><i class="sw arrow"></i>河流流向</div>
      <div class="legend-row" v-if="hasSubbasins"><i class="sw subbasin"></i>子流域（预报单元）</div>
      <div class="legend-row" v-if="hasSubbasins"><i class="sw suboutlet"></i>子流域出口断面</div>
      <div class="legend-row" v-if="hasControlPoints"><i class="sw ctrl"></i>手工控制断面</div>
    </div>

    <div class="map-tip" v-if="state.map.drawType">正在绘制：{{ drawLabel }}（双击 / 右键结束折线）</div>
    <div class="map-tip edit" v-else-if="state.map.modify">编辑模式：拖动顶点修改要素，松开即保存</div>
  </div>
</template>

<script setup>
import 'ol/ol.css'
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import OLMap from 'ol/Map'
import View from 'ol/View'
import TileLayer from 'ol/layer/Tile'
import VectorLayer from 'ol/layer/Vector'
import ImageLayer from 'ol/layer/Image'
import VectorSource from 'ol/source/Vector'
import ImageStatic from 'ol/source/ImageStatic'
import GeoJSON from 'ol/format/GeoJSON'
import Feature from 'ol/Feature'
import Point from 'ol/geom/Point'
import LineString from 'ol/geom/LineString'
import { Style, Stroke, Fill, Circle as CircleStyle, RegularShape, Text as TextStyle } from 'ol/style'
import Draw from 'ol/interaction/Draw'
import Modify from 'ol/interaction/Modify'
import Snap from 'ol/interaction/Snap'
import { boundingExtent } from 'ol/extent'
import {
  loadAllLayerData,
  loadLayerData,
  layerTypeLabel,
  saveDrawnFeatures,
  state,
  subbasinColor,
  toast,
  withBusy
} from '../store'
import { api } from '../api'
import { BASEMAPS, createAmapSource } from '../amap'
import {
  coordsToGcj,
  extentToGcj,
  gcj02ToWgs84,
  geometryToGcj,
  geometryToWgs,
  wgs84ToGcj02
} from '../gcj'
import MapDrawToolbar from './MapDrawToolbar.vue'
import SelectionPanel from './SelectionPanel.vue'

const el = ref(null)
const geoJSON = new GeoJSON({ dataProjection: 'EPSG:4326', featureProjection: 'EPSG:4326' })

let map = null
let baseLayer = null
let satLayer = null
let satLabelLayer = null
let reliefLayer = null
let topoLayer = null
let hiLayer = null
let drawLayer = null
let drawSource = null
let drawInteraction = null
let modifyInteraction = null
let snapInteraction = null
const vecLayers = new Map() // lid -> VectorLayer

const clickTargets = []

const drawLabel = ref('')

const COLORS = {
  river: '#2f8ac4',
  hydro_station: '#c0392b',
  rain_station: '#1f7a4d',
  lake: '#3ba9cf',
  boundary: '#8a97a5',
  subbasin: '#4f7fd4',
  control_point: '#b7791f',
  other: '#8a97a5'
}

// 图层叠放次序：子流域/边界在下，河流与站点在上
const LAYER_Z = {
  boundary: 2,
  subbasin: 3,
  lake: 4,
  river: 5,
  hydro_station: 6,
  rain_station: 6,
  control_point: 7,
  other: 5
}

const hasSubbasins = computed(() => {
  const l = state.layers.find((x) => x.type === 'subbasin')
  return !!(l && l.visible && state.map.showSubbasins && (l.feature_count || 0) > 0)
})
const hasControlPoints = computed(() => {
  const l = state.layers.find((x) => x.type === 'control_point')
  return !!(l && l.visible && (l.feature_count || 0) > 0)
})

// ---------------------------------------------------------------- 样式
function riverStyle(feature, selected) {
  const order = Number(feature.get('级别') || feature.get('order') || 1) || 1
  const w = Math.min(6.5, 1.4 + order * 1.05)
  return new Style({
    stroke: new Stroke({
      color: selected ? '#f39c12' : COLORS.river,
      width: selected ? w + 3 : w,
      lineCap: 'round',
      lineJoin: 'round'
    })
  })
}

function stationStyle(feature, type, selected, showLabel) {
  const color = COLORS[type] || COLORS.other
  const nm = feature.get('站名') || feature.get('名称') || feature.get('NAME') || feature.get('name') || ''
  const styles = [
    new Style({
      image:
        type === 'hydro_station'
          ? new CircleStyle({ radius: 6, fill: new Fill({ color }), stroke: new Stroke({ color: '#fff', width: 2 }) })
          : new CircleStyle({
              radius: 5.5,
              fill: new Fill({ color: selected ? '#f39c12' : '#ffffff' }),
              stroke: new Stroke({ color: selected ? '#f39c12' : color, width: 2.2 })
            })
    })
  ]
  if (showLabel && nm) {
    styles.push(
      new Style({
        text: new TextStyle({
          text: String(nm),
          offsetY: -14,
          font: '600 12px Microsoft YaHei, sans-serif',
          fill: new Fill({ color: selected ? '#f39c12' : color }),
          stroke: new Stroke({ color: 'rgba(255,255,255,.92)', width: 3 })
        })
      })
    )
  }
  return styles
}

function polygonStyle(feature, type, selected) {
  const isLake = type === 'lake'
  return new Style({
    fill: new Fill({ color: isLake ? 'rgba(59,169,207,.32)' : 'rgba(138,151,165,.1)' }),
    stroke: new Stroke({
      color: selected ? '#f39c12' : isLake ? '#2b8fb5' : COLORS.boundary,
      width: selected ? 3 : 1.6,
      lineDash: type === 'boundary' ? [7, 5] : undefined
    })
  })
}

function controlPointStyle(feature, selected) {
  const nm = feature.get('名称') || feature.get('name') || ''
  const styles = [
    new Style({
      image: new RegularShape({
        points: 4,
        radius: 7,
        radius2: 0,
        angle: Math.PI / 4,
        fill: new Fill({ color: selected ? '#f39c12' : '#ffffff' }),
        stroke: new Stroke({ color: selected ? '#f39c12' : COLORS.control_point, width: 2.2 })
      }),
      zIndex: 30
    })
  ]
  if (state.map.showLabels && nm) {
    styles.push(
      new Style({
        text: new TextStyle({
          text: String(nm),
          offsetY: -14,
          font: '600 12px Microsoft YaHei, sans-serif',
          fill: new Fill({ color: selected ? '#f39c12' : COLORS.control_point }),
          stroke: new Stroke({ color: 'rgba(255,255,255,.92)', width: 3 })
        })
      })
    )
  }
  return styles
}

/** 子流域（预报单元）：面按排水序分色，出口断面用三角表示。 */
function subbasinStyle(feature, selected) {
  const idx = Math.max(0, Number(feature.get('order_index') || 1) - 1)
  const color = subbasinColor(idx)
  const g = feature.getGeometry()
  const isPoint = g && (g.getType() === 'Point' || g.getType() === 'MultiPoint')

  if (isPoint) {
    return new Style({
      image: new RegularShape({
        points: 3,
        radius: selected ? 10 : 8,
        rotation: 0,
        fill: new Fill({ color: '#ffffff' }),
        stroke: new Stroke({ color: selected ? '#f39c12' : color, width: 2.4 })
      }),
      text: state.map.subbasinLabels
        ? new TextStyle({
            text: String(feature.get('code') || ''),
            offsetY: 16,
            font: '700 11px Microsoft YaHei, sans-serif',
            fill: new Fill({ color: selected ? '#f39c12' : color }),
            stroke: new Stroke({ color: 'rgba(255,255,255,.95)', width: 3 })
          })
        : undefined,
      zIndex: 25
    })
  }

  const styles = [
    new Style({
      fill: new Fill({ color: subbasinColor(idx, state.map.subbasinOpacity) }),
      stroke: new Stroke({
        color: selected ? '#f39c12' : subbasinColor(idx),
        width: selected ? 3.4 : 1.8,
        lineJoin: 'round'
      })
    })
  ]
  if (state.map.subbasinLabels && feature.get('code')) {
    const area = Number(feature.get('area_km2') || 0)
    styles.push(
      new Style({
        text: new TextStyle({
          text: `${feature.get('code')}  ${area ? area.toFixed(1) : ''}${area ? ' km²' : ''}`,
          font: '700 12px Microsoft YaHei, sans-serif',
          fill: new Fill({ color: selected ? '#f39c12' : '#12324f' }),
          stroke: new Stroke({ color: 'rgba(255,255,255,.96)', width: 3.6 }),
          overflow: false
        }),
        zIndex: 24
      })
    )
  }
  return styles
}

function styleFunction(layer) {
  return (feature) => {
    const selected = isSelected(layer, feature)
    const showLabel = state.map.showLabels
    if (layer.type === 'river') return riverStyle(feature, selected)
    if (layer.type === 'hydro_station' || layer.type === 'rain_station')
      return stationStyle(feature, layer.type, selected, showLabel)
    if (layer.type === 'subbasin') return subbasinStyle(feature, selected)
    if (layer.type === 'control_point') return controlPointStyle(feature, selected)
    if (layer.type === 'lake' || layer.type === 'boundary') return polygonStyle(feature, layer.type, selected)
    const g = feature.getGeometry()
    if (g && (g.getType() === 'Point' || g.getType() === 'MultiPoint'))
      return stationStyle(feature, 'rain_station', selected, showLabel)
    if (g && (g.getType() === 'Polygon' || g.getType() === 'MultiPolygon'))
      return polygonStyle(feature, 'other', selected)
    return new Style({ stroke: new Stroke({ color: selected ? '#f39c12' : COLORS.other, width: selected ? 4 : 2 }) })
  }
}

function isSelected(layer, feature) {
  const s = state.selection
  return !!s && s.source === 'map' && s.layerId === layer.id && String(s.featureId) === String(feature.getId())
}

// ---------------------------------------------------------------- 初始化
onMounted(() => {
  // 底图：高德（GCJ-02 火星坐标）。矢量图自带注记；影像另叠一层注记
  baseLayer = new TileLayer({ source: createAmapSource('vector'), visible: false, zIndex: 0 })
  satLayer = new TileLayer({ source: createAmapSource('sat'), visible: false, zIndex: 0 })
  satLabelLayer = new TileLayer({ source: createAmapSource('label'), visible: false, zIndex: 0.6 })

  reliefLayer = new ImageLayer({ opacity: state.map.reliefOpacity, zIndex: 1, visible: false })
  topoLayer = new VectorLayer({ source: new VectorSource(), zIndex: 8, visible: state.map.showTopology })
  drawSource = new VectorSource()
  drawLayer = new VectorLayer({ source: drawSource, zIndex: 12 })
  hiLayer = new VectorLayer({ source: new VectorSource(), zIndex: 14 })

  map = new OLMap({
    target: el.value,
    layers: [baseLayer, satLayer, satLabelLayer, reliefLayer, topoLayer, drawLayer, hiLayer],
    view: new View({
      projection: 'EPSG:4326',
      center: wgs84ToGcj02(110, 30.5),
      zoom: 8,
      maxZoom: 18
    }),
    controls: []
  })

  syncBase()

  snapInteraction = new Snap({ source: drawSource })
  map.addInteraction(snapInteraction)

  map.on('singleclick', onMapClick)
  syncDrawInteraction()

  setTimeout(() => map.updateSize(), 60)
  // 调试句柄：便于自动化测试与问题排查
  if (typeof window !== 'undefined') window.__hmap = { map, fit: fitToProject, state }

  // 页面加载时项目可能已处于选中状态（上面的 watch 不会触发），需补一次加载与定位
  if (state.project && state.project.id) {
    loadAllLayerData()
      .then(() => {
        rebuildVectorLayers()
        fitToProject()
        syncRelief() // 挂载时项目已选中，补一次晕渲同步
      })
      .catch((e) => console.error('[MapView] initial load failed', e))
  }
})

onBeforeUnmount(() => {
  if (map) map.setTarget(null)
})

// ---------------------------------------------------------------- 项目切换
watch(
  () => (state.project ? state.project.id : null),
  async (pid) => {
    try {
      rebuildVectorLayers()
      syncTopology()
      clearHighlight()
      if (!pid) return
      await loadAllLayerData()
      rebuildVectorLayers()
      fitToProject()
    } catch (e) {
      console.error('[MapView] project switch failed', e)
    }
  },
  { immediate: false }
)

watch(() => state.tab, (t) => {
  if (t === 'map' && map) setTimeout(() => map.updateSize(), 60)
})

watch(
  () => state.layers.map((l) => `${l.id}:${l.visible}:${l.name}`).join('|'),
  () => {
    loadAllLayerData().then(rebuildVectorLayers)
  }
)

watch(
  () => state.layerData,
  () => rebuildVectorLayers(),
  { deep: false }
)

watch(() => state.map.showTopology, () => {
  if (topoLayer) topoLayer.setVisible(state.map.showTopology)
})

watch(() => state.map.showLabels, () => {
  vecLayers.forEach((l) => l.changed())
})

watch(() => state.topology, syncTopology)
watch(() => state.selection, () => {
  vecLayers.forEach((l) => l.changed())
  updateHighlight()
})

watch(() => state.map.drawType, syncDrawInteraction)
watch(() => state.map.drawTarget, syncDrawInteraction)
watch(() => state.map.modify, syncModifyInteraction)
watch(() => state.map.basemap, syncBase)
watch(() => state.map.showRelief, syncRelief)
watch(() => state.map.reliefOpacity, syncRelief)
watch(() => state.dem, () => syncRelief()) // 项目载入 DEM 后自动显示晕渲

// 子流域显示选项：面填充/描边随透明度与标注开关重绘 —— 直接重建样式并触发重绘
watch(
  () => [state.map.showSubbasins, state.map.subbasinLabels, state.map.subbasinOpacity].join('|'),
  () => {
    const l = state.layers.find((x) => x.type === 'subbasin')
    if (l) {
      const vl = vecLayers.get(l.id)
      if (vl) {
        vl.setZIndex(LAYER_Z.subbasin)
        vl.setStyle(styleFunction(l))
        vl.setVisible(!!l.visible && state.map.showSubbasins)
        vl.changed()
      }
    }
  }
)

// 外部请求定位到某个范围（例如点击子流域列表）
watch(
  () => state.map.focus,
  (f) => {
    if (!f || !f.extent || !map) return
    const size = map.getSize()
    if (!size || !size[0] || !size[1]) {
      setTimeout(() => (state.map.focus = { ...f, ts: Date.now() }), 160)
      return
    }
    const ext = extentToGcj(f.extent)
    map.getView().fit(boundingExtent([[ext[0], ext[1]], [ext[2], ext[3]]]), {
      padding: [70, 70, 70, 70],
      maxZoom: 14,
      duration: 350
    })
  }
)

// 外部请求选中某个子流域单元：定位 + 高亮 + 填充属性面板
watch(
  () => state.map.pickSubbasin,
  (p) => {
    if (!p || !p.code || !map) return
    const layer = state.layers.find((x) => x.type === 'subbasin')
    if (!layer) return
    const vl = vecLayers.get(layer.id)
    if (!vl) return
    const feats = vl.getSource().getFeatures()
    const poly = feats.find((f) => f.get('kind') === 'subbasin' && f.get('code') === p.code)
    if (!poly) {
      toast(`地图上未找到单元 ${p.code} 的边界`, 'err')
      return
    }
    const ext = poly.getGeometry().getExtent()
    if (ext && Number.isFinite(ext[0]) && ext[2] > ext[0]) {
      map.getView().fit(boundingExtent([[ext[0], ext[1]], [ext[2], ext[3]]]), {
        padding: [70, 70, 70, 70],
        maxZoom: 14,
        duration: 350
      })
    }
    const props = {}
    for (const k of poly.getKeys()) {
      if (k === 'geometry' || k.startsWith('__')) continue
      props[k] = poly.get(k)
    }
    state.selection = {
      source: 'map',
      kind: 'feature',
      layerId: layer.id,
      layerType: 'subbasin',
      featureId: poly.getId(),
      title: String(poly.get('name') || ''),
      titleKey: 'name',
      properties: props,
      lon: null,
      lat: null
    }
  }
)

// ---------------------------------------------------------------- 图层构建
function fitToProject() {
  const raw = state.project && state.project.extent
  if (!raw || !map) return
  // 容器尚未完成布局时 fit 会算错，延迟重试
  const size = map.getSize()
  if (!size || !size[0] || !size[1]) {
    setTimeout(fitToProject, 150)
    return
  }
  // 数据为 WGS-84，底图为 GCJ-02，定位前先纠偏
  const ext = extentToGcj(raw)
  map.getView().fit(boundingExtent([[ext[0], ext[1]], [ext[2], ext[3]]]), {
    padding: [40, 40, 40, 40],
    maxZoom: 13,
    duration: 300
  })
}

function rebuildVectorLayers() {
  if (!map) return
  const keep = new Set()
  for (const layer of state.layers) {
    keep.add(layer.id)
    let vl = vecLayers.get(layer.id)
    if (!vl) {
      vl = new VectorLayer({ source: new VectorSource(), zIndex: LAYER_Z[layer.type] || 5 })
      map.addLayer(vl)
      vecLayers.set(layer.id, vl)
    }
    vl.setZIndex(LAYER_Z[layer.type] || 5)
    vl.setStyle(styleFunction(layer))
    const renderOff = layer.type === 'subbasin' && !state.map.showSubbasins
    vl.setVisible(!!layer.visible && !renderOff)
    const gj = state.layerData[layer.id]
    const src = vl.getSource()
    src.clear()
    if (gj && gj.features && gj.features.length) {
      const feats = geoJSON.readFeatures(gj, { dataProjection: 'EPSG:4326', featureProjection: 'EPSG:4326' })
      for (const f of feats) {
        f.set('__layerId', layer.id)
        // 源数据为 WGS-84，渲染前纠偏到 GCJ-02 以匹配高德底图
        geometryToGcj(f.getGeometry())
      }
      src.addFeatures(feats)
    }
  }
  for (const [lid, vl] of Array.from(vecLayers.entries())) {
    if (!keep.has(lid)) {
      map.removeLayer(vl)
      vecLayers.delete(lid)
    }
  }
}

function syncBase() {
  const m = state.map.basemap
  if (baseLayer) baseLayer.setVisible(m === 'amap')
  if (satLayer) satLayer.setVisible(m === 'amap_sat')
  if (satLabelLayer) satLabelLayer.setVisible(m === 'amap_sat')
}

function syncRelief() {
  if (!reliefLayer) return
  const d = state.dem
  const on = state.map.showRelief && !!d && !!d.relief && state.project
  reliefLayer.setVisible(on)
  reliefLayer.setOpacity(state.map.reliefOpacity)
  if (on) {
    const url = api.reliefUrl(state.project.id) + `?t=${encodeURIComponent(d.file || '')}`
    reliefLayer.setSource(
      new ImageStatic({
        url,
        // 晕渲栅格按 WGS-84 四至贴图，整体平移到 GCJ-02 位置（区域内偏差仅数十米，作背景可忽略）
        imageExtent: extentToGcj(d.bounds),
        projection: 'EPSG:4326',
        crossOrigin: 'anonymous'
      })
    )
  }
}

// ---------------------------------------------------------------- 拓扑图层
function syncTopology() {
  if (!topoLayer) return
  const src = topoLayer.getSource()
  src.clear()
  const t = state.topology
  topoLayer.setVisible(state.map.showTopology && !!t)
  if (!t) return

  const nodeById = new Map(t.nodes.map((n) => [n.id, n]))
  const feats = []
  // topology 坐标为 WGS-84，渲染前纠偏到 GCJ-02（每条河段只转一次，缓存复用）
  const gcjCoords = new Map()
  for (const e of t.edges) gcjCoords.set(e.id, coordsToGcj(e.coords || []))

  // 流向箭头
  for (const e of t.edges) {
    const p = pointAlong(gcjCoords.get(e.id), 0.62)
    if (!p) continue
    const f = new Feature({ geometry: new Point(p.pt) })
    f.set('rotation', p.rotation)
    feats.push(f)
  }
  // 节点符号
  for (const n of t.nodes) {
    const f = new Feature({ geometry: new Point(wgs84ToGcj02(n.x, n.y)) })
    f.set('nodeKind', n.kind)
    f.set('nodeId', n.id)
    feats.push(f)
  }
  // 站点挂接连线
  for (const s of t.stations) {
    const e = t.edges.find((x) => x.id === s.edge_id)
    if (!e) continue
    const p = pointAlong(gcjCoords.get(e.id), s.ratio || 0.5)
    if (!p) continue
    const f = new Feature({ geometry: new LineString([wgs84ToGcj02(s.x, s.y), p.pt]) })
    f.set('connector', true)
    f.set('attached', s.attached)
    feats.push(f)
  }

  src.addFeatures(feats)
  topoLayer.setStyle((feature) => {
    const g = feature.getGeometry().getType()
    if (g === 'Point' && feature.get('rotation') !== undefined) {
      return new Style({
        image: new RegularShape({
          points: 3,
          radius: 7,
          rotation: feature.get('rotation'),
          fill: new Fill({ color: 'rgba(30,111,168,.85)' }),
          stroke: new Stroke({ color: 'rgba(255,255,255,.9)', width: 1 })
        })
      })
    }
    if (g === 'Point') {
      const k = feature.get('nodeKind')
      if (k === 'outlet') {
        return new Style({
          image: new RegularShape({
            points: 3,
            radius: 9,
            rotation: 0,
            fill: new Fill({ color: '#ffffff' }),
            stroke: new Stroke({ color: '#c0392b', width: 2.4 })
          })
        })
      }
      if (k === 'junction')
        return new Style({
          image: new CircleStyle({ radius: 3.4, fill: new Fill({ color: '#1e6fa8' }) })
        })
      if (k === 'source')
        return new Style({
          image: new CircleStyle({
            radius: 3.2,
            fill: new Fill({ color: '#ffffff' }),
            stroke: new Stroke({ color: '#1e6fa8', width: 1.5 })
          })
        })
      return null
    }
    return new Style({
      stroke: new Stroke({
        color: feature.get('attached') === false ? 'rgba(183,121,31,.75)' : 'rgba(30,111,168,.55)',
        width: 1.2,
        lineDash: [4, 4]
      })
    })
  })
}

function pointAlong(coords, ratio) {
  if (!coords || coords.length < 2) return null
  const segs = []
  let total = 0
  for (let i = 0; i < coords.length - 1; i++) {
    const d = Math.hypot(coords[i + 1][0] - coords[i][0], coords[i + 1][1] - coords[i][1])
    segs.push(d)
    total += d
  }
  if (total <= 0) return null
  let target = ratio * total
  for (let i = 0; i < segs.length; i++) {
    if (target <= segs[i] || i === segs.length - 1) {
      const t = segs[i] > 0 ? Math.max(0, Math.min(1, target / segs[i])) : 0
      const a = coords[i]
      const b = coords[i + 1]
      const pt = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]
      return { pt, rotation: Math.atan2(b[0] - a[0], b[1] - a[1]) }
    }
    target -= segs[i]
  }
  return null
}

// ---------------------------------------------------------------- 高亮
function clearHighlight() {
  if (hiLayer) hiLayer.getSource().clear()
}

function updateHighlight() {
  clearHighlight()
  const s = state.selection
  if (!s || s.source !== 'map' || !state.topology) return
  if (s.layerType !== 'hydro_station' && s.layerType !== 'rain_station') return
  const t = state.topology
  const st = t.stations.find(
    (x) => Math.abs(x.x - s.lon) < 1e-6 && Math.abs(x.y - s.lat) < 1e-6
  ) || t.stations.find((x) => x.name && s.title && x.name === s.title)
  if (!st) return
  const host = t.edges.find((e) => e.id === st.edge_id)
  if (!host) return
  const node = t.nodes.find((n) => n.id === host.from_node)
  const ids = new Set([host.id, ...((node && node.upstream_edges) || [])])
  const feats = []
  for (const id of ids) {
    const e = t.edges.find((x) => x.id === id)
    if (e)
      feats.push(
        geoJSON.readFeature({
          type: 'Feature',
          geometry: { type: 'LineString', coordinates: coordsToGcj(e.coords) }
        })
      )
  }
  hiLayer.setSource(new VectorSource({ features: feats }))
  hiLayer.setStyle(
    new Style({
      stroke: new Stroke({ color: 'rgba(243,156,18,.9)', width: 6, lineCap: 'round' })
    })
  )
  toast(`已高亮 ${st.name} 以上 ${ids.size} 个河段`, 'ok', 2200)
}

// ---------------------------------------------------------------- 交互
function onMapClick(evt) {
  if (state.map.drawType || state.map.modify) return
  let picked = null
  map.forEachFeatureAtPixel(evt.pixel, (feature, layer) => {
    const lid = feature.get('__layerId')
    if (!lid || !layer || layer === hiLayer || layer === topoLayer) return false
    picked = { feature, lid }
    return true
  })
  if (!picked) {
    state.selection = null
    return
  }
  const f = picked.feature
  const layer = state.layers.find((l) => l.id === picked.lid)
  const props = {}
  for (const k of f.getKeys()) {
    if (k === 'geometry' || k.startsWith('__')) continue
    props[k] = f.get(k)
  }
  const titleKey = ['站名', '名称', 'NAME', 'name', '河名', '河流名称'].find((k) => props[k])
  state.selection = {
    source: 'map',
    kind: 'feature',
    layerId: picked.lid,
    layerType: layer ? layer.type : 'other',
    featureId: f.getId(),
    title: titleKey ? String(props[titleKey]) : '',
    titleKey: titleKey || '名称',
    properties: props,
    lon: null,
    lat: null
  }
  const g = f.getGeometry()
  if (g && g.getType() === 'Point') {
    // 视图中为 GCJ-02 坐标，selection 里统一保存 WGS-84（与拓扑、DEM 数据一致）
    const c = g.getCoordinates()
    const [wl, wa] = gcj02ToWgs84(c[0], c[1])
    state.selection.lon = wl
    state.selection.lat = wa
  }
  updateHighlight()
}

function clearDraw() {
  if (drawInteraction) {
    map.removeInteraction(drawInteraction)
    drawInteraction = null
  }
  drawSource.clear()
}

function syncDrawInteraction() {
  if (!map) return
  clearDraw()
  const t = state.map.drawType
  if (!t) return
  const target = state.layers.find((l) => l.id === state.map.drawTarget)
  const isCtrl = t === 'Point' && target && target.type === 'control_point'
  drawLabel.value =
    (isCtrl ? '点（控制断面）' : { Point: '点（站点）', LineString: '线（河流）', Polygon: '面（湖泊）' }[t]) || t
  drawInteraction = new Draw({ source: drawSource, type: t, stopClick: true })
  map.addInteraction(drawInteraction)
  drawInteraction.on('drawend', (e) => {
    // 立即从临时源移除，交给业务图层渲染
    setTimeout(() => drawSource.clear(), 0)
    handleDrawn(e.feature)
  })
}

async function handleDrawn(feature) {
  const t = state.map.drawType
  // 绘制坐标产生于 GCJ-02 视图，落库前转回 WGS-84
  const geomWgs = geometryToWgs(feature.getGeometry().clone())
  const geom = geoJSON.writeGeometryObject(geomWgs, {
    dataProjection: 'EPSG:4326',
    featureProjection: 'EPSG:4326',
    decimals: 7
  })
  const defType = { Point: 'hydro_station', LineString: 'river', Polygon: 'lake' }[t] || 'other'
  const defName = { Point: '手工绘制站点', LineString: '手工绘制河流', Polygon: '手工绘制湖泊' }[t] || '手工绘制图层'

  try {
    let lid = state.map.drawTarget
    if (!lid) {
      const info = await withBusy('正在创建图层…', () =>
        api.createLayer(state.project.id, { name: defName, type: defType, features: [], source: 'draw' })
      )
      state.layers.push(info)
      lid = info.id
      state.map.drawTarget = lid
    } else {
      const layer = state.layers.find((l) => l.id === lid)
      if (layer && layer.source !== 'draw' && layer.source !== 'samples') {
        // 允许继续追加，但提示一下类型
      }
    }
    const nameField = {
      hydro_station: '站名',
      rain_station: '站名',
      river: '河名',
      lake: '名称',
      control_point: '名称'
    }[(state.layers.find((l) => l.id === lid) || {}).type]
    const props = {}
    if (nameField) {
      const n = prompt(`请输入${nameField}（可留空）`, '')
      if (n) props[nameField] = n
    }
    await saveDrawnFeatures(lid, [
      { type: 'Feature', geometry: geom, properties: props }
    ])
    toast('已保存绘制的要素', 'ok', 1800)
  } catch (err) {
    toast('保存失败：' + err.message, 'err')
  }
}

function syncModifyInteraction() {
  if (!map) return
  if (modifyInteraction) {
    map.removeInteraction(modifyInteraction)
    modifyInteraction = null
  }
  if (!state.map.modify) {
    if (snapInteraction) {
      map.removeInteraction(snapInteraction)
      snapInteraction = new Snap({ source: drawSource })
    }
    return
  }
  const sources = Array.from(vecLayers.values()).filter((l) => l.getVisible()).map((l) => l.getSource())
  if (!sources.length) return
  modifyInteraction = new Modify({ source: undefined, features: undefined })
  // 用集合式 Modify：把可见图层的要素合并到一个临时源中更好维护，这里直接监听各图层
  map.removeInteraction(modifyInteraction)
  const allFeatures = []
  for (const s of sources) allFeatures.push(...s.getFeatures())
  const tmpSource = new VectorSource({ features: allFeatures })
  modifyInteraction = new Modify({ source: tmpSource })
  map.addInteraction(modifyInteraction)
  snapInteraction = new Snap({ source: tmpSource, pixelTolerance: 10 })
  map.addInteraction(snapInteraction)

  modifyInteraction.on('modifyend', async (e) => {
    for (const f of e.features.getArray()) {
      const lid = f.get('__layerId')
      if (!lid) continue
      const fid = f.getId()
      // 视图中为 GCJ-02，落库前转回 WGS-84
      const geomWgs = geometryToWgs(f.getGeometry().clone())
      const geometry = geoJSON.writeGeometryObject(geomWgs, {
        dataProjection: 'EPSG:4326',
        featureProjection: 'EPSG:4326',
        decimals: 7
      })
      try {
        await api.updateFeature(state.project.id, lid, fid, { geometry })
        await loadLayerData(lid, true)
      } catch (err) {
        toast('保存要素失败：' + err.message, 'err')
      }
    }
    toast('要素已更新', 'ok', 1600)
  })
}
</script>

<script>
// OL 的几个构造器只在需要时引用，避免打包器 tree-shaking 问题
import Feature from 'ol/Feature'
import Point from 'ol/geom/Point'
import LineString from 'ol/geom/LineString'
export const olRefs = { Feature, Point, LineString, RegularShape }
</script>

<style scoped>
.map-wrap { position: absolute; inset: 0; }
.map { position: absolute; inset: 0; }

.map-ctrl .ctl-row { display: flex; align-items: center; gap: 7px; }
.map-ctrl .ctl-lab { color: var(--text-2); font-size: 12px; }
.map-ctrl .ctl-note { color: var(--text-3); font-size: 11px; line-height: 1.35; max-width: 210px; }
.map-ctrl select {
  padding: 3px 6px;
  border: 1px solid var(--line-strong);
  border-radius: 5px;
  background: #fff;
}
.map-ctrl input[type='range'] { width: 84px; }

.legend-title { font-weight: 600; color: var(--text-2); margin-bottom: 3px; }
.legend-row { display: flex; align-items: center; gap: 6px; color: var(--text-2); }
.sw { width: 16px; height: 10px; display: inline-block; flex: 0 0 auto; }
.sw.river { height: 3px; background: #2f8ac4; border-radius: 2px; }
.sw.hydro { height: 10px; border-radius: 50%; background: #c0392b; width: 10px; }
.sw.rain { height: 10px; width: 10px; border-radius: 50%; background: #fff; border: 2.2px solid #1f7a4d; }
.sw.lake { background: rgba(59,169,207,.45); border: 1px solid #2b8fb5; }
.sw.node { height: 10px; width: 10px; background: transparent; position: relative; }
.sw.node::after { content: ''; display: block; width: 6px; height: 6px; margin: 2px; border-radius: 50%; background: #1e6fa8; }
.sw.outlet { height: 10px; width: 10px; background: transparent; position: relative; }
.sw.outlet::after {
  content: '';
  display: block;
  width: 0; height: 0;
  margin: 1px auto;
  border-left: 5px solid transparent;
  border-right: 5px solid transparent;
  border-bottom: 8px solid #c0392b;
}
.sw.arrow { height: 10px; width: 10px; background: transparent; position: relative; }
.sw.arrow::after {
  content: '';
  display: block;
  width: 0; height: 0;
  margin: 2px auto;
  border-left: 4px solid transparent;
  border-right: 4px solid transparent;
  border-bottom: 7px solid #1e6fa8;
}
.sw.subbasin {
  background: rgba(79,127,212,.34);
  border: 1.6px solid #4f7fd4;
  border-radius: 2px;
}
.sw.suboutlet { height: 10px; width: 10px; background: transparent; position: relative; }
.sw.suboutlet::after {
  content: '';
  display: block;
  width: 0; height: 0;
  margin: 1px auto;
  border-left: 5px solid transparent;
  border-right: 5px solid transparent;
  border-bottom: 8px solid #4f7fd4;
}
.sw.ctrl { height: 10px; width: 10px; background: transparent; position: relative; }
.sw.ctrl::after {
  content: '';
  display: block;
  width: 6px; height: 6px;
  margin: 2px auto;
  background: #fff;
  border: 2px solid #b7791f;
  transform: rotate(45deg);
}

.map-tip {
  position: absolute;
  top: 58px; /* 让开顶部居中的视图切换按钮 */
  left: 50%;
  transform: translateX(-50%);
  background: rgba(30,111,168,.94);
  color: #fff;
  padding: 5px 14px;
  border-radius: 16px;
  font-size: 12px;
  z-index: 7;
}
.map-tip.edit { background: rgba(183,121,31,.94); }
</style>
