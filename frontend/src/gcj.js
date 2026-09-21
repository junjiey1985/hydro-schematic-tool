/**
 * GCJ-02（火星坐标 / 高德、腾讯底图坐标）与 WGS-84（GPS / 本项目数据坐标）互转。
 *
 * 背景：国内合规底图（高德、腾讯）使用 GCJ-02，与 WGS-84 相差约 300~600 m。
 * 本项目数据（SHP 导入、DEM、拓扑计算、子流域划分）统一为 WGS-84，
 * 因此地图显示时把数据整体纠偏到 GCJ-02，落库前再转回 WGS-84。
 *
 * 精度：正解为标准算法；反解用迭代逼近，迭代 4 次后误差 < 1e-9 度（亚毫米级）。
 */

const PI = Math.PI
const A = 6378245.0 // 克拉索夫斯基椭球长半轴
const EE = 0.00669342162296594323 // 第一偏心率平方

/** 中国大陆粗略范围之外不做偏移（港澳台及境外本身即 WGS-84 显示）。 */
function outOfChina(lon, lat) {
  return !(lon > 73.66 && lon < 135.05 && lat > 3.86 && lat < 53.55)
}

function transformLat(x, y) {
  let ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x))
  ret += ((20.0 * Math.sin(6.0 * x * PI) + 20.0 * Math.sin(2.0 * x * PI)) * 2.0) / 3.0
  ret += ((20.0 * Math.sin(y * PI) + 40.0 * Math.sin((y / 3.0) * PI)) * 2.0) / 3.0
  ret += ((160.0 * Math.sin((y / 12.0) * PI) + 320 * Math.sin((y * PI) / 30.0)) * 2.0) / 3.0
  return ret
}

function transformLon(x, y) {
  let ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x))
  ret += ((20.0 * Math.sin(6.0 * x * PI) + 20.0 * Math.sin(2.0 * x * PI)) * 2.0) / 3.0
  ret += ((20.0 * Math.sin(x * PI) + 40.0 * Math.sin((x / 3.0) * PI)) * 2.0) / 3.0
  ret += ((150.0 * Math.sin((x / 12.0) * PI) + 300.0 * Math.sin((x / 30.0) * PI)) * 2.0) / 3.0
  return ret
}

/** WGS-84 → GCJ-02 */
export function wgs84ToGcj02(lon, lat) {
  if (outOfChina(lon, lat)) return [lon, lat]
  let dLat = transformLat(lon - 105.0, lat - 35.0)
  let dLon = transformLon(lon - 105.0, lat - 35.0)
  const radLat = (lat / 180.0) * PI
  let magic = Math.sin(radLat)
  magic = 1 - EE * magic * magic
  const sqrtMagic = Math.sqrt(magic)
  dLat = (dLat * 180.0) / (((A * (1 - EE)) / (magic * sqrtMagic)) * PI)
  dLon = (dLon * 180.0) / ((A / sqrtMagic) * Math.cos(radLat) * PI)
  return [lon + dLon, lat + dLat]
}

/** GCJ-02 → WGS-84（迭代反解） */
export function gcj02ToWgs84(lon, lat) {
  if (outOfChina(lon, lat)) return [lon, lat]
  let wlon = lon
  let wlat = lat
  for (let i = 0; i < 4; i++) {
    const [glon, glat] = wgs84ToGcj02(wlon, wlat)
    wlon += lon - glon
    wlat += lat - glat
  }
  return [wlon, wlat]
}

const forward = (c) => wgs84ToGcj02(c[0], c[1])
const inverse = (c) => gcj02ToWgs84(c[0], c[1])

/** 递归处理任意层级的坐标数组（Point / LineString / Polygon / MultiPolygon）。 */
export function mapCoords(coords, fn) {
  if (!coords || !coords.length) return coords
  if (typeof coords[0] === 'number') return fn(coords)
  return coords.map((c) => mapCoords(c, fn))
}

export const coordsToGcj = (coords) => mapCoords(coords, forward)
export const coordsToWgs = (coords) => mapCoords(coords, inverse)
export const pointToGcj = (lon, lat) => wgs84ToGcj02(lon, lat)
export const pointToWgs = (lon, lat) => gcj02ToWgs84(lon, lat)

/**
 * 就地纠偏 OL 几何体（不依赖 ol 包，只用 getCoordinates / setCoordinates）。
 * 支持 Point / MultiPoint / LineString / MultiLineString / Polygon / MultiPolygon / GeometryCollection。
 */
export function transformGeometry(geom, toView = true) {
  if (!geom) return geom
  const t = typeof geom.getType === 'function' ? geom.getType() : ''
  if (t === 'GeometryCollection') {
    geom.getGeometries().forEach((g) => transformGeometry(g, toView))
    return geom
  }
  if (typeof geom.getCoordinates !== 'function' || typeof geom.setCoordinates !== 'function') return geom
  geom.setCoordinates(mapCoords(geom.getCoordinates(), toView ? forward : inverse))
  return geom
}

export const geometryToGcj = (geom) => transformGeometry(geom, true)
export const geometryToWgs = (geom) => transformGeometry(geom, false)

/** 范围纠偏：[minX, minY, maxX, maxY]（取左下、右上两角） */
export function extentToGcj(extent) {
  if (!extent || extent.length < 4) return extent
  const [x0, y0] = wgs84ToGcj02(extent[0], extent[1])
  const [x1, y1] = wgs84ToGcj02(extent[2], extent[3])
  return [x0, y0, x1, y1]
}

export function extentToWgs(extent) {
  if (!extent || extent.length < 4) return extent
  const [x0, y0] = gcj02ToWgs84(extent[0], extent[1])
  const [x1, y1] = gcj02ToWgs84(extent[2], extent[3])
  return [x0, y0, x1, y1]
}
