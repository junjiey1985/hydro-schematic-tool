<template>
  <FloatingPanel
    v-if="visible"
    title="选中对象"
    :width="254"
    :initial-x="250"
    :initial-y="58"
    :active="true"
    :tone="isMap ? 'blue' : 'amber'"
    :badge="kindLabel"
    closable
    @close="clear"
  >
    <template #icon>
      <svg viewBox="0 0 24 24" width="13" height="13">
        <circle cx="12" cy="12" r="7" fill="none" stroke="currentColor" stroke-width="2" />
        <circle cx="12" cy="12" r="1.6" fill="currentColor" />
        <path
          d="M12 2v3M12 19v3M2 12h3M19 12h3"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
        />
      </svg>
    </template>

    <div class="sel-meta">
      <span class="tag">{{ kindLabel }}</span>
      <span v-if="subLabel" class="tag" :title="subLabel">{{ subLabel }}</span>
    </div>

    <!-- ================= 地图要素 ================= -->
    <template v-if="isMap">
      <div class="field" v-if="sel.title !== undefined">
        <label>名称</label>
        <input type="text" v-model="sel.title" @change="renameFeature" />
      </div>
      <div class="sel-props" v-if="propRows.length">
        <table class="kv">
          <tr v-for="r in propRows" :key="r.k">
            <td>{{ r.k }}</td>
            <td>{{ r.v }}</td>
          </tr>
        </table>
      </div>
      <div v-else class="muted" style="font-size: 12px">该要素无其他属性</div>
      <div class="btn-row">
        <button class="btn sm danger" @click="deleteSelectedFeature">删除要素</button>
        <button class="btn sm" @click="clear">取消选中</button>
      </div>
    </template>

    <!-- ================= 概化图元素 ================= -->
    <template v-else>
      <div class="field">
        <label>名称</label>
        <input type="text" v-model="sel.title" @change="applySchematicRename" />
      </div>
      <div class="field" v-if="sel.kind === 'station'">
        <label>类型</label>
        <select v-model="sel.stype" @change="applySchematicRename">
          <option value="hydro">水文站</option>
          <option value="rain">雨量站</option>
          <option value="manual">自定义</option>
        </select>
      </div>
      <div class="sel-props" v-if="propRows.length">
        <table class="kv">
          <tr v-for="r in propRows" :key="r.k">
            <td>{{ r.k }}</td>
            <td>{{ r.v }}</td>
          </tr>
        </table>
      </div>
      <div v-else class="muted" style="font-size: 12px">该元素无其他属性</div>
      <div class="btn-row">
        <button class="btn sm danger" @click="deleteSelectedElement">从概化图删除</button>
        <button class="btn sm" @click="clear">取消选中</button>
      </div>
    </template>
  </FloatingPanel>
</template>

<script setup>
import { computed } from 'vue'
import FloatingPanel from './FloatingPanel.vue'
import { api } from '../api'
import { state, layerTypeLabel, markDirty, removeSchematicElement, toast } from '../store'

// context: 'map' | 'schematic' —— 只在对应视图里显示
const props = defineProps({
  context: { type: String, default: 'map' }
})

const sel = computed(() => state.selection || {})
const isMap = computed(() => sel.value.source === 'map')

const visible = computed(() => {
  const s = state.selection
  if (!s) return false
  return props.context === 'schematic' ? s.source === 'schematic' : s.source === 'map'
})

const kindLabel = computed(() => {
  const s = state.selection
  if (!s) return ''
  if (s.source === 'map') return s.layerType ? layerTypeLabel(s.layerType) : '地图要素'
  return { node: '概化节点', edge: '概化河段', station: '站点', lake: '湖泊/水库' }[s.kind] || '概化元素'
})

const subLabel = computed(() => {
  const s = state.selection
  if (!s) return ''
  if (s.source === 'map') {
    const ln = (s.properties || {})._layer_name
    return ln ? String(ln) : s.featureId != null ? `要素 #${s.featureId}` : ''
  }
  return s.id || ''
})

const propRows = computed(() => {
  const s = state.selection
  if (!s) return []
  if (s.source === 'map') {
    const p = { ...(s.properties || {}) }
    delete p._layer_id
    delete p._layer_name
    delete p._layer_type
    return Object.entries(p).map(([k, v]) => ({ k, v: fmt(v) }))
  }
  return Object.entries(s.props || {}).map(([k, v]) => ({ k, v: fmt(v) }))
})

function fmt(v) {
  if (v === null || v === undefined || v === '') return '—'
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(3)
  return String(v)
}

function clear() {
  state.selection = null
}

// ---------------------------------------------------------------- 地图要素
function renameFeature() {
  const s = state.selection
  if (!s || s.source !== 'map' || !s.title) return
  const key = s.titleKey || '名称'
  api
    .updateFeature(state.project.id, s.layerId, s.featureId, { properties: { [key]: s.title } })
    .then(() => {
      const lid = s.layerId
      return api.getLayer(state.project.id, lid).then((r) => {
        state.layerData[lid] = r.geojson
      })
    })
    .then(() => toast('属性已更新', 'ok'))
    .catch((e) => toast(e.message, 'err'))
}

async function deleteSelectedFeature() {
  const s = state.selection
  if (!s) return
  if (!confirm('确定删除该要素吗？')) return
  await api.deleteFeature(state.project.id, s.layerId, s.featureId)
  const r = await api.getLayer(state.project.id, s.layerId)
  state.layerData[s.layerId] = r.geojson
  state.selection = null
  toast('要素已删除', 'ok')
}

// ---------------------------------------------------------------- 概化图元素
function applySchematicRename() {
  const s = state.selection
  if (!s || s.source !== 'schematic') return
  const w = state.schematicWorking
  if (!w) return
  if (s.kind === 'station') {
    const item = w.stations.find((x) => x.id === s.id)
    if (item) {
      item.name = s.title
      if (s.stype) item.type = s.stype
    }
  } else if (s.kind === 'lake') {
    const item = w.lakes.find((x) => x.id === s.id)
    if (item) item.name = s.title
  }
  markDirty()
}

async function deleteSelectedElement() {
  const s = state.selection
  if (!s) return
  if (!confirm('确定从概化图中删除该元素吗？')) return
  const group = { node: 'nodes', edge: 'edges', station: 'stations', lake: 'lakes' }[s.kind]
  await removeSchematicElement(group, s.id)
  state.selection = null
}
</script>

<style scoped>
.sel-meta {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
.sel-props {
  max-height: 268px;
  overflow: auto;
  margin: 0 -2px;
  padding: 0 2px;
}
.sel-props table.kv td {
  font-size: 12px;
}
</style>
