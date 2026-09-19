<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { api } from '../api'

/**
 * 模型名输入 + 「获取列表」下拉。
 *
 * 列表在服务端拉取（多数提供商的 /models 不带 CORS 头，浏览器直连会被挡），
 * 这里只负责交互与缓存。
 */
const model = defineModel({ type: String, default: '' })
const props = defineProps({
  baseUrl: { type: String, default: '' },
  apiKey: { type: String, default: '' },
  role: { type: String, default: 'text' },
  placeholder: { type: String, default: '' },
})

// 模块级缓存：同一个 base_url 不必反复拉（弹窗里有 4 个实例，切换作用域/角色时复用）
const cache = new Map()

const rootEl = ref(null)
const open = ref(false)
const loading = ref(false)
const error = ref('')
const models = ref([])
const filter = ref('')

/** 粗略判断某个模型名看起来能不能收图——只在视觉角色下作提示，不做过滤。 */
function looksVision(name) {
  return /(^|[-_.])(vl|vision|omni|multimodal|llava)([-_.]|$)|gpt-4o|gpt-4\.1|gemini|claude-3|claude-4|qwen.*vl|internvl/i
    .test(String(name))
}

const shown = computed(() => {
  const q = filter.value.trim().toLowerCase()
  const list = q ? models.value.filter((m) => m.toLowerCase().includes(q)) : models.value
  return list.slice(0, 300)
})

async function load(force = false) {
  const baseUrl = (props.baseUrl || '').trim()
  if (!baseUrl) {
    error.value = '请先填写接口地址'
    open.value = true
    return
  }
  const key = baseUrl.replace(/\/+$/, '')
  // 缓存只用于"聚焦时直接展开"；点按钮一律真去拉一次——
  // 同一个地址换了 key 之后可见的模型可能不同，缓存不该挡住刷新。
  if (!force && cache.has(key)) {
    models.value = cache.get(key)
    filter.value = ''
    error.value = ''
    open.value = true
    return
  }
  loading.value = true
  error.value = ''
  open.value = true
  try {
    const data = await api.listModels({
      base_url: baseUrl,
      // 脱敏回显值交给服务端判断：它会改用已存的那个 key
      api_key: props.apiKey || '',
      role: props.role,
    })
    models.value = data.models || []
    cache.set(key, models.value)
    filter.value = ''
  } catch (e) {
    error.value = e?.detail || e?.message || '获取失败'
    // 拉失败时不要把旧结果留在缓存里，否则下次点开还是过期列表
    cache.delete(key)
    models.value = []
  } finally {
    loading.value = false
  }
}

function toggle() {
  if (loading.value) return
  if (open.value) {
    open.value = false
    return
  }
  load(true)
}

function onFocus() {
  if (models.value.length) {
    open.value = true
    return
  }
  const key = (props.baseUrl || '').trim().replace(/\/+$/, '')
  if (key && cache.has(key)) {
    models.value = cache.get(key)
    open.value = true
  }
}

function pick(name) {
  model.value = name
  open.value = false
}

function onDocClick(e) {
  if (open.value && rootEl.value && !rootEl.value.contains(e.target)) open.value = false
}

onMounted(() => document.addEventListener('click', onDocClick))
onUnmounted(() => document.removeEventListener('click', onDocClick))
</script>

<template>
  <div ref="rootEl" class="mp">
    <div class="mp-row">
      <input
        v-model="model"
        :placeholder="placeholder"
        @focus="onFocus"
        @keydown.esc="open = false"
      />
      <button
        type="button"
        class="mp-btn"
        :class="{ active: open }"
        :disabled="loading"
        :title="models.length ? '重新获取模型列表' : '从提供商获取可用模型列表'"
        @click="toggle"
      >{{ loading ? '获取中…' : (models.length ? '重取' : '获取列表') }}</button>
    </div>

    <div v-if="open" class="mp-panel">
      <p v-if="loading" class="mp-status">正在从 {{ baseUrl || '提供商' }} 获取…</p>
      <p v-else-if="error" class="mp-status mp-error">{{ error }}</p>
      <template v-else>
        <input v-model="filter" class="mp-filter" placeholder="输入关键字筛选…" />
        <ul class="mp-list">
          <li v-for="m in shown" :key="m" :class="{ current: m === model }" @click="pick(m)">
            <span class="mp-name">{{ m }}</span>
            <span v-if="role === 'vision' && looksVision(m)" class="mp-badge">可用视觉</span>
          </li>
          <li v-if="!shown.length" class="mp-empty">没有匹配的模型</li>
        </ul>
        <p class="mp-count">共 {{ models.length }} 个模型</p>
      </template>
    </div>
  </div>
</template>

<style scoped>
.mp { position: relative; }
.mp-row { display: flex; gap: 6px; }
.mp-row input {
  flex: 1;
  min-width: 0;
  padding: 9px 12px;
  border: 1px solid var(--dsh-border-strong);
  border-radius: var(--dsh-r-sm);
  font-size: var(--dsh-fs-md);
  font-family: inherit;
  outline: none;
  color: var(--dsh-text);
  background: var(--dsh-surface);
  transition: border-color var(--dsh-dur) var(--dsh-ease), box-shadow var(--dsh-dur) var(--dsh-ease);
}
.mp-row input:focus { border-color: var(--dsh-brand); box-shadow: var(--dsh-ring); }
.mp-btn {
  flex-shrink: 0;
  padding: 0 12px;
  border: 1px solid var(--dsh-border-strong);
  border-radius: var(--dsh-r-sm);
  background: var(--dsh-surface);
  color: var(--dsh-text-2);
  font-size: var(--dsh-fs-base);
  font-family: inherit;
  cursor: pointer;
  white-space: nowrap;
  transition: all var(--dsh-dur) var(--dsh-ease);
}
.mp-btn:hover:not(:disabled) { border-color: var(--dsh-brand); color: var(--dsh-brand); background: var(--dsh-brand-soft); }
.mp-btn.active { border-color: var(--dsh-brand); color: var(--dsh-brand); background: var(--dsh-brand-soft); }
.mp-btn:disabled { opacity: 0.6; cursor: default; }

.mp-panel {
  position: absolute;
  z-index: 10;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  background: var(--dsh-surface);
  border: 1px solid var(--dsh-border-strong);
  border-radius: var(--dsh-r-md);
  box-shadow: var(--dsh-shadow-lg);
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.mp-status { font-size: var(--dsh-fs-base); color: var(--dsh-text-3); padding: 6px 4px; }
.mp-error { color: var(--dsh-danger-strong); }
.mp-filter {
  padding: 6px 9px;
  border: 1px solid var(--dsh-border);
  border-radius: var(--dsh-r-xs);
  font-size: var(--dsh-fs-base);
  font-family: inherit;
  outline: none;
  color: var(--dsh-text);
}
.mp-filter:focus { border-color: var(--dsh-brand); }
/* 模型多了要能滚，别把弹窗撑爆 */
.mp-list { list-style: none; margin: 0; padding: 0; max-height: 210px; overflow-y: auto; }
.mp-list li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: var(--dsh-r-xs);
  cursor: pointer;
  font-size: var(--dsh-fs-base);
  color: var(--dsh-text-2);
}
.mp-list li:hover { background: var(--dsh-brand-soft); color: var(--dsh-brand); }
.mp-list li.current { background: var(--dsh-surface-3); color: var(--dsh-text); font-weight: 500; }
.mp-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: Consolas, 'Courier New', monospace; }
.mp-badge {
  flex-shrink: 0;
  font-size: 10px;
  line-height: 15px;
  padding: 0 6px;
  border-radius: var(--dsh-r-pill);
  color: var(--dsh-success);
  background: var(--dsh-success-soft);
  border: 1px solid var(--dsh-success-line);
}
.mp-empty { justify-content: center; color: var(--dsh-text-4); cursor: default; }
.mp-empty:hover { background: none; color: var(--dsh-text-4); }
.mp-count { font-size: var(--dsh-fs-xs); color: var(--dsh-text-4); padding: 0 4px; }
</style>
