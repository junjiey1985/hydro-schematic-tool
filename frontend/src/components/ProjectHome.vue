<template>
  <div class="home">
    <div class="home-wrap">
      <!-- ============ 头部 ============ -->
      <div class="home-head">
        <div class="brand-lg">
          <div class="logo">水</div>
          <div>
            <h1>流域水文建模平台</h1>
            <p>河流水系数字化 · 拓扑概化 · 水文模拟与参数率定</p>
          </div>
        </div>
        <div class="home-actions">
          <input
            v-model="filter"
            class="home-search"
            type="text"
            placeholder="按名称 / 说明筛选项目…"
          />
          <button class="btn" @click="pickImport">导入项目 zip</button>
          <input ref="importFile" type="file" accept=".zip" style="display: none" @change="doImport" />
          <button class="btn primary" @click="$emit('create', 'blank')">＋ 新建项目</button>
        </div>
      </div>

      <!-- ============ 项目卡片 ============ -->
      <div v-if="!ready" class="home-tip"><div class="spin"></div>正在加载…</div>

      <div v-else class="cards">
        <div
          v-for="p in filtered"
          :key="p.id"
          class="card"
          @click="editId !== p.id && $emit('open', p.id)"
        >
          <!-- 编辑态：重命名 -->
          <template v-if="editId === p.id">
            <div class="edit-form" @click.stop>
              <div class="field"><label>项目名称</label>
                <input ref="renameInput" v-model="editName" type="text" @keyup.enter="saveRename(p.id)" />
              </div>
              <div class="field"><label>说明（可选）</label>
                <textarea v-model="editDesc" rows="2"></textarea>
              </div>
              <div class="edit-btns">
                <button class="btn sm" @click="editId = ''">取消</button>
                <button class="btn sm primary" @click="saveRename(p.id)">保存</button>
              </div>
            </div>
          </template>

          <!-- 展示态 -->
          <template v-else>
            <div class="card-top">
              <div class="card-name" :title="p.name">{{ p.name }}</div>
              <a
                class="card-del"
                :href="`/api/projects/${p.id}/export`"
                :download="`${p.name}.zip`"
                title="导出项目 zip（备份 / 迁移）"
                @click.stop
              >⤓</a>
              <button class="card-del" title="重命名" @click.stop="startRename(p)">✎</button>
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

            <div v-if="p.description" class="card-desc" :title="p.description">{{ p.description }}</div>

            <div class="card-tags">
              <span class="wf" :class="{ on: p.has_dem }" title="DEM 与河网">DEM</span>
              <span class="wf" :class="{ on: p.has_topology }" title="拓扑关系">拓扑</span>
              <span class="wf" :class="{ on: p.has_schematic }" title="水系概化图">概化图</span>
              <span class="wf" :class="{ on: p.has_subbasins }" title="预报单元">单元</span>
              <span class="wf" :class="{ on: p.has_timeseries }" title="时序数据">时序</span>
              <span class="wf" :class="{ on: p.has_calib }" title="率定结果">率定</span>
              <span class="tag">{{ p.layer_count }} 个图层</span>
            </div>

            <div class="card-meta">
              <span>创建于 {{ fmtDate(p.created_at) }}</span>
              <span>更新于 {{ fmtDate(p.updated_at) }}</span>
            </div>

            <div class="card-open">进入工作台 →</div>
          </template>
        </div>

        <!-- 新建卡片 -->
        <div class="card new" @click="$emit('create', 'blank')">
          <div class="plus">＋</div>
          <div class="new-t">新建项目</div>
          <div class="new-d">导入 SHP / DEM 数据，或用示例流域快速开始</div>
        </div>
      </div>

      <div v-if="ready && !filtered.length" class="home-empty">
        {{ projects.length ? '没有匹配的项目——试试清空筛选条件。' : '还没有项目——点「＋ 新建项目」后选择 示例项目，一键体验 DEM 生成河网 → 拓扑 → 概化图 → 率定全流程。' }}
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
import { state, loadProjects, removeProjectFromHome, toast } from '../store'
import { api } from '../api'

defineEmits(['open', 'create'])

const ready = computed(() => state.ready)
const projects = computed(() => state.projects)
const busy = computed(() => state.busy)

const filter = ref('')
const filtered = computed(() => {
  const q = filter.value.trim().toLowerCase()
  if (!q) return projects.value
  return projects.value.filter(
    (p) =>
      (p.name || '').toLowerCase().includes(q) ||
      (p.description || '').toLowerCase().includes(q)
  )
})

const confirmDel = ref('')
let delTimer = null

// 导入项目 zip
const importFile = ref(null)

function pickImport() {
  if (importFile.value) importFile.value.click()
}

async function doImport(e) {
  const file = (e.target.files || [])[0]
  if (fileInputReset(e.target)) return
  try {
    const meta = await api.importProject(file)
    await loadProjects()
    toast(`已导入为「${meta.name}」`, 'ok')
  } catch (err) {
    toast(err.message || '导入失败', 'err', 5200)
  }
}

function fileInputReset(input) {
  const empty = !input.files || !input.files.length
  input.value = ''
  return empty
}

// 重命名
const editId = ref('')
const editName = ref('')
const editDesc = ref('')
const renameInput = ref(null)

function startRename(p) {
  editId.value = p.id
  editName.value = p.name || ''
  editDesc.value = p.description || ''
  requestAnimationFrame(() => {
    const el = Array.isArray(renameInput.value) ? renameInput.value[0] : renameInput.value
    if (el) el.focus()
  })
}

async function saveRename(pid) {
  const name = editName.value.trim()
  if (!name) {
    toast('项目名称不能为空', 'err')
    return
  }
  try {
    await api.updateProject(pid, { name, description: editDesc.value })
    await loadProjects()
    editId.value = ''
    toast('已保存', 'ok')
  } catch (e) {
    toast(e.message || '保存失败', 'err', 5200)
  }
}

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
  align-items: center;
}
.home-search {
  width: 220px;
  padding: 7px 12px;
  border: 1px solid #d7dfe7;
  border-radius: 8px;
  font-size: 13px;
  color: #35505f;
  background: #fff;
  outline: none;
}
.home-search:focus {
  border-color: #1e6fa8;
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
  text-decoration: none;
}
.card-del:hover {
  color: #1e6fa8;
  background: #eaf2f8;
}
.card-del.sure {
  color: #fff;
  background: #c0392b;
  font-weight: 600;
}
.card-del.sure:hover {
  color: #fff;
  background: #a93226;
}
.card-desc {
  margin-top: 8px;
  font-size: 12px;
  color: #7b8a97;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.card-tags {
  display: flex;
  gap: 5px;
  flex-wrap: wrap;
  align-items: center;
  margin-top: 12px;
}
.wf {
  font-size: 11px;
  line-height: 1;
  padding: 4px 7px;
  border-radius: 5px;
  background: #f0f3f6;
  color: #a7b4bf;
  border: 1px solid transparent;
}
.wf.on {
  background: #e8f4ee;
  color: #2e8b74;
  border-color: #bfe0d2;
  font-weight: 600;
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

/* 编辑态 */
.edit-form {
  cursor: default;
}
.edit-form .field {
  margin-bottom: 10px;
}
.edit-form label {
  display: block;
  font-size: 12px;
  color: #7b8a97;
  margin-bottom: 4px;
}
.edit-form input,
.edit-form textarea {
  width: 100%;
  padding: 7px 10px;
  border: 1px solid #d7dfe7;
  border-radius: 8px;
  font-size: 13px;
  color: #1c2b36;
  outline: none;
  box-sizing: border-box;
}
.edit-form input:focus,
.edit-form textarea:focus {
  border-color: #1e6fa8;
}
.edit-btns {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
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
