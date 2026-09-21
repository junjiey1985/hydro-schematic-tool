<template>
  <FloatingPanel
    v-if="state.project"
    title="手工绘制"
    :width="216"
    :active="!!state.map.drawType || state.map.modify"
    :tone="state.map.modify ? 'amber' : 'blue'"
    :badge="statusText"
  >
    <template #icon>
      <svg viewBox="0 0 24 24" width="13" height="13">
        <path
          d="M4 20h4L20 8a2.8 2.8 0 0 0-4-4L4 16v4z"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linejoin="round"
        />
      </svg>
    </template>

    <!-- 绘制状态 / 结束 -->
    <div v-if="state.map.drawType" class="status">
      <span class="pulse"></span>
      <span class="stxt">
        绘制中：<b>{{ drawLabel }}</b>
        <span class="dim">· 双击/右键结束</span>
      </span>
      <button class="btn sm" @click="endDraw">结束</button>
    </div>

    <!-- 三种绘制工具 -->
    <div class="tools">
      <button
        v-for="t in tools"
        :key="t.type"
        class="tool"
        :class="{ on: state.map.drawType === t.type }"
        :disabled="state.map.modify"
        @click="setDraw(t.type)"
        :title="t.tip"
      >
        <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
          <template v-if="t.type === 'Point'">
            <circle cx="12" cy="12" r="5" fill="currentColor" />
            <circle cx="12" cy="12" r="8.5" fill="none" stroke="currentColor" stroke-width="1.4" opacity=".45" />
          </template>
          <template v-else-if="t.type === 'LineString'">
            <path
              d="M3 18c3.5 0 4.5-5 8-6s5 3 9-3"
              fill="none"
              stroke="currentColor"
              stroke-width="2.2"
              stroke-linecap="round"
            />
            <circle cx="3" cy="18" r="2.1" fill="currentColor" />
            <circle cx="20" cy="9" r="2.1" fill="currentColor" />
          </template>
          <template v-else>
            <path
              d="M12 3.6 20.4 9.3 17.2 19H6.8L3.6 9.3z"
              fill="currentColor"
              fill-opacity=".22"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linejoin="round"
            />
          </template>
        </svg>
        <span>{{ t.short }}</span>
      </button>
    </div>

    <!-- 编辑模式 -->
    <button class="modify" :class="{ on: state.map.modify }" @click="toggleModify">
      <span class="box">{{ state.map.modify ? '✓' : '' }}</span>
      <span>编辑模式（拖动顶点改要素）</span>
    </button>

    <!-- 目标图层 -->
    <div class="field">
      <label>保存到图层</label>
      <select v-model="state.map.drawTarget" :disabled="state.map.modify">
        <option :value="null">{{ autoLayerLabel }}</option>
        <option v-for="l in drawableLayers" :key="l.id" :value="l.id">
          {{ l.name }}（{{ layerTypeLabel(l.type) }}）
        </option>
      </select>
    </div>

    <div class="hint" v-if="!state.map.drawType && !state.map.modify">
      选一种图形后在地图上点击即可开始绘制，绘制完成自动保存。
    </div>
    <div class="hint" v-else-if="state.map.drawType">
      按住 Shift 可吸附到已有要素节点；按 Esc 结束绘制。
    </div>
    <div class="hint" v-else>拖动顶点或节点修改已有要素，松开鼠标即保存。</div>
  </FloatingPanel>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted } from 'vue'
import { layerTypeLabel, state } from '../store'
import FloatingPanel from './FloatingPanel.vue'

const tools = [
  { type: 'Point', short: '绘点', tip: '绘制点要素（水文站 / 雨量站等）' },
  { type: 'LineString', short: '绘线', tip: '绘制线要素（河流等）' },
  { type: 'Polygon', short: '绘面', tip: '绘制面要素（湖泊 / 水库等）' }
]

const drawLabel = computed(() => {
  const t = state.map.drawType
  const target = state.layers.find((l) => l.id === state.map.drawTarget)
  if (t === 'Point' && target && target.type === 'control_point') return '点（控制断面）'
  return { Point: '点（站点）', LineString: '线（河流）', Polygon: '面（湖泊）' }[t] || ''
})
const statusText = computed(() => {
  if (state.map.drawType) return drawLabel.value
  if (state.map.modify) return '编辑模式'
  return ''
})

const drawableLayers = computed(() => state.layers.filter((l) => l.type !== 'boundary'))

const autoLayerLabel = computed(() => {
  const n = { Point: '手工绘制站点', LineString: '手工绘制河流', Polygon: '手工绘制湖泊' }[state.map.drawType] || '手工绘制图层'
  return `＋ 新建图层（${n}）`
})

function setDraw(t) {
  state.map.modify = false
  state.map.drawType = state.map.drawType === t ? null : t
}

function endDraw() {
  state.map.drawType = null
}

function toggleModify() {
  state.map.modify = !state.map.modify
  if (state.map.modify) state.map.drawType = null
}

function onKey(e) {
  if (e.key === 'Escape' && state.map.drawType) {
    state.map.drawType = null
  }
}

onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<style scoped>
.status {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 6px 5px 8px;
  border-radius: 7px;
  background: var(--primary-soft);
  border: 1px solid #cfe4f2;
}
.status .stxt { flex: 1; font-size: 11.5px; color: var(--text-2); min-width: 0; }
.status .stxt b { color: var(--primary); }
.status .dim { color: var(--text-3); }
.status .btn { padding: 2px 7px; font-size: 11px; }
.pulse {
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--accent);
  animation: pl 1.3s ease-in-out infinite;
  flex: 0 0 auto;
}
@keyframes pl { 0%, 100% { opacity: 1; } 50% { opacity: 0.25; } }

.tools { display: flex; gap: 6px; }
.tool {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: 6px 2px 4px;
  border: 1px solid var(--line-strong);
  border-radius: 7px;
  background: #fff;
  color: var(--text-2);
  font-size: 11px;
  transition: all 0.12s;
}
.tool:hover:not(:disabled) { border-color: var(--primary); color: var(--primary); }
.tool.on {
  background: var(--primary);
  border-color: var(--primary);
  color: #fff;
  box-shadow: 0 2px 6px rgba(30, 111, 168, 0.28);
}
.tool:disabled { opacity: 0.4; cursor: not-allowed; }

.modify {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 6px 8px;
  border: 1px solid var(--line-strong);
  border-radius: 7px;
  background: #fff;
  color: var(--text-2);
  font-size: 11.5px;
  text-align: left;
}
.modify:hover { border-color: var(--amber); color: var(--amber); }
.modify.on { background: #fdf6e7; border-color: #e0c281; color: #8a5d12; font-weight: 600; }
.modify .box {
  width: 13px; height: 13px;
  border: 1px solid var(--line-strong);
  border-radius: 3px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  background: #fff;
  flex: 0 0 auto;
}
.modify.on .box { background: var(--amber); border-color: var(--amber); color: #fff; }

.field label {
  display: block;
  color: var(--text-2);
  font-size: 11px;
  margin-bottom: 3px;
}
.field select {
  width: 100%;
  padding: 4px 6px;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  background: #fff;
  color: var(--text);
  outline: none;
}
.field select:focus { border-color: var(--primary); }

.hint {
  font-size: 10.5px;
  line-height: 1.55;
  color: var(--text-3);
  padding-top: 1px;
  border-top: 1px dashed var(--line);
}
</style>
