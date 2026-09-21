<template>
  <FloatingPanel
    title="概化图编辑"
    :width="228"
    :initial-x="14"
    :initial-y="14"
    :active="state.sch.tool !== 'select'"
    :tone="state.sch.tool !== 'select' ? 'amber' : 'blue'"
    :badge="badge"
  >
    <template #icon>
      <svg viewBox="0 0 24 24" width="13" height="13">
        <path
          d="M12 21V9M12 9 6 4M12 9l6-5"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
        />
        <circle cx="12" cy="21" r="1.8" fill="currentColor" />
        <circle cx="6" cy="4" r="1.8" fill="currentColor" />
        <circle cx="18" cy="4" r="1.8" fill="currentColor" />
      </svg>
    </template>

    <!-- 编辑工具 -->
    <div class="tools">
      <button
        v-for="t in tools"
        :key="t.tool"
        class="tool"
        :class="{ on: state.sch.tool === t.tool }"
        @click="setTool(t.tool)"
        :title="t.tip"
      >
        <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
          <!-- 选择/拖动：箭头光标 -->
          <template v-if="t.tool === 'select'">
            <path d="M6 3.5 18.2 11l-5.3 1.1 3 5.9-2.5 1.2-3-5.9-3.9 3.7z" fill="currentColor" />
          </template>
          <!-- 加点：圆 + 加号 -->
          <template v-else-if="t.tool === 'addNode'">
            <circle cx="9.5" cy="12" r="4.6" fill="currentColor" fill-opacity=".22" stroke="currentColor" stroke-width="1.7" />
            <path d="M18 10v6M15 13h6" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
          </template>
          <!-- 加线段：两点连线 -->
          <template v-else-if="t.tool === 'addEdge'">
            <path d="M5 18 19 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
            <circle cx="5" cy="18" r="2.6" fill="currentColor" />
            <circle cx="19" cy="6" r="2.6" fill="currentColor" />
          </template>
          <!-- 加面：多边形 -->
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

    <!-- 显示标注 -->
    <button class="toggle" :class="{ on: state.sch.showLabels }" @click="state.sch.showLabels = !state.sch.showLabels">
      <span class="box">{{ state.sch.showLabels ? '✓' : '' }}</span>
      <span>显示名称标注</span>
    </button>

    <!-- 河段按预报单元着色 -->
    <button
      class="toggle"
      :class="{ on: state.sch.colorBySubbasin && hasSb }"
      :disabled="!hasSb"
      :title="hasSb ? '同一预报单元的河段用同一种颜色' : '尚未划分子流域'"
      @click="toggleColor"
    >
      <span class="box">{{ state.sch.colorBySubbasin && hasSb ? '✓' : '' }}</span>
      <span>河段按预报单元着色</span>
    </button>

    <div v-if="state.sch.colorBySubbasin && hasSb" class="sb-legend">
      <div
        v-for="sb in sbList"
        :key="sb.code"
        class="sb-chip"
        :class="{ on: state.sch.focusSubbasin === sb.code }"
        :title="`点击聚焦 ${sb.code}（其余河段淡化并定位到该单元），再点一次退出聚焦`"
        @click="focusSb(sb.code)"
      >
        <i :style="{ background: sb.color }"></i>
        <b>{{ sb.code }}</b>
        <span class="nm">{{ sb.name }}</span>
        <em>{{ sb.area_km2 }} km²</em>
      </div>
      <div
        v-if="sbMissing"
        class="sb-chip muted"
        title="不属于任何预报单元的河段（未划分或落在单元之外）"
      >
        <i style="background: #9db7c9"></i>
        <b>—</b>
        <span class="nm">未归属单元</span>
        <em>{{ sbMissing }} 段</em>
      </div>
    </div>

    <!-- 布局参数 -->
    <div class="field">
      <label>布局方向</label>
      <select v-model="state.sch.orientation">
        <option value="vertical">纵向（上游在上）</option>
        <option value="horizontal">横向（上游在左）</option>
      </select>
    </div>
    <div class="field">
      <label>层间距 <b>{{ Math.round(state.sch.level_gap) }}</b></label>
      <input type="range" min="40" max="220" step="2" v-model.number="state.sch.level_gap" />
    </div>
    <div class="field">
      <label>叶间距 <b>{{ Math.round(state.sch.leaf_gap) }}</b></label>
      <input type="range" min="20" max="140" step="2" v-model.number="state.sch.leaf_gap" />
    </div>

    <div class="btn-row">
      <button class="btn sm" @click="relayout">按新参数重排</button>
      <button class="btn sm primary" :disabled="!state.schDirty" @click="saveSchematic">保存</button>
    </div>

    <div class="hint">{{ hint }}</div>
  </FloatingPanel>
</template>

<script setup>
import { computed } from 'vue'
import { buildSchematic, clearSubbasinFocus, focusSubbasinInSchematic, saveSchematic, state, subbasinEdgeStyle } from '../store'
import FloatingPanel from './FloatingPanel.vue'

const tools = [
  { tool: 'select', short: '选择/拖动', tip: '选择并拖动节点 / 河段 / 站点 / 湖泊' },
  { tool: 'addNode', short: '加点', tip: '在空白处单击添加新节点' },
  { tool: 'addEdge', short: '加线段', tip: '依次单击两个节点连成河段' },
  { tool: 'addLake', short: '加面', tip: '在空白处单击放置湖泊 / 水库' }
]

const toolLabel = computed(() => (tools.find((t) => t.tool === state.sch.tool) || {}).short || '')
const badge = computed(() => (state.sch.tool !== 'select' ? toolLabel.value : state.schDirty ? '未保存' : ''))

// ---------------------------------------------------------------- 预报单元着色
const sbStyle = computed(() => subbasinEdgeStyle())
const sbList = computed(() => (sbStyle.value ? sbStyle.value.list : []))
const hasSb = computed(() => sbList.value.length > 0)
const sbMissing = computed(() => {
  const s = sbStyle.value
  const cur = state.schematicWorking
  if (!s || !cur) return 0
  return (cur.edges || []).filter((e) => !s.edge[e.id]).length
})

function toggleColor() {
  if (!hasSb.value) return
  state.sch.colorBySubbasin = !state.sch.colorBySubbasin
  if (!state.sch.colorBySubbasin) clearSubbasinFocus()
}

/** 聚焦某个预报单元（其余河段淡化 + 定位到该单元）；再次点击退出并回到适配视图。 */
function focusSb(code) {
  focusSubbasinInSchematic(code)
}

const hint = computed(() => {
  if (state.sch.tool === 'addNode') return '加点模式：在空白处单击添加新节点，Esc 退出。'
  if (state.sch.tool === 'addEdge') return '加线段模式：依次单击两个节点完成连线，Esc 取消。'
  if (state.sch.tool === 'addLake') return '加面模式：单击空白处放置湖泊，Esc 退出。'
  return '拖动节点 / 河段 / 站点 / 湖泊调整位置，单击查看属性；空白处拖动平移，滚轮缩放。'
})

function setTool(t) {
  state.sch.tool = state.sch.tool === t ? 'select' : t
}

async function relayout() {
  await buildSchematic({ reset: true })
}
</script>

<style scoped>
.tools {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
}
.tool {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 6px 7px;
  border: 1px solid var(--line-strong);
  border-radius: 7px;
  background: #fff;
  color: var(--text-2);
  font-size: 11px;
  white-space: nowrap;
  transition: all 0.12s;
}
.tool:hover { border-color: var(--primary); color: var(--primary); }
.tool.on {
  background: var(--primary);
  border-color: var(--primary);
  color: #fff;
  box-shadow: 0 2px 6px rgba(30, 111, 168, 0.28);
}

.toggle {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 6px 8px;
  border: 1px solid var(--line-strong);
  border-radius: 7px;
  background: #fff;
  color: var(--text-2);
  font-size: 11.5px;
}
.toggle:hover { border-color: var(--primary); color: var(--primary); }
.toggle.on { background: var(--primary-soft); border-color: #cfe4f2; color: var(--primary); font-weight: 600; }
.toggle .box {
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
.toggle.on .box { background: var(--primary); border-color: var(--primary); color: #fff; }
.toggle:disabled { opacity: .5; cursor: not-allowed; }

.sb-legend {
  max-height: 112px;
  overflow: auto;
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 6px 7px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: #fbfcfe;
}
.sb-chip {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 10.5px;
  color: var(--text-2);
  line-height: 1.5;
}
.sb-chip i {
  width: 9px;
  height: 9px;
  border-radius: 2px;
  flex: 0 0 auto;
  border: 1px solid rgba(0, 0, 0, 0.08);
}
.sb-chip b { color: var(--text); font-weight: 600; }
.sb-chip .nm { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sb-chip em { color: var(--text-3); font-style: normal; flex: 0 0 auto; }
.sb-chip.muted { opacity: .85; }
.sb-chip.on {
  background: var(--primary-soft);
  outline: 1px solid #cfe4f2;
  border-radius: 5px;
}
.sb-chip.on b, .sb-chip.on .nm { color: var(--primary); }
.sb-chip { cursor: pointer; }
.sb-chip:hover { background: #f2f7fb; border-radius: 5px; }

.field label {
  display: block;
  color: var(--text-2);
  font-size: 11px;
  margin-bottom: 3px;
}
.field label b { color: var(--primary); }
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
.field input[type='range'] { width: 100%; }

.btn-row { display: flex; gap: 6px; }
.btn-row .btn { flex: 1; }

.hint {
  font-size: 10.5px;
  line-height: 1.55;
  color: var(--text-3);
  padding-top: 1px;
  border-top: 1px dashed var(--line);
}
</style>
