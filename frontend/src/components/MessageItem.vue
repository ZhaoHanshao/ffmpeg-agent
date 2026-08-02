<script setup>
import { computed } from 'vue'
import { marked } from 'marked'
import { API_BASE } from '../api'
import { isImage, isVideo, sanitizeHtml } from '../utils'

const props = defineProps({
  msg: { type: Object, required: true },
})

// computed 缓存 markdown 渲染结果：流式期间只有当前消息的 MessageItem 会重渲染，
// 且同一文本不会重复解析
const rendered = computed(() => sanitizeHtml(marked.parse(props.msg.text || '')))

// v-html 内容里的代码块复制按钮，通过事件委托绑定
function onMsgClick(e) {
  const btn = e.target.closest('.code-copy')
  if (!btn) return
  const block = btn.closest('.code-block')
  const code = block?.querySelector('pre code')
  if (!code) return
  const text = code.textContent.replace(/\n$/, '')
  navigator.clipboard.writeText(text).then(() => {
    btn.textContent = '✓ 已复制'
    setTimeout(() => (btn.textContent = '⧉ 复制'), 1500)
  }).catch(() => {
    // 剪贴板不可用时静默忽略
  })
}
</script>

<template>
  <div class="msg-row" :class="msg.role">
    <div class="avatar">{{ msg.role === 'user' ? '👤' : msg.role === 'ai' ? '🤖' : '⚙️' }}</div>
    <div class="bubble">
      <div class="msg-text" v-html="rendered" @click="onMsgClick" />
      <div v-if="msg.outputFile" class="output-area">
        <img
          v-if="isImage(msg.outputFile)"
          :src="`${API_BASE}/output/${encodeURIComponent(msg.outputFile)}`"
          class="preview-img"
          loading="lazy"
        />
        <video
          v-else-if="isVideo(msg.outputFile)"
          :src="`${API_BASE}/output/${encodeURIComponent(msg.outputFile)}`"
          class="preview-video"
          controls
        />
        <a
          :href="`${API_BASE}/output/${encodeURIComponent(msg.outputFile)}`"
          class="download-link"
          download
        >⬇️ 下载 {{ msg.outputFile }}</a>
      </div>
    </div>
  </div>
</template>

<style scoped>
.msg-row { display: flex; gap: 10px; align-items: flex-start; }
.msg-row.user { flex-direction: row-reverse; }
.msg-row.system { justify-content: center; }
.msg-row.system .bubble {
  background: #f3f4f6;
  color: #6b7280;
  font-size: 13px;
  padding: 6px 14px;
  text-align: center;
}

.avatar {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
  background: #f3f4f6;
}

.bubble {
  max-width: 70%;
  padding: 10px 14px;
  border-radius: 14px;
  font-size: 14px;
  line-height: 1.6;
  word-break: break-word;
}
.user .bubble { background: #4f6ef7; color: #fff; border-bottom-right-radius: 4px; }
.ai .bubble { background: #fff; border: 1px solid #e5e7eb; border-bottom-left-radius: 4px; }

.msg-text { white-space: normal; }
.msg-text :deep(p) { margin: 0 0 8px; }
.msg-text :deep(p:last-child) { margin-bottom: 0; }
.msg-text :deep(h1), .msg-text :deep(h2), .msg-text :deep(h3), .msg-text :deep(h4) {
  font-size: 15px;
  margin: 12px 0 6px;
}
.msg-text :deep(ul), .msg-text :deep(ol) { margin: 4px 0 8px; padding-left: 20px; }
.msg-text :deep(code) {
  background: #f3f4f6;
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 12.5px;
  font-family: Consolas, 'Courier New', monospace;
}
.msg-text :deep(img) {
  max-width: 100%;
  height: auto;
  object-fit: contain;
  border-radius: 8px;
}
.msg-text :deep(.code-block) { position: relative; margin: 8px 0; }
.msg-text :deep(.code-copy) {
  position: absolute;
  top: 6px;
  right: 6px;
  z-index: 1;
  background: rgba(255, 255, 255, 0.1);
  color: #cbd5e1;
  border: 1px solid rgba(255, 255, 255, 0.25);
  border-radius: 5px;
  font-size: 11px;
  padding: 2px 8px;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s;
}
.msg-text :deep(.code-block:hover .code-copy) { opacity: 1; }
.msg-text :deep(.code-copy:hover) { background: rgba(255, 255, 255, 0.2); color: #fff; }
.msg-text :deep(pre) {
  background: #0f172a;
  color: #e2e8f0;
  padding: 32px 12px 10px;
  border-radius: 8px;
  overflow-x: auto;
  margin: 8px 0;
}
.msg-text :deep(pre code) { background: none; padding: 0; color: inherit; }
.msg-text :deep(blockquote) {
  border-left: 3px solid #d1d5db;
  padding-left: 10px;
  color: #6b7280;
  margin: 8px 0;
}
.msg-text :deep(table) {
  border-collapse: collapse;
  display: block;
  overflow-x: auto;
  margin: 8px 0;
  font-size: 13px;
}
.msg-text :deep(th), .msg-text :deep(td) { border: 1px solid #e5e7eb; padding: 4px 10px; }
.msg-text :deep(hr) { border: none; border-top: 1px solid #e5e7eb; margin: 10px 0; }
.user .msg-text :deep(code) { background: rgba(255, 255, 255, 0.2); }
.user .msg-text :deep(a) { color: #c7d2fe; }

.output-area { margin-top: 8px; display: flex; flex-direction: column; gap: 6px; }
.preview-img {
  max-width: 100%;
  height: auto;
  object-fit: contain;
  border-radius: 8px;
  border: 1px solid #e5e7eb;
}
.preview-video { width: 100%; max-height: 320px; border-radius: 8px; }
.download-link { font-size: 13px; }
</style>
