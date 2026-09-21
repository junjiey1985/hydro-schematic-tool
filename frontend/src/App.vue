<template>
  <div class="app">
    <!-- ============ 顶栏 ============ -->
    <div class="topbar">
      <div class="brand">
        <div class="logo">水</div>
        <span>水系概化图工具</span>
      </div>

      <select class="proj-sel" :value="state.project ? state.project.id : ''" @change="onProjectChange">
        <option v-if="!state.projects.length" value="">（暂无项目）</option>
        <option v-for="p in state.projects" :key="p.id" :value="p.id">{{ p.name }}</option>
      </select>

      <button class="btn sm" @click="openNewProject">新建项目</button>
      <button class="btn sm danger" :disabled="!state.project" @click="ui.delProject = true">删除项目</button>

      <span class="vdiv"></span>

      <button class="btn sm" :disabled="!state.project" :title="demTitle" @click="ui.demTool = true">
        <span v-if="state.dem" class="dem-dot"></span>
        DEM 与河网自动生成
      </button>

      <button class="btn sm" :disabled="!state.project" :title="subTitle" @click="openSubbasin">
        <span v-if="state.subbasins" class="sub-dot"></span>
        子流域划分
      </button>

      <div class="spacer"></div>

      <span v-if="state.topology" class="tag ok">
        拓扑：{{ state.stats.edges }} 河段 / {{ state.stats.nodes }} 节点
      </span>
      <span v-else class="tag warn">尚未构建拓扑</span>
      <span v-if="state.subbasins" class="tag ok">预报单元：{{ state.stats.subbasins }}</span>

      <button
        v-if="state.tab === 'schematic'"
        class="btn primary"
        :disabled="!state.schDirty"
        @click="saveSchematic()"
      >
        {{ state.schDirty ? '保存概化图 *' : '已保存' }}
      </button>
    </div>

    <!-- ============ 主体 ============ -->
    <div class="main">
      <SidePanel
        @open-upload="ui.upload = true"
        @open-topo-options="ui.topoOptions = true"
        @open-subbasin="openSubbasin"
      />

      <div class="stage">
        <MapView v-show="state.tab === 'map'" />
        <SchematicView v-if="state.tab === 'schematic'" />

        <!-- 视图切换（浮于中间区域顶部居中） -->
        <div class="view-tabs">
          <button :class="{ on: state.tab === 'map' }" @click="state.tab = 'map'">地图视图</button>
          <button :class="{ on: state.tab === 'schematic' }" @click="state.tab = 'schematic'">
            概化图视图
          </button>
        </div>

        <div v-if="!state.project" class="empty">
          <div class="big">还没有项目</div>
          <div>可以新建一个示例项目，立刻体验 DEM 生成河网 → 拓扑 → 概化图的全流程</div>
          <div class="btn-row" style="justify-content: center">
            <button class="btn primary" @click="openNewProject('sample')">用示例数据快速开始</button>
            <button class="btn" @click="openNewProject('blank')">新建空项目</button>
          </div>
        </div>

        <div v-if="state.busy" class="busy">
          <div class="spin"></div>
          <span>{{ state.busy }}</span>
        </div>
      </div>
    </div>

    <!-- ============ 提示 ============ -->
    <div v-if="state.toast.show" class="toast" :class="state.toast.type">{{ state.toast.text }}</div>

    <!-- ============ 删除项目 ============ -->
    <div v-if="ui.delProject" class="mask" @click.self="ui.delProject = false">
      <div class="modal" style="width: 380px">
        <h3>删除项目</h3>
        <div class="body">
          确定要删除项目「{{ state.project && state.project.name }}」吗？该项目下的所有图层、拓扑与概化图都会一并删除，且不可恢复。
        </div>
        <div class="foot">
          <button class="btn" @click="ui.delProject = false">取消</button>
          <button class="btn danger" @click="doDeleteProject">确认删除</button>
        </div>
      </div>
    </div>

    <!-- ============ 新建项目 ============ -->
    <div v-if="ui.newProject" class="mask" @click.self="ui.newProject = false">
      <div class="modal">
        <h3>新建项目</h3>
        <div class="body">
          <!-- 创建方式 -->
          <div class="mode-row">
            <div class="mode" :class="{ on: newMode === 'blank' }" @click="pickNewMode('blank')">
              <div class="mode-t">空白项目</div>
              <div class="mode-d">自己导入 SHP / DEM 数据</div>
            </div>
            <div
              class="mode"
              :class="{ on: newMode === 'sample', off: !samplesOk }"
              @click="pickNewMode('sample')"
            >
              <div class="mode-t">示例项目<span v-if="samplesOk" class="tag ok mtag">推荐</span></div>
              <div class="mode-d">预置真实流域数据，开箱即用</div>
            </div>
          </div>

          <!-- 示例项目 -->
          <template v-if="newMode === 'sample'">
            <div v-if="samplesOk" class="hintbar">
              <div class="hb-t">将导入：{{ samplesInfo.name }}</div>
              <div>图层（{{ samplesInfo.layers.length }}）：{{ sampleLayerNames }}</div>
              <div v-if="samplesInfo.dem">
                DEM：{{ samplesInfo.dem.file }}（{{ samplesInfo.dem.ncols }} × {{ samplesInfo.dem.nrows }}，{{ fmtNum(samplesInfo.dem.cellsize, 5) }}°）
              </div>
              <div>导入后自动完成拓扑构建与概化图生成，打开即可查看效果。</div>
            </div>
            <div v-else class="hintbar warn-bar">
              示例数据未生成。请在 backend 目录执行：
              <code>python scripts/make_real_samples.py</code>
            </div>
            <div class="field">
              <label>项目名称（可选）</label>
              <input type="text" v-model="form.name" :placeholder="samplesInfo ? samplesInfo.name : '示例流域'" />
              <div class="hint">留空则使用默认名称「{{ samplesInfo ? samplesInfo.name : '示例流域' }}」</div>
            </div>
          </template>

          <!-- 空白项目 -->
          <template v-else>
            <div class="field">
              <label>项目名称</label>
              <input type="text" v-model="form.name" placeholder="例如：清江流域水系" />
            </div>
            <div class="field">
              <label>说明（可选）</label>
              <textarea v-model="form.description" rows="3"></textarea>
            </div>
          </template>
        </div>
        <div class="foot">
          <button class="btn" @click="ui.newProject = false">取消</button>
          <button class="btn primary" :disabled="!canCreate" @click="doCreateProject">
            {{ newMode === 'sample' ? '创建并导入示例数据' : '创建' }}
          </button>
        </div>
      </div>
    </div>

    <!-- ============ 导入 SHP ============ -->
    <div v-if="ui.upload" class="mask" @click.self="ui.upload = false">
      <div class="modal">
        <h3>导入 SHP 数据</h3>
        <div class="body">
          <div class="field">
            <label>选择文件</label>
            <input ref="fileInput" type="file" multiple accept=".shp,.shx,.dbf,.prj,.cpg,.zip" @change="onPick" />
            <div class="hint">
              请同时选中同名的一组文件（.shp/.shx/.dbf/.prj），或直接上传打包好的 .zip。
              坐标会自动转换到 WGS84；中文属性自动识别 UTF-8 / GBK 编码。
            </div>
          </div>
          <div class="row2">
            <div class="field">
              <label>图层类型</label>
              <select v-model="form.layerType">
                <option value="">自动识别</option>
                <option value="river">河流水系</option>
                <option value="hydro_station">水文站</option>
                <option value="rain_station">雨量站</option>
                <option value="lake">湖泊水库</option>
                <option value="boundary">流域边界</option>
                <option value="subbasin">子流域（预报单元）</option>
                <option value="control_point">控制断面</option>
                <option value="other">其他</option>
              </select>
            </div>
            <div class="field">
              <label>图层名称（可选）</label>
              <input type="text" v-model="form.layerName" placeholder="留空则用文件名" />
            </div>
          </div>
          <div class="field">
            <label>属性编码</label>
            <select v-model="form.encoding">
              <option value="">自动识别</option>
              <option value="utf-8">UTF-8</option>
              <option value="gbk">GBK / GB18030</option>
            </select>
          </div>
        </div>
        <div class="foot">
          <button class="btn" @click="ui.upload = false">取消</button>
          <button class="btn primary" :disabled="!picked.length" @click="doUpload">
            导入 {{ picked.length ? `(${picked.length} 个文件)` : '' }}
          </button>
        </div>
      </div>
    </div>

    <!-- ============ DEM 与河网自动生成 ============ -->
    <div v-if="ui.demTool" class="mask" @click.self="ui.demTool = false">
      <div class="modal">
        <h3>DEM 与河网自动生成</h3>
        <div class="body">
          <div v-if="state.dem" class="hintbar" style="margin-bottom: 10px">
            栅格：{{ state.dem.file }}<br />
            尺寸：{{ state.dem.ncols }} × {{ state.dem.nrows }}（{{ fmtNum(state.dem.cellsize, 5) }}°）<br />
            高程：{{ fmtNum(state.dem.elev_min, 1) }} ~ {{ fmtNum(state.dem.elev_max, 1) }} m
          </div>
          <div class="field">
            <label>选择 DEM 文件</label>
            <input ref="demInput" type="file" accept=".asc,.txt,.grd,.tif,.tiff" @change="onDemPick" />
            <div class="hint">支持 ESRI ASCII Grid(.asc) 与 GeoTIFF(.tif)；重新选择将替换当前 DEM</div>
          </div>
          <template v-if="state.dem">
            <div class="field">
              <label>
                汇流累积阈值：{{ demForm.threshold }} 格
                <span class="muted">（≈ {{ thresholdKm2 }} km²）</span>
              </label>
              <input type="range" min="100" max="6000" step="50" v-model.number="demForm.threshold" style="width: 100%" />
              <div class="hint">阈值越小河网越密；建议 400 ~ 1500</div>
            </div>
            <div class="row2">
              <div class="field">
                <label>抽稀容差（米）</label>
                <input type="number" v-model.number="demForm.simplify_m" min="0" step="10" />
              </div>
              <div class="field">
                <label>干流命名</label>
                <input type="text" v-model="demForm.main_name" />
              </div>
            </div>
            <label class="chk" style="margin-bottom: 8px">
              <input type="checkbox" v-model="demForm.replace" />
              覆盖上一次由 DEM 生成的河网
            </label>
          </template>
        </div>
        <div class="foot">
          <button class="btn danger" v-if="state.dem" style="margin-right: auto" @click="doRemoveDem">移除 DEM</button>
          <button class="btn" @click="ui.demTool = false">关闭</button>
          <button class="btn primary" :disabled="!state.dem || !!state.busy" @click="doExtract">从 DEM 生成河网</button>
        </div>
      </div>
    </div>

    <!-- ============ 子流域划分 · 预报单元 ============ -->
    <div v-if="ui.subbasin" class="mask" @click.self="ui.subbasin = false">
      <div class="modal" style="width: 640px">
        <h3>子流域划分 · 预报单元</h3>
        <div class="body">
          <div v-if="subPrereq" class="hintbar warn-bar" style="margin-bottom: 12px">{{ subPrereq }}</div>

          <div class="sub-sec">① 划分依据（控制断面来源）</div>
          <div class="mode-row">
            <div class="mode" :class="{ on: subForm.mode === 'station' }" @click="subForm.mode = 'station'">
              <div class="mode-t">水文站</div>
              <div class="mode-d">
                {{ stationCount }} 个站作为控制断面，切出与测站对应的预报单元（推荐）
              </div>
            </div>
            <div class="mode" :class="{ on: subForm.mode === 'junction' }" @click="subForm.mode = 'junction'">
              <div class="mode-t">汇流节点</div>
              <div class="mode-d">{{ junctionCount }} 个汇流点自动分区，单元更细、无测站也适用</div>
            </div>
            <div class="mode" :class="{ on: subForm.mode === 'manual' }" @click="subMode('manual')">
              <div class="mode-t">手工控制断面</div>
              <div class="mode-d">在地图上自绘断面点，按实际预报断面切分</div>
            </div>
          </div>

          <div v-if="subForm.mode === 'manual'" class="manual-box">
            <div class="mb-l">
              已绘制控制断面：<b>{{ controlCount }}</b> 个
              <span v-if="!controlCount" class="muted">（先新建图层并绘制，再回到这里划分）</span>
            </div>
            <button class="btn sm" @click="startDrawControl">新建控制断面图层并开始绘制</button>
          </div>

          <div class="sub-sec">② 划分参数</div>
          <div class="row2">
            <div class="field">
              <label>最小单元面积（km²）</label>
              <input type="number" v-model.number="subForm.min_area_km2" min="0" step="10" />
              <div class="hint">合并面积过小的单元（水文站模式下仅作提示）</div>
            </div>
            <div class="field">
              <label>控制断面吸附距离（米）</label>
              <input type="number" v-model.number="subForm.snap_max_m" min="0" step="500" />
              <div class="hint">断面落到最近河道格网的最大容许偏移</div>
            </div>
          </div>
          <div class="row2">
            <div class="field">
              <label>单元名称前缀</label>
              <input type="text" v-model="subForm.name_prefix" />
            </div>
            <div class="field">
              <label>单元命名方式</label>
              <div class="chk-line">
                <label class="chk">
                  <input type="checkbox" v-model="subForm.include_outlet" />
                  含流域出口单元
                </label>
              </div>
            </div>
          </div>

          <!-- ============ 划分结果 ============ -->
          <template v-if="subList.length">
            <div class="sub-sec">③ 划分结果</div>
            <div class="stat-grid" style="margin-bottom: 10px">
              <div class="k">预报单元数</div><div class="v">{{ subList.length }}</div>
              <div class="k">单元合计面积</div><div class="v">{{ fmtNum(subStats.total_area_km2, 2) }} km²</div>
              <div class="k">流域口径总面积</div><div class="v">{{ fmtNum(subStats.basin_area_km2, 2) }} km²</div>
              <div class="k">未归属面积</div><div class="v">{{ fmtNum(subStats.unassigned_area_km2, 2) }} km²</div>
              <div class="k">最大汇流级</div><div class="v">{{ subStats.max_order }}</div>
              <div class="k">计算耗时</div><div class="v">{{ fmtNum(subStats.elapsed_s, 1) }} s</div>
            </div>

            <div class="tbl-wrap">
              <table class="tbl">
                <thead>
                  <tr>
                    <th style="width: 52px">编号</th>
                    <th style="width: 110px">名称</th>
                    <th style="width: 76px">面积 km²</th>
                    <th style="width: 88px">上游面积</th>
                    <th style="width: 108px">出口断面</th>
                    <th style="width: 74px">上级单元</th>
                    <th style="width: 62px">河段数</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="sb in subList" :key="sb.code" @click="gotoSubbasin(sb)">
                    <td><span class="dot" :style="{ background: subbasinColorOf(sb) }"></span>{{ sb.code }}</td>
                    <td>
                      <input
                        class="mini"
                        :value="sb.name"
                        @click.stop
                        @change="doRenameSub(sb, $event.target.value)"
                      />
                    </td>
                    <td class="num">{{ fmtNum(sb.area_km2, 2) }}</td>
                    <td class="num">{{ fmtNum(sb.upstream_area_km2, 2) }}</td>
                    <td>{{ sb.outlet_station ? sb.outlet_station.name : sb.outlet.control || '—' }}</td>
                    <td>{{ sb.parent || '—' }}</td>
                    <td class="num">{{ sb.edge_count }}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div class="warn-list" v-if="subWarnings.length">
              <div v-for="(w, i) in subWarnings" :key="i">· {{ w }}</div>
            </div>
          </template>
        </div>

        <div class="foot">
          <button
            v-if="subList.length"
            class="btn danger"
            style="margin-right: auto"
            @click="doClearSubbasins"
          >
            清除划分结果
          </button>
          <template v-if="subList.length">
            <a class="btn sm" :href="subExport('geojson')" download="subbasins.geojson">导出 GeoJSON</a>
            <a class="btn sm" :href="subExport('csv')" download="subbasins.csv">导出 CSV</a>
            <a class="btn sm" :href="subExport('shp')" download="subbasins.zip">导出 SHP</a>
          </template>
          <button class="btn" @click="ui.subbasin = false">关闭</button>
          <button class="btn primary" :disabled="!canDelineate || !!state.busy" @click="doDelineate">
            {{ subList.length ? '重新划分' : '开始划分' }}
          </button>
        </div>
      </div>
    </div>

    <!-- ============ 构建拓扑参数 ============ -->
    <div v-if="ui.topoOptions" class="mask" @click.self="ui.topoOptions = false">
      <div class="modal">
        <h3>构建拓扑关系</h3>
        <div class="body">
          <div class="row2">
            <div class="field">
              <label>端点吸附容差（米）</label>
              <input type="number" v-model.number="topoForm.snap_tolerance_m" min="0" step="5" />
              <div class="hint">小于该距离的端点视为同一节点</div>
            </div>
            <div class="field">
              <label>站点挂接最大距离（米）</label>
              <input type="number" v-model.number="topoForm.station_snap_max_m" min="0" step="50" />
              <div class="hint">超出则该站点标记为未挂接</div>
            </div>
          </div>
          <div class="field">
            <label>流向判定方式</label>
            <select v-model="topoForm.flow_direction">
              <option value="auto">自动（有 DEM 用 DEM，否则按数字化方向）</option>
              <option value="dem">DEM 高程</option>
              <option value="attribute">属性字段高程</option>
              <option value="digitized">数字化方向</option>
            </select>
          </div>
          <label class="chk" style="margin-bottom: 8px">
            <input type="checkbox" v-model="topoForm.dissolve_pseudo_nodes" />
            合并假节点（仅两条河段首尾相接处）
          </label>
          <label class="chk">
            <input type="checkbox" v-model="topoForm.split_at_hydro_station" />
            在水文站处打断河段（把测站变成控制断面）
          </label>
        </div>
        <div class="foot">
          <button class="btn" @click="ui.topoOptions = false">取消</button>
          <button class="btn primary" @click="doBuildTopology">开始构建</button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import MapView from './components/MapView.vue'
import SchematicView from './components/SchematicView.vue'
import SidePanel from './components/SidePanel.vue'
import { api } from './api'
import {
  state,
  bootstrap,
  createProject,
  importSamples,
  openProject,
  removeProject,
  uploadShp,
  uploadDem,
  extractFromDem,
  deleteDem as removeDem,
  buildTopology,
  saveSchematic,
  withBusy,
  clearSubbasins,
  delineateSubbasins,
  ensureControlPointLayer,
  loadSubbasinOptions,
  pickSubbasin,
  renameSubbasin,
  subbasinColorOf
} from './store'

const ui = reactive({
  newProject: false,
  upload: false,
  topoOptions: false,
  delProject: false,
  demTool: false,
  subbasin: false
})
const form = reactive({ name: '', description: '', layerType: '', layerName: '', encoding: '' })
const newMode = ref('blank')
const samplesInfo = ref(null)
const samplesOk = ref(false)
const topoForm = reactive({
  snap_tolerance_m: 30,
  station_snap_max_m: 800,
  flow_direction: 'auto',
  dissolve_pseudo_nodes: true,
  split_at_hydro_station: true
})
const demForm = reactive({ threshold: 600, simplify_m: 110, main_name: '干流', replace: true })
const subForm = reactive({
  mode: 'station',
  min_area_km2: 30,
  snap_max_m: 10000,
  name_prefix: '子流域',
  include_outlet: true
})
const picked = ref([])
const fileInput = ref(null)
const demInput = ref(null)

// ---------------------------------------------------------------- 子流域划分
const subList = computed(() => (state.subbasins ? state.subbasins.subbasins || [] : []))
const subStats = computed(() => (state.subbasins ? state.subbasins.stats || {} : {}))
const subWarnings = computed(() => (state.subbasins ? state.subbasins.warnings || [] : []))
const stationCount = computed(() => {
  const o = state.subOptions
  if (!o) return 0
  return (o.hydro_stations || []).filter((s) => s.attached).length
})
const junctionCount = computed(() => (state.subOptions ? state.subOptions.junction_count || 0 : 0))
const controlCount = computed(() => {
  const l = state.layers.find((x) => x.type === 'control_point')
  return l ? l.feature_count || 0 : 0
})

const subPrereq = computed(() => {
  if (!state.project) return '尚未选择项目'
  if (!state.dem) return '子流域划分需要 DEM：请先在顶栏「DEM 与河网自动生成」中上传 DEM 并生成河网。'
  if (!state.topology) return '尚未构建河网拓扑：请先在侧栏「拓扑关系与概化图」中点击「构建拓扑关系」。'
  if (subForm.mode === 'station' && stationCount.value === 0)
    return '当前拓扑中没有已挂接的水文站，请改用「汇流节点」或「手工控制断面」模式。'
  if (subForm.mode === 'manual' && controlCount.value === 0)
    return '手工模式需要先在地图上绘制控制断面点。'
  return ''
})

const canDelineate = computed(() => !subPrereq.value && !!state.project)

const subTitle = computed(() =>
  state.subbasins
    ? `已划分 ${subList.value.length} 个预报单元，点击可重新划分或导出`
    : '把流域按控制断面切分为若干预报单元（子流域）'
)

function subExport(fmt) {
  return state.project ? `/api/projects/${state.project.id}/subbasins/export.${fmt}` : '#'
}

async function openSubbasin() {
  if (!state.project) return
  await loadSubbasinOptions(true)
  const o = state.subOptions && state.subOptions.options
  if (o) {
    if (!state.subbasins) {
      subForm.mode = o.mode || 'station'
      subForm.min_area_km2 = o.min_area_km2 ?? 30
      subForm.snap_max_m = o.snap_max_m ?? 10000
      subForm.name_prefix = o.name_prefix || '子流域'
      subForm.include_outlet = o.include_outlet !== false
    } else if (state.subbasins.options) {
      const cur = state.subbasins.options
      subForm.mode = cur.mode || subForm.mode
      subForm.min_area_km2 = cur.min_area_km2 ?? subForm.min_area_km2
      subForm.snap_max_m = cur.snap_max_m ?? subForm.snap_max_m
      subForm.name_prefix = cur.name_prefix || subForm.name_prefix
      subForm.include_outlet = cur.include_outlet !== false
    }
  }
  ui.subbasin = true
}

function subMode(m) {
  subForm.mode = m
  if (m === 'manual' && controlCount.value === 0) {
    toast('请点击右侧按钮新建控制断面图层，然后在地图上点击绘制断面点', 'info', 4200)
  }
}

async function startDrawControl() {
  try {
    const layer = await ensureControlPointLayer()
    state.map.modify = false
    state.map.drawTarget = layer.id
    state.map.drawType = 'Point'
    ui.subbasin = false
    toast('已进入控制断面绘制模式，在地图上点击即可放置断面点', 'ok', 4200)
  } catch (e) {
    toast('创建控制断面图层失败：' + e.message, 'err')
  }
}

async function doDelineate() {
  if (!canDelineate.value) return
  try {
    await delineateSubbasins({
      mode: subForm.mode,
      min_area_km2: Number(subForm.min_area_km2) || 0,
      snap_max_m: Number(subForm.snap_max_m) || 10000,
      name_prefix: subForm.name_prefix || '子流域',
      include_outlet: !!subForm.include_outlet,
      save: true
    })
    await loadSubbasinOptions(true)
  } catch (e) {
    /* store 内已提示 */
  }
}

async function doClearSubbasins() {
  if (!confirm('确定清除子流域划分结果及其地图图层吗？')) return
  await clearSubbasins()
}

async function doRenameSub(sb, name) {
  const v = String(name || '').trim()
  if (!v || v === sb.name) return
  await renameSubbasin(sb.code, v)
}

function gotoSubbasin(sb) {
  ui.subbasin = false
  state.tab = 'map'
  // 等地图容器可见后再定位
  setTimeout(() => pickSubbasin(sb.code), 120)
}

const demTitle = computed(() =>
  state.dem
    ? `当前 DEM：${state.dem.file}（点击可调整参数重新生成河网）`
    : '尚未加载 DEM，点击上传并自动提取河网'
)

watch(
  () => state.dem,
  (d) => {
    if (d && d.accum_threshold) demForm.threshold = d.accum_threshold
  },
  { immediate: true }
)

onMounted(() => {
  bootstrap()
  api
    .samplesAvailable()
    .then((r) => {
      samplesOk.value = !!r.available
      samplesInfo.value = r.available ? r : null
    })
    .catch(() => {
      samplesOk.value = false
    })
})

const sampleLayerNames = computed(() => {
  const ls = (samplesInfo.value && samplesInfo.value.layers) || []
  return ls.map((l) => l.name || l.file).join('、')
})

const canCreate = computed(() =>
  newMode.value === 'sample' ? samplesOk.value : !!form.name.trim()
)

function openNewProject(mode = 'blank') {
  newMode.value = mode === 'sample' && samplesOk.value ? 'sample' : 'blank'
  form.name = ''
  form.description = ''
  ui.newProject = true
}

function pickNewMode(m) {
  if (m === 'sample' && !samplesOk.value) return
  newMode.value = m
}

function onProjectChange(e) {
  const pid = e.target.value
  if (pid) openProject(pid)
}

async function doCreateProject() {
  if (!canCreate.value) return
  const name = form.name.trim()
  if (newMode.value === 'sample') {
    ui.newProject = false
    form.name = ''
    form.description = ''
    await importSamples(name ? { name } : {})
    return
  }
  await withBusy('正在创建…', () => createProject(name, form.description))
  ui.newProject = false
  form.name = ''
  form.description = ''
}

async function doDeleteProject() {
  if (!state.project) return
  ui.delProject = false
  await withBusy('正在删除项目…', () => removeProject(state.project.id))
}

function onPick(e) {
  picked.value = Array.from(e.target.files || [])
}

async function doUpload() {
  const files = picked.value
  if (!files.length) return
  await uploadShp(files, {
    layerType: form.layerType || undefined,
    name: form.layerName || undefined,
    encoding: form.encoding || undefined
  })
  ui.upload = false
  picked.value = []
  if (fileInput.value) fileInput.value.value = ''
}

async function doBuildTopology() {
  ui.topoOptions = false
  await buildTopology({ ...topoForm })
}

function fmtNum(v, n = 2) {
  return v === null || v === undefined ? '—' : Number(v).toFixed(n)
}

const thresholdKm2 = computed(() => {
  const d = state.dem
  if (!d) return '—'
  const mdx = d.cellsize * 111320 * Math.cos((((d.bounds?.[1] || 0) + (d.bounds?.[3] || 0)) / 2) * (Math.PI / 180))
  const mdy = d.cellsize * 110540
  return ((mdx * mdy) / 1e6 * demForm.threshold).toFixed(1)
})

function onDemPick(e) {
  const f = (e.target.files || [])[0]
  if (f) uploadDem(f).catch(() => {})
  e.target.value = ''
}

async function doExtract() {
  const layerName = demForm.replace ? null : undefined
  const existing = state.layers.find((l) => l.source === 'dem')
  try {
    await extractFromDem({
      threshold: demForm.threshold,
      simplify_m: demForm.simplify_m,
      main_name: demForm.main_name,
      layer_name: layerName || undefined,
      replace_layer_id: demForm.replace && existing ? existing.id : undefined
    })
    ui.demTool = false
  } catch (err) {
    /* extractFromDem 内部已提示 */
  }
}

async function doRemoveDem() {
  if (!confirm('确定移除该 DEM 及其分析结果吗？')) return
  await removeDem()
}
</script>

<style scoped>
/* 视图切换：浮在地图/概化图区域顶部居中 */
.view-tabs {
  position: absolute;
  top: 10px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 7;
  display: flex;
  gap: 2px;
  padding: 3px;
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.95);
  border: 1px solid var(--line-strong);
  box-shadow: 0 2px 10px rgba(23, 43, 66, 0.12);
  backdrop-filter: blur(4px);
}
.view-tabs button {
  border: none;
  background: transparent;
  padding: 6px 16px;
  border-radius: 7px;
  color: var(--text-2);
  font-weight: 500;
  transition: all 0.12s;
}
.view-tabs button:hover:not(.on) {
  color: var(--primary);
  background: var(--primary-soft);
}
.view-tabs button.on {
  background: var(--primary);
  color: #fff;
  font-weight: 600;
  box-shadow: 0 1px 3px rgba(30, 111, 168, 0.35);
}

.proj-sel {
  padding: 5px 9px;
  border: 1px solid var(--line-strong);
  border-radius: 6px;
  background: #fff;
  color: var(--text);
  max-width: 220px;
}
.vdiv {
  width: 1px;
  height: 22px;
  background: var(--line);
  flex: 0 0 auto;
}
.dem-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--green);
  flex: 0 0 auto;
}
.sub-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #4f7fd4;
  flex: 0 0 auto;
}
.sub-sec {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-2);
  margin: 4px 0 8px;
  padding-bottom: 5px;
  border-bottom: 1px dashed var(--line);
}
.sub-sec:not(:first-of-type) {
  margin-top: 16px;
}
.manual-box {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  margin-top: -4px;
  margin-bottom: 4px;
  background: #fdf6e7;
  border: 1px solid #f0e0bc;
  border-radius: 7px;
  font-size: 11.5px;
  color: var(--text-2);
}
.manual-box .mb-l {
  flex: 1;
}
.chk-line {
  padding-top: 5px;
}
.tbl-wrap {
  max-height: 260px;
  overflow: auto;
  border: 1px solid var(--line);
  border-radius: 7px;
}
.tbl {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.tbl th {
  position: sticky;
  top: 0;
  background: var(--panel-2);
  color: var(--text-2);
  font-weight: 600;
  text-align: left;
  padding: 6px 7px;
  border-bottom: 1px solid var(--line);
  white-space: nowrap;
}
.tbl td {
  padding: 4px 7px;
  border-bottom: 1px dashed var(--line);
  white-space: nowrap;
}
.tbl tbody tr {
  cursor: pointer;
}
.tbl tbody tr:hover {
  background: var(--primary-soft);
}
.tbl td.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.tbl .dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 2px;
  margin-right: 5px;
}
.tbl input.mini {
  width: 100%;
  padding: 2px 5px;
  border: 1px solid transparent;
  border-radius: 4px;
  background: transparent;
  color: var(--text);
  font-size: 12px;
  outline: none;
}
.tbl input.mini:hover {
  border-color: var(--line-strong);
  background: #fff;
}
.tbl input.mini:focus {
  border-color: var(--primary);
  background: #fff;
}
.warn-list {
  margin-top: 8px;
  font-size: 11px;
  line-height: 1.65;
  color: var(--amber);
}
.mode-row {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}
.mode {
  flex: 1 1 0;
  padding: 10px 12px;
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  background: #fff;
  cursor: pointer;
  transition: all 0.12s;
}
.mode:hover {
  border-color: var(--primary);
}
.mode.on {
  border-color: var(--primary);
  background: var(--primary-soft);
  box-shadow: inset 0 0 0 1px var(--primary);
}
.mode.off {
  opacity: 0.5;
  cursor: not-allowed;
}
.mode-t {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text);
}
.mode-d {
  margin-top: 3px;
  font-size: 11px;
  color: var(--text-3);
  line-height: 1.5;
}
.mtag {
  font-size: 10px;
  padding: 0 5px;
}
.hb-t {
  font-weight: 600;
  color: var(--text);
  margin-bottom: 3px;
}
.warn-bar {
  border-left: 3px solid var(--amber);
  color: var(--text-2);
}
.warn-bar code {
  background: rgba(0, 0, 0, 0.05);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 11px;
}
</style>
