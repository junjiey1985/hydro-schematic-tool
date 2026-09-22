<template>
  <div class="home">
    <div class="home-wrap">
      <!-- ============ 头部 ============ -->
      <div class="home-head">
        <div class="brand-lg">
          <div class="logo">水</div>
          <div>
            <h1>水系概化图工具</h1>
            <p>河流水系数字化 · 拓扑概化 · 水文模拟与参数率定</p>
          </div>
        </div>
        <div class="home-actions">
          <button class="btn ghost" @click="$emit('create', 'sample')">导入示例流域</button>
          <button class="btn primary" @click="$emit('create', 'blank')">＋ 新建项目</button>
        </div>
      </div>

      <!-- ============ 项目卡片 ============ -->
      <div v-if="!ready" class="home-tip"><div class="spin"></div>正在加载…</div>

      <div v-else class="cards">
        <div
          v-for="p in projects"
          :key="p.id"
          class="card"
          @click="$emit('open', p.id)"
        >
          <div class="card-top">
            <div class="card-name" :title="p.name">{{ p.name }}</div>
            <button
              v-if="confirmDel !== p.id"
              class="card-del"
              title="删除项目"
              @click.stop="confirmDel = p.id; delTimer = setTimeout(() => (confirmDel = ''), 3000)"
            >✕</button>
            <template v-else>
              <button class="card-del sure" title="再次点击确认删除" @click.stop="doDelete(p.id)">确认删除</button>
            </template>
          </div>

          <div class="card-tags">
            <span class="tag ok">拓扑 ✓</span>
            <span v-if="p.has_schematic" class="tag ok">概化图 ✓</span>
            <span v-else class="tag">概化图未生成</span>
            <span class="tag">{{ p.layer_count }} 个图层</span>
          </div>

          <div class="card-meta">
            <span>创建于 {{ fmtDate(p.created_at) }}</span>
            <span>更新于 {{ fmtDate(p.updated_at) }}</span>
          </div>

          <div class="card-open">进入工作台 →</div>
        </div>

        <!-- 新建卡片 -->
        <div class="card new" @click="$emit('create', 'blank')">
          <div class="plus">＋</div>
          <div class="new-t">新建项目</div>
          <div class="new-d">导入 SHP / DEM 数据，或用示例流域快速开始</div>
        </div>
      </div>

      <div v-if="ready && !projects.length" class="home-empty">
        还没有项目——点上面「导入示例流域」可一键体验 DEM 生成河网 → 拓扑 → 概化图 → 率定全流程。
      </div>
    </div>

    <!-- 忙碌遮罩 -->
    <div v-if="busy" class="busy">
      <div class="spin"></div>
      <span>{{ busy }}</span>
    </div>
  </div>
</template>

<script setup>
import { onMounted, onBeforeUnmount, ref, computed } from 'vue'
import { state, loadProjects, removeProjectFromHome } from '../store'

defineEmits(['open', 'create'])

const ready = computed(() => state.ready)
const projects = computed(() => state.projects)
const busy = computed(() => state.busy)

const confirmDel = ref('')
let delTimer = null

onMounted(() => {
  // 回到开始页时刷新一次列表（含其他会话的改动）
  loadProjects().catch(() => {})
})
onBeforeUnmount(() => {
  if (delTimer) clearTimeout(delTimer)
})

async function doDelete(pid) {
  if (delTimer) clearTimeout(delTimer)
  confirmDel.value = ''
  try {
    await removeProjectFromHome(pid)
  } catch (e) {
    /* toast 已在 store 中报错 */
  }
}

function fmtDate(s) {
  if (!s) return '—'
  return String(s).slice(0, 10)
}
</script>

<style scoped>
.home {
  position: fixed;
  inset: 0;
  overflow: auto;
  background:
    radial-gradient(1100px 500px at 12% -10%, #e3eef7 0%, transparent 60%),
    radial-gradient(900px 460px at 95% 0%, #e8f1ea 0%, transparent 55%),
    #f5f7fa;
  z-index: 50;
}
.home-wrap {
  max-width: 1080px;
  margin: 0 auto;
  padding: 56px 28px 48px;
}
.home-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 34px;
  flex-wrap: wrap;
}
.brand-lg {
  display: flex;
  align-items: center;
  gap: 16px;
}
.brand-lg .logo {
  width: 52px;
  height: 52px;
  border-radius: 14px;
  background: linear-gradient(135deg, #1e6fa8, #2e8b74);
  color: #fff;
  font-size: 26px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 6px 18px rgba(30, 111, 168, 0.28);
}
.brand-lg h1 {
  margin: 0;
  font-size: 24px;
  color: #1c2b36;
}
.brand-lg p {
  margin: 4px 0 0;
  font-size: 13px;
  color: #7b8a97;
}
.home-actions {
  display: flex;
  gap: 10px;
}
.btn.ghost {
  background: #fff;
  border: 1px solid #d7dfe7;
  color: #35505f;
}

/* ---- 卡片网格 ---- */
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 18px;
}
.card {
  position: relative;
  background: #fff;
  border: 1px solid #e3e9ef;
  border-radius: 14px;
  padding: 18px 18px 14px;
  cursor: pointer;
  transition: box-shadow 0.18s, transform 0.18s, border-color 0.18s;
}
.card:hover {
  transform: translateY(-3px);
  border-color: #9fc3dc;
  box-shadow: 0 10px 26px rgba(30, 111, 168, 0.13);
}
.card-top {
  display: flex;
  align-items: center;
  gap: 8px;
}
.card-name {
  flex: 1;
  font-size: 16px;
  font-weight: 600;
  color: #1c2b36;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.card-del {
  border: none;
  background: transparent;
  color: #a7b4bf;
  cursor: pointer;
  font-size: 12px;
  padding: 2px 6px;
  border-radius: 6px;
}
.card-del:hover {
  color: #c0392b;
  background: #fbeeea;
}
.card-del.sure {
  color: #fff;
  background: #c0392b;
  font-weight: 600;
}
.card-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 12px;
}
.card-meta {
  display: flex;
  justify-content: space-between;
  margin-top: 12px;
  font-size: 12px;
  color: #8a97a5;
}
.card-open {
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px dashed #e7edf2;
  font-size: 13px;
  color: #1e6fa8;
  opacity: 0;
  transition: opacity 0.15s;
}
.card:hover .card-open {
  opacity: 1;
}

/* 新建卡片 */
.card.new {
  border-style: dashed;
  border-color: #c3d2dd;
  background: rgba(255, 255, 255, 0.55);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 150px;
  text-align: center;
}
.card.new:hover {
  border-color: #1e6fa8;
}
.card.new .plus {
  font-size: 30px;
  color: #7fa8c4;
  line-height: 1;
}
.card.new .new-t {
  margin-top: 8px;
  font-weight: 600;
  color: #35505f;
}
.card.new .new-d {
  margin-top: 6px;
  font-size: 12px;
  color: #8a97a5;
  max-width: 220px;
}

.home-tip,
.home-empty {
  margin-top: 30px;
  text-align: center;
  color: #7b8a97;
  font-size: 13px;
}
.home-empty {
  background: #fff;
  border: 1px dashed #d7dfe7;
  border-radius: 12px;
  padding: 22px;
}

.busy {
  position: fixed;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  background: rgba(255, 255, 255, 0.95);
  border: 1px solid #e3e9ef;
  border-radius: 12px;
  padding: 16px 22px;
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  color: #35505f;
  box-shadow: 0 10px 30px rgba(28, 43, 54, 0.12);
  z-index: 60;
}
.spin {
  width: 16px;
  height: 16px;
  border: 2px solid #cfe0ec;
  border-top-color: #1e6fa8;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
