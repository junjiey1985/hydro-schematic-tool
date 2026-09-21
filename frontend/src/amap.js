/**
 * 高德地图底图（GCJ-02 火星坐标）。
 *
 * 合规说明：国内地图必须使用具备测绘资质的合规底图服务（高德 / 腾讯 / 百度 / 天地图），
 * 不使用 OpenStreetMap 等境外瓦片源。本模块只使用高德官方栅格瓦片服务。
 *
 * 密钥：不硬编码在源码中，从环境变量读取（frontend/.env.local，已加入 .gitignore）：
 *   VITE_AMAP_KEY=在高德开放平台申请的 Web 端 Key
 *   VITE_AMAP_SECURITY_CODE=对应的安全密钥
 * 安全密钥（securityJsCode）仅在使用高德 JS API（地理编码、路径规划等）时需要，
 * 使用前先设置：window._AMapSecurityConfig = { securityJsCode: AMAP_SECURITY_CODE }。
 * 本项目的底图走标准瓦片服务（OpenLayers XYZ 源），不依赖 JS API。
 *
 * 注意：高德底图为 GCJ-02 坐标，与本项目 WGS-84 数据相差 300~600 m，
 * 所有图层在渲染前必须经 gcj.js 纠偏，否则会出现整体偏移。
 */
import XYZ from 'ol/source/XYZ'

export const AMAP_KEY = String(import.meta.env.VITE_AMAP_KEY || '').trim()
export const AMAP_SECURITY_CODE = String(import.meta.env.VITE_AMAP_SECURITY_CODE || '').trim()

const KEY_SUFFIX = AMAP_KEY ? `&key=${encodeURIComponent(AMAP_KEY)}` : ''

/** 高德标准矢量底图（路网 + 注记） */
export const AMAP_VECTOR_URL = `https://webrd0{1-4}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}${KEY_SUFFIX}`
/** 高德卫星影像（无注记） */
export const AMAP_SAT_URL = `https://webst0{1-4}.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}${KEY_SUFFIX}`
/** 高德注记（叠加在影像之上） */
export const AMAP_SAT_LABEL_URL = `https://webst0{1-4}.is.autonavi.com/appmaptile?style=8&x={x}&y={y}&z={z}${KEY_SUFFIX}`

export const AMAP_ATTRIBUTION = '© 高德地图'

/** 底图选项（与 state.map.basemap 取值一致） */
export const BASEMAPS = [
  { value: 'none', label: '无底图' },
  { value: 'amap', label: '高德地图' },
  { value: 'amap_sat', label: '高德影像' }
]

export function createAmapSource(kind = 'vector') {
  const url = { vector: AMAP_VECTOR_URL, sat: AMAP_SAT_URL, label: AMAP_SAT_LABEL_URL }[kind] || AMAP_VECTOR_URL
  return new XYZ({
    url,
    maxZoom: 18,
    crossOrigin: 'anonymous',
    attributions: AMAP_ATTRIBUTION
  })
}
