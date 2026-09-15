<script setup>
import { ref, nextTick } from 'vue'

const props = defineProps({
  conversations: { type: Array, default: () => [] },
  currentId: { type: String, default: '' },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  sending: { type: Boolean, default: false },
})

const emit = defineEmits(['select', 'create', 'rename', 'remove'])

const editingId = ref('')
const draftTitle = ref('')
const titleInput = ref(null)

async function startRename(conv) {
  editingId.value = conv.id
  draftTitle.value = conv.title
  await nextTick()
  titleInput.value?.[0]?.focus?.()
  titleInput.value?.[0]?.select?.()
}

function commitRename(conv) {
  const title = draftTitle.value.trim()
  editingId.value = ''
  if (title && title !== conv.title) emit('rename', conv.id, title)
}

/** 时间是列表里唯一能帮用户定位"上次那个对话"的信息，所以用相对时间。 */
function relTime(ts) {
  if (!ts) return ''
  const diff = Date.now() / 1000 - ts
  if (diff < 60) return '刚刚'
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  if (diff < 86400 * 30) return `${Math.floor(diff / 86400)} 天前`
  const d = new Date(ts * 1000)
  return `${d.getMonth() + 1}月${d.getDate()}日`
}

function onRemove(conv) {
  if (window.confirm(`删除对话「${conv.title}」？该对话的记录将不可恢复。`)) {
    emit('remove', conv.id)
  }
}
</script>

<template>
  <aside class="conv-panel">
    <div class="conv-head">
      <span class="conv-title">对话</span>
      <span v-if="loading" class="conv-loading" aria-hidden="true" />
      <button class="conv-new" type="button" title="新建对话" @click="emit('create')">＋ 新建</button>
    </div>

    <p v-if="error" class="conv-error">{{ error }}</p>

    <div v-if="!conversations.length" class="conv-empty">
      <p>还没有对话</p>
      <p class="conv-empty-hint">点「＋ 新建」开始，或在右侧直接提问</p>
    </div>

    <ul v-else class="conv-list">
      <li
        v-for="c in conversations"
        :key="c.id"
        class="conv-item"
        :class="{ active: c.id === currentId }"
        @click="c.id !== currentId && emit('select', c.id)"
      >
        <div class="conv-item-main">
          <input
            v-if="editingId === c.id"
            ref="titleInput"
            v-model="draftTitle"
            class="conv-rename"
            @click.stop
            @keydown.enter.stop="commitRename(c)"
            @keydown.esc.stop="editingId = ''"
            @blur="commitRename(c)"
          />
          <span v-else class="conv-name" :title="c.title" @dblclick.stop="startRename(c)">{{ c.title }}</span>
          <span class="conv-meta">
            <span>{{ relTime(c.updated_at) }}</span>
            <span v-if="c.message_count" class="conv-count">{{ c.message_count }} 条</span>
            <!-- 本对话单独指定了模型：列表里一眼能看出来，否则用户会忘了为什么回答风格不一样 -->
            <span v-if="c.has_llm_override" class="conv-model" :title="`本对话模型：${c.llm_model || '已覆盖'}`">
              {{ c.llm_model || '自定义模型' }}
            </span>
          </span>
        </div>
        <button
          class="conv-del"
          type="button"
          title="删除对话"
          aria-label="删除对话"
          @click.stop="onRemove(c)"
        >✕</button>
      </li>
    </ul>

    <p v-if="sending" class="conv-hint">正在生成回答…切换对话会中断当前任务</p>
  </aside>
</template>

<style scoped>
.conv-panel {
  width: 240px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
  background: var(--dsh-surface-2);
  border-right: 1px solid var(--dsh-border);
  transition: margin-left 0.25s var(--dsh-ease);
}
/* 折叠：整体左移出视口（宽度保持不变，避免内部布局在动画中反复重排） */
.conv-panel.collapsed { margin-left: -240px; }

.conv-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px 12px 10px;
  flex-shrink: 0;
}
.conv-title {
  font-size: var(--dsh-fs-base);
  font-weight: 600;
  color: var(--dsh-text-2);
  letter-spacing: 0.02em;
}
.conv-loading {
  width: 10px;
  height: 10px;
  border: 2px solid var(--dsh-border);
  border-top-color: var(--dsh-brand);
  border-radius: 50%;
  animation: conv-spin 0.7s linear infinite;
}
@keyframes conv-spin { to { transform: rotate(360deg); } }
.conv-new {
  margin-left: auto;
  background: var(--dsh-surface);
  border: 1px solid var(--dsh-border);
  border-radius: var(--dsh-r-pill);
  color: var(--dsh-text-2);
  font-size: var(--dsh-fs-sm);
  font-family: inherit;
  padding: 3px 10px;
  cursor: pointer;
  white-space: nowrap;
  transition: all var(--dsh-dur) var(--dsh-ease);
}
.conv-new:hover { color: var(--dsh-brand); border-color: var(--dsh-brand-line); background: var(--dsh-brand-soft); }

.conv-error {
  margin: 0 12px 8px;
  padding: 7px 10px;
  font-size: var(--dsh-fs-sm);
  color: var(--dsh-danger-strong);
  background: var(--dsh-danger-soft);
  border: 1px solid var(--dsh-danger-line);
  border-radius: var(--dsh-r-sm);
}

.conv-empty {
  padding: 26px 16px;
  text-align: center;
  color: var(--dsh-text-4);
  font-size: var(--dsh-fs-base);
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.conv-empty-hint { font-size: var(--dsh-fs-sm); line-height: 1.5; }

.conv-list {
  list-style: none;
  margin: 0;
  padding: 0 8px 12px;
  overflow-y: auto;
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.conv-list::-webkit-scrollbar { width: 6px; }
.conv-list::-webkit-scrollbar-thumb {
  background: var(--dsh-scroll-thumb);
  border-radius: var(--dsh-r-pill);
  border: 2px solid transparent;
  background-clip: content-box;
}

.conv-item {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 7px 6px 7px 10px;
  border-radius: var(--dsh-r-sm);
  cursor: pointer;
  border: 1px solid transparent;
  transition: background var(--dsh-dur) var(--dsh-ease), border-color var(--dsh-dur) var(--dsh-ease);
}
.conv-item:hover { background: var(--dsh-surface); }
.conv-item.active {
  background: var(--dsh-brand-soft);
  border-color: var(--dsh-brand-line);
}
.conv-item-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.conv-name {
  font-size: var(--dsh-fs-base);
  color: var(--dsh-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.conv-item.active .conv-name { color: var(--dsh-brand); font-weight: 500; }
.conv-rename {
  width: 100%;
  font-size: var(--dsh-fs-base);
  font-family: inherit;
  padding: 2px 6px;
  border: 1px solid var(--dsh-brand);
  border-radius: var(--dsh-r-xs);
  outline: none;
  color: var(--dsh-text);
  background: var(--dsh-surface);
}
.conv-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--dsh-fs-xs);
  color: var(--dsh-text-4);
  overflow: hidden;
  white-space: nowrap;
}
.conv-model {
  color: var(--dsh-warn);
  background: var(--dsh-warn-soft);
  border: 1px solid var(--dsh-warn-line);
  border-radius: var(--dsh-r-pill);
  padding: 0 6px;
  max-width: 96px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.conv-del {
  flex-shrink: 0;
  background: none;
  border: none;
  color: var(--dsh-text-4);
  cursor: pointer;
  font-size: 11px;
  line-height: 1;
  padding: 4px 5px;
  border-radius: var(--dsh-r-xs);
  opacity: 0;
  transition: opacity var(--dsh-dur) var(--dsh-ease), color var(--dsh-dur) var(--dsh-ease);
}
.conv-item:hover .conv-del,
.conv-item.active .conv-del { opacity: 1; }
.conv-del:hover { color: var(--dsh-danger); background: var(--dsh-danger-soft); }
/* 触屏没有 hover：删除按钮必须常驻，否则永远点不到 */
@media (hover: none) {
  .conv-del { opacity: 1; }
}

.conv-hint {
  flex-shrink: 0;
  margin: 0 12px 12px;
  font-size: var(--dsh-fs-xs);
  color: var(--dsh-warn);
  line-height: 1.5;
}
</style>
