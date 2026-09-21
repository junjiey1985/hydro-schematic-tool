<template>
  <div
    ref="root"
    class="fp"
    :class="[{ collapsed, active }, tone]"
    :style="{ left: pos.x + 'px', top: pos.y + 'px', width: collapsed ? 'auto' : width + 'px' }"
    @pointerdown.stop
    @wheel.stop
  >
    <div class="fp-head" @pointerdown="startDrag" :title="collapsed ? '点此展开' : '按住可拖动'">
      <span class="fp-ico">
        <slot name="icon" />
      </span>
      <span class="fp-ttl">{{ title }}</span>
      <span v-if="collapsed && badge" class="fp-pill">{{ badge }}</span>
      <span class="fp-grow"></span>
      <span v-if="!collapsed" class="fp-grip">⠿</span>
      <button class="fp-mini" @click.stop="collapsed = !collapsed" :title="collapsed ? '展开' : '收起'">
        {{ collapsed ? '▸' : '▾' }}
      </button>
      <button v-if="closable" class="fp-mini fp-close" @click.stop="$emit('close')" title="关闭">
        ✕
      </button>
    </div>
    <div v-show="!collapsed" class="fp-body">
      <slot />
    </div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'

const props = defineProps({
  title: { type: String, default: '' },
  width: { type: Number, default: 216 },
  initialX: { type: Number, default: 14 },
  initialY: { type: Number, default: 14 },
  active: { type: Boolean, default: false },
  tone: { type: String, default: 'blue' }, // blue | amber
  badge: { type: String, default: '' },
  closable: { type: Boolean, default: false }
})

defineEmits(['close'])

const root = ref(null)
const collapsed = ref(false)
const pos = reactive({ x: props.initialX, y: props.initialY })

let drag = null

function startDrag(e) {
  if (e.button !== 0 || !root.value) return
  const r = root.value.getBoundingClientRect()
  const wrap = wrapRectOf()
  drag = { dx: e.clientX - r.left, dy: e.clientY - r.top, wrap }
  window.addEventListener('pointermove', onDragMove)
  window.addEventListener('pointerup', endDrag)
  e.preventDefault()
}

function onDragMove(e) {
  if (!drag || !root.value) return
  const wrap = wrapRectOf()
  let x = e.clientX - drag.dx - drag.wrap.left
  let y = e.clientY - drag.dy - drag.wrap.top
  x = Math.min(Math.max(8, x), Math.max(8, wrap.width - root.value.offsetWidth - 8))
  y = Math.min(Math.max(8, y), Math.max(8, wrap.height - root.value.offsetHeight - 8))
  pos.x = Math.round(x)
  pos.y = Math.round(y)
}

function endDrag() {
  drag = null
  window.removeEventListener('pointermove', onDragMove)
  window.removeEventListener('pointerup', endDrag)
}

function wrapRectOf() {
  const wrap = root.value && root.value.parentElement
  const r = wrap ? wrap.getBoundingClientRect() : { left: 0, top: 0, width: 0, height: 0 }
  return { left: r.left, top: r.top, width: r.width, height: r.height }
}

onBeforeUnmount(() => {
  window.removeEventListener('pointermove', onDragMove)
  window.removeEventListener('pointerup', endDrag)
})
</script>

<style scoped>
.fp {
  position: absolute;
  z-index: 8;
  background: rgba(255, 255, 255, 0.96);
  border: 1px solid var(--line-strong);
  border-radius: 10px;
  box-shadow: var(--shadow);
  backdrop-filter: blur(3px);
  overflow: hidden;
  color: var(--text);
}
.fp.active.blue {
  border-color: var(--primary);
  box-shadow: 0 0 0 2px rgba(30, 111, 168, 0.14), var(--shadow);
}
.fp.active.amber {
  border-color: #d8b96a;
  box-shadow: 0 0 0 2px rgba(183, 121, 31, 0.14), var(--shadow);
}

.fp-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 7px 6px 9px;
  background: var(--panel-2);
  border-bottom: 1px solid var(--line);
  cursor: move;
  user-select: none;
  touch-action: none;
}
.fp.collapsed .fp-head { border-bottom: none; }
.fp-ico { display: inline-flex; color: var(--text-2); }
.fp.active .fp-ico { color: var(--primary); }
.fp-ttl {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-2);
  letter-spacing: 0.4px;
  white-space: nowrap;
}
.fp-grow { flex: 1; }
.fp-grip { color: var(--text-3); font-size: 11px; }
.fp-pill {
  padding: 1px 7px;
  border-radius: 9px;
  background: var(--primary);
  color: #fff;
  font-size: 11px;
  white-space: nowrap;
}
.fp-mini {
  border: none;
  background: transparent;
  color: var(--text-3);
  font-size: 11px;
  line-height: 1;
  padding: 2px 3px;
  border-radius: 4px;
}
.fp-mini:hover { background: #fff; color: var(--primary); }
.fp-close:hover { background: #fdeaea; color: var(--accent); }

.fp-body {
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 7px;
}
</style>
