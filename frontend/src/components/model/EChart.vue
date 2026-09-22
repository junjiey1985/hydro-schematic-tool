<template>
  <div ref="el" class="echart" :style="{ height: height + 'px' }"></div>
</template>

<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { init } from '../../echarts'

const props = defineProps({
  option: { type: Object, default: () => ({}) },
  height: { type: Number, default: 220 }
})

const el = ref(null)
const inst = shallowRef(null)
let ro = null

function render() {
  if (!inst.value) return
  inst.value.setOption(props.option || {}, true)
}

function resize() {
  if (inst.value) inst.value.resize()
}

onMounted(async () => {
  inst.value = init(el.value, null, { renderer: 'canvas' })
  render()
  // 弹窗刚插入时容器可能还是零尺寸，延后一帧再适配一次
  await nextTick()
  resize()
  if (typeof ResizeObserver !== 'undefined') {
    ro = new ResizeObserver(() => resize())
    ro.observe(el.value)
  }
})

watch(() => props.option, render)

onBeforeUnmount(() => {
  if (ro) {
    ro.disconnect()
    ro = null
  }
  if (inst.value) inst.value.dispose()
  inst.value = null
})
</script>

<style scoped>
.echart {
  width: 100%;
}
</style>
