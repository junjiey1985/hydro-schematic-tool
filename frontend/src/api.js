const BASE = ''

async function request(path, { method = 'GET', body, form } = {}) {
  const opts = { method, headers: {} }
  if (form) {
    opts.body = form
  } else if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json'
    opts.body = JSON.stringify(body)
  }
  const res = await fetch(BASE + path, opts)
  const text = await res.text()
  let data = null
  try {
    data = text ? JSON.parse(text) : null
  } catch (e) {
    data = { detail: text }
  }
  if (!res.ok) {
    const msg = (data && (data.detail || data.error || data.message)) || `请求失败 (${res.status})`
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg))
  }
  return data
}

export const api = {
  // ---------------- 项目
  listProjects: () => request('/api/projects'),
  createProject: (name, description) => request('/api/projects', { method: 'POST', body: { name, description } }),
  getProject: (pid) => request(`/api/projects/${pid}`),
  deleteProject: (pid) => request(`/api/projects/${pid}`, { method: 'DELETE' }),
  samplesAvailable: () => request('/api/projects/samples/available'),
  importSamples: (payload = {}) => request('/api/projects/samples/import', { method: 'POST', body: payload }),

  // ---------------- 图层
  listLayers: (pid) => request(`/api/projects/${pid}/layers`),
  getLayer: (pid, lid) => request(`/api/projects/${pid}/layers/${lid}`),
  createLayer: (pid, payload) => request(`/api/projects/${pid}/layers`, { method: 'POST', body: payload }),
  updateLayer: (pid, lid, patch) => request(`/api/projects/${pid}/layers/${lid}`, { method: 'PATCH', body: patch }),
  deleteLayer: (pid, lid) => request(`/api/projects/${pid}/layers/${lid}`, { method: 'DELETE' }),
  uploadShp: (pid, files, { layerType, name, encoding } = {}) => {
    const fd = new FormData()
    for (const f of files) fd.append('files', f)
    if (layerType) fd.append('layer_type', layerType)
    if (name) fd.append('name', name)
    if (encoding) fd.append('encoding', encoding)
    return request(`/api/projects/${pid}/layers/upload`, { method: 'POST', form: fd })
  },
  addFeatures: (pid, lid, features) => request(`/api/projects/${pid}/layers/${lid}/features`, { method: 'POST', body: { features } }),
  updateFeature: (pid, lid, fid, patch) => request(`/api/projects/${pid}/layers/${lid}/features/${fid}`, { method: 'PUT', body: patch }),
  deleteFeature: (pid, lid, fid) => request(`/api/projects/${pid}/layers/${lid}/features/${fid}`, { method: 'DELETE' }),

  // ---------------- 拓扑
  getTopology: (pid) => request(`/api/projects/${pid}/topology`),
  buildTopology: (pid, payload = {}) => request(`/api/projects/${pid}/topology/build`, { method: 'POST', body: payload }),
  topologyGeoJSON: (pid) => request(`/api/projects/${pid}/topology/geojson`),
  clearTopology: (pid) => request(`/api/projects/${pid}/topology`, { method: 'DELETE' }),

  // ---------------- 概化图
  getSchematic: (pid) => request(`/api/projects/${pid}/schematic`),
  buildSchematic: (pid, payload = {}) => request(`/api/projects/${pid}/schematic/build`, { method: 'POST', body: payload }),
  editSchematic: (pid, edits) => request(`/api/projects/${pid}/schematic/edit`, { method: 'POST', body: edits }),
  resetSchematic: (pid, payload = {}) => request(`/api/projects/${pid}/schematic/reset`, { method: 'POST', body: payload }),
  svgUrl: (pid) => `/api/projects/${pid}/schematic/export.svg`,

  // ---------------- DEM
  getDem: (pid) => request(`/api/projects/${pid}/dem`),
  uploadDem: (pid, file) => {
    const fd = new FormData()
    fd.append('file', file)
    return request(`/api/projects/${pid}/dem/upload`, { method: 'POST', form: fd })
  },
  extractDem: (pid, payload = {}) => request(`/api/projects/${pid}/dem/extract`, { method: 'POST', body: payload }),
  deleteDem: (pid) => request(`/api/projects/${pid}/dem`, { method: 'DELETE' }),
  reliefUrl: (pid) => `/api/projects/${pid}/dem/relief`,

  // ---------------- 子流域 / 预报单元
  subbasinOptions: (pid) => request(`/api/projects/${pid}/subbasins/options`),
  getSubbasins: (pid) => request(`/api/projects/${pid}/subbasins`),
  delineateSubbasins: (pid, payload = {}) =>
    request(`/api/projects/${pid}/subbasins/delineate`, { method: 'POST', body: payload }),
  patchSubbasin: (pid, code, patch) =>
    request(`/api/projects/${pid}/subbasins/${encodeURIComponent(code)}`, { method: 'PATCH', body: patch }),
  clearSubbasins: (pid) => request(`/api/projects/${pid}/subbasins`, { method: 'DELETE' }),
  subbasinsUrl: (pid, fmt = 'geo') => `/api/projects/${pid}/subbasins/export.${fmt}`,

  // ---------------- 时序数据（率定输入）
  timeseriesManifest: (pid) => request(`/api/projects/${pid}/timeseries/manifest`),
  timeseriesImport: (pid, files, { kind = 'rain', interval = '', station = '', encoding = '' } = {}) => {
    const fd = new FormData()
    for (const f of files) fd.append('files', f)
    fd.append('kind', kind)
    fd.append('interval', interval)
    fd.append('station', station)
    fd.append('encoding', encoding)
    return request(`/api/projects/${pid}/timeseries/import`, { method: 'POST', form: fd })
  },
  timeseriesSeries: (pid, kind, key, limit = 0) =>
    request(`/api/projects/${pid}/timeseries/${kind}/${encodeURIComponent(key)}?limit=${limit}`),
  timeseriesDelete: (pid, kind, key) =>
    request(`/api/projects/${pid}/timeseries/${kind}/${encodeURIComponent(key)}`, { method: 'DELETE' }),
  timeseriesClear: (pid, kind = '') =>
    request(`/api/projects/${pid}/timeseries${kind ? `?kind=${kind}` : ''}`, { method: 'DELETE' }),
  timeseriesDemo: (pid, payload = {}) =>
    request(`/api/projects/${pid}/timeseries/demo`, { method: 'POST', body: payload }),

  // ---------------- 模型模拟与率定（P2：params / apply / simulate）
  calibrationParams: (pid) => request(`/api/projects/${pid}/calibration/params`),
  calibrationApply: (pid, payload = {}) =>
    request(`/api/projects/${pid}/calibration/apply`, { method: 'POST', body: payload }),
  calibrationSimulate: (pid, payload = {}) =>
    request(`/api/projects/${pid}/calibration/simulate`, { method: 'POST', body: payload }),

  // ---------------- 率定任务（P3）
  calibrationRun: (pid, payload = {}) =>
    request(`/api/projects/${pid}/calibration/run`, { method: 'POST', body: payload }),
  calibrationRuns: (pid) => request(`/api/projects/${pid}/calibration/runs`),
  calibrationStatus: (pid, rid) => request(`/api/projects/${pid}/calibration/runs/${rid}/status`),
  calibrationStop: (pid, rid) =>
    request(`/api/projects/${pid}/calibration/runs/${rid}/stop`, { method: 'POST' }),
  calibrationResult: (pid, rid) => request(`/api/projects/${pid}/calibration/runs/${rid}/result`),
  calibrationConvergence: (pid, rid) =>
    request(`/api/projects/${pid}/calibration/runs/${rid}/convergence`),

  // ---------------- 情景预报（P6）
  calibrationForecast: (pid, payload = {}) =>
    request(`/api/projects/${pid}/calibration/forecast`, { method: 'POST', body: payload })
}
