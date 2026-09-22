/**
 * ECharts 配置构造：纯函数（数据 → option），便于复用。
 * 配色沿用面板既有语义：实测灰、模拟蓝。
 */

const C_OBS = '#7b8a99'
const C_SIM = '#1e6fa8'
const C_GRID = '#f0f3f7'
const C_AXIS = '#e3e8ee'
const C_T2 = '#5b6b7c'
const C_T3 = '#93a1b0'

const TIP = {
  trigger: 'axis',
  backgroundColor: '#ffffff',
  borderColor: C_AXIS,
  borderWidth: 0.5,
  padding: [6, 9],
  textStyle: { color: C_T2, fontSize: 11 },
  axisPointer: { type: 'line', lineStyle: { color: C_AXIS } }
}

const LEGEND = {
  right: 10,
  top: 2,
  itemWidth: 14,
  itemHeight: 2,
  itemGap: 12,
  textStyle: { color: C_T2, fontSize: 11 }
}

const Y_AXIS = {
  type: 'value',
  splitLine: { lineStyle: { color: C_GRID } },
  axisLine: { show: false },
  axisTick: { show: false },
  axisLabel: { color: C_T3, fontSize: 10 }
}

/** 实测 vs 模拟过程线；splitIndex 之后为验证期（浅底色标出）。
 *  传入 rain（与 times 等长的逐时段面雨量 mm）时，右轴倒挂降雨柱垫底，便于判读降雨-流量对应。 */
export function flowOption({
  times = [],
  obs = [],
  sim = [],
  rain = [],
  splitIndex = null,
  yName = 'm³/s',
  rainLabel = 'mm'
} = {}) {
  const n = times.length
  const hasRain = Array.isArray(rain) && rain.length === n && rain.some((v) => Number(v) > 0)
  const lines = [
    {
      name: '实测',
      type: 'line',
      data: obs,
      showSymbol: false,
      symbol: 'none',
      lineStyle: { width: 1.1, color: C_OBS },
      itemStyle: { color: C_OBS },
      emphasis: { focus: 'series' },
      z: 3
    },
    {
      name: '模拟',
      type: 'line',
      data: sim,
      showSymbol: false,
      symbol: 'none',
      lineStyle: { width: 1.5, color: C_SIM },
      itemStyle: { color: C_SIM },
      emphasis: { focus: 'series' },
      z: 4
    }
  ]
  if (splitIndex != null && splitIndex > 0 && splitIndex < n) {
    lines[0].markArea = {
      silent: true,
      itemStyle: { color: 'rgba(30,111,168,0.055)' },
      label: {
        show: true,
        position: 'insideTopRight',
        color: C_T3,
        fontSize: 10,
        formatter: '验证期'
      },
      data: [[{ xAxis: times[splitIndex] }, { xAxis: times[n - 1] }]]
    }
  }
  let series = lines
  let yAxis = { ...Y_AXIS, name: yName, nameTextStyle: { color: C_T3, fontSize: 10, align: 'right' } }
  let grid = { left: 56, right: 14, top: 30, bottom: 44 }
  if (hasRain) {
    const rainMax = rain.reduce((m, v) => Math.max(m, Number(v) || 0), 0)
    series = [
      {
        name: '面雨量',
        type: 'bar',
        yAxisIndex: 1,
        data: rain,
        barWidth: '62%',
        itemStyle: { color: '#a9c4dc' },
        z: 1
      },
      ...lines
    ]
    yAxis = [
      { ...Y_AXIS, name: yName, nameTextStyle: { color: C_T3, fontSize: 10, align: 'right' } },
      {
        type: 'value',
        name: `P (${rainLabel})`,
        inverse: true,
        min: 0,
        max: rainMax > 0 ? Math.max(rainMax * 2.5, 1) : 1,
        splitLine: { show: false },
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: C_T3, fontSize: 10, formatter: (v) => Number(v).toFixed(0) },
        nameTextStyle: { color: C_T3, fontSize: 10, align: 'left' }
      }
    ]
    grid = { left: 56, right: 52, top: 30, bottom: 44 }
  }
  return {
    animation: false,
    grid,
    tooltip: TIP,
    legend: LEGEND,
    xAxis: {
      type: 'category',
      data: times,
      boundaryGap: false,
      axisLine: { lineStyle: { color: C_AXIS } },
      axisTick: { show: false },
      axisLabel: {
        color: C_T3,
        fontSize: 10,
        hideOverlap: true,
        formatter: (v) => String(v).slice(0, 10)
      }
    },
    yAxis,
    dataZoom: [
      { type: 'inside', throttle: 60 },
      {
        type: 'slider',
        height: 15,
        bottom: 6,
        borderColor: 'transparent',
        backgroundColor: '#f7f9fb',
        fillerColor: 'rgba(30,111,168,0.12)',
        handleStyle: { color: C_SIM, borderColor: C_SIM },
        moveHandleSize: 0,
        textStyle: { color: C_T3, fontSize: 10 }
      }
    ],
    series
  }
}

/** SCE-UA 逐代收敛曲线；x = 代数，y = 目标函数 F（越小越好）。 */
export function convergenceOption({ curve = [], colors = {}, activeCode = '' } = {}) {
  const series = (curve || []).map((c) => {
    const col = colors[c.code] || C_SIM
    const dim = activeCode && c.code !== activeCode
    return {
      name: c.code,
      type: 'line',
      showSymbol: false,
      symbol: 'none',
      data: (c.points || []).map((p) => [p.gen, p.best_f]),
      lineStyle: { width: dim ? 1 : 2, color: col, opacity: dim ? 0.35 : 1 },
      itemStyle: { color: col },
      emphasis: { focus: 'series' },
      z: dim ? 2 : 3
    }
  })
  return {
    animation: false,
    grid: { left: 62, right: 16, top: 28, bottom: 30 },
    tooltip: {
      ...TIP,
      valueFormatter: (v) => (v == null || !Number.isFinite(Number(v)) ? '—' : Number(v).toFixed(5))
    },
    legend: LEGEND,
    xAxis: {
      ...Y_AXIS,
      type: 'value',
      name: '代',
      minInterval: 1,
      scale: true,
      splitLine: { show: false },
      nameTextStyle: { color: C_T3, fontSize: 10, align: 'right' }
    },
    yAxis: {
      ...Y_AXIS,
      name: '目标函数 F',
      scale: true,
      nameTextStyle: { color: C_T3, fontSize: 10, align: 'right' }
    },
    series: series.length
      ? series
      : [
          {
            name: '等待第一代…',
            type: 'line',
            data: [],
            showSymbol: false,
            lineStyle: { width: 1.6, color: C_SIM }
          }
        ]
  }
}
