<script setup>
import { computed, ref, watch } from 'vue'
import { marked } from 'marked'
import { API_BASE } from '../api'
import { isImage, isVideo, sanitizeHtml, fileKind } from '../utils'

const props = defineProps({
  msg: { type: Object, required: true },
})

// computed 缓存 markdown 渲染结果：流式期间只有当前消息的 MessageItem 会重渲染，
// 且同一文本不会重复解析
const rendered = computed(() => sanitizeHtml(marked.parse(props.msg.text || '')))

// 输出文件可能已被用户删除（或后端清理过），此时 <img> 会渲染成破图。
// 记录加载失败，改为只显示下载链接，避免出现破图占位。
const previewFailed = ref(false)
watch(
  () => props.msg.outputFile,
  () => {
    previewFailed.value = false
  }
)

function onPreviewError() {
  previewFailed.value = true
}

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
      <div v-if="msg.files?.length" class="msg-files">
        <span v-for="f in msg.files" :key="`${f.src}-${f.name}`" class="msg-file-chip">
          <span class="msg-file-icon">{{ f.src === 'output' ? '🎯' : fileKind(f.name).icon }}</span>
          <span class="msg-file-name">{{ f.name }}</span>
          <span v-if="f.src === 'output'" class="msg-file-tag">输出</span>
        </span>
      </div>
      <div v-if="msg.stage" class="msg-stage">
        <span class="stage-spinner" aria-hidden="true" />
        <span class="stage-text">{{ msg.stage }}</span>
      </div>
      <div v-if="msg.text" class="msg-text" v-html="rendered" @click="onMsgClick" />
      <div v-if="msg.outputFile" class="output-area">
        <img
          v-if="isImage(msg.outputFile) && !previewFailed"
          :src="`${API_BASE}/output/${encodeURIComponent(msg.outputFile)}`"
          class="preview-img"
          loading="lazy"
          alt="输出预览"
          @error="onPreviewError"
        />
        <video
          v-else-if="isVideo(msg.outputFile) && !previewFailed"
          :src="`${API_BASE}/output/${encodeURIComponent(msg.outputFile)}`"
          class="preview-video"
          controls
          @error="onPreviewError"
        />
        <span v-if="previewFailed" class="preview-missing">预览不可用（文件可能已被删除）</span>
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
  background: var(--dsh-surface-3);
  color: var(--dsh-text-3);
  font-size: var(--dsh-fs-base);
  padding: 6px 14px;
  text-align: center;
  border: none;
  border-radius: var(--dsh-r-pill);
}

.avatar {
  width: 30px;
  height: 30px;
  border-radius: var(--dsh-r-sm);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 15px;
  flex-shrink: 0;
  background: var(--dsh-surface);
  border: 1px solid var(--dsh-border);
}
.msg-row.ai .avatar { background: var(--dsh-brand-soft); border-color: var(--dsh-brand-line); }

.bubble {
  max-width: 74%;
  padding: 10px 14px;
  border-radius: var(--dsh-r-lg);
  font-size: var(--dsh-fs-md);
  line-height: 1.65;
  word-break: break-word;
}
.user .bubble {
  background: var(--dsh-brand);
  color: var(--dsh-text-invert);
  border-bottom-right-radius: var(--dsh-r-xs);
  box-shadow: var(--dsh-shadow-brand);
}
.ai .bubble {
  background: var(--dsh-surface);
  border: 1px solid var(--dsh-border);
  border-bottom-left-radius: var(--dsh-r-xs);
  box-shadow: var(--dsh-shadow-xs);
}

.msg-text { white-space: normal; }
/* 阶段提示（"正在理解素材画面…"）：与回答正文分开显示，不污染最终内容 */
.msg-stage {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--dsh-fs-base);
  color: var(--dsh-text-3);
  padding: 2px 0;
}
.stage-spinner {
  width: 12px;
  height: 12px;
  border: 2px solid var(--dsh-border);
  border-top-color: var(--dsh-brand);
  border-radius: 50%;
  animation: msg-spin 0.7s linear infinite;
  flex-shrink: 0;
}
@keyframes msg-spin { to { transform: rotate(360deg); } }
.stage-text { line-height: 1.4; }.msg-text :deep(p) { margin: 0 0 8px; }
.msg-text :deep(p:last-child) { margin-bottom: 0; }
.msg-text :deep(h1), .msg-text :deep(h2), .msg-text :deep(h3), .msg-text :deep(h4) {
  font-size: var(--dsh-fs-lg);
  margin: 12px 0 6px;
}
.msg-text :deep(ul), .msg-text :deep(ol) { margin: 4px 0 8px; padding-left: 20px; }
.msg-text :deep(code) {
  background: var(--dsh-surface-3);
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 12.5px;
  font-family: Consolas, 'Courier New', monospace;
}
.msg-text :deep(img) {
  max-width: 100%;
  height: auto;
  object-fit: contain;
  border-radius: var(--dsh-r-sm);
}
.msg-text :deep(.code-block) { position: relative; margin: 8px 0; }
.msg-text :deep(.code-copy) {
  position: absolute;
  top: 6px;
  right: 6px;
  z-index: 1;
  background: rgba(255, 255, 255, 0.12);
  color: #cbd5e1;
  border: 1px solid rgba(255, 255, 255, 0.25);
  border-radius: var(--dsh-r-xs);
  font-size: var(--dsh-fs-xs);
  padding: 2px 8px;
  cursor: pointer;
  opacity: 0;
  transition: opacity var(--dsh-dur) var(--dsh-ease);
}
.msg-text :deep(.code-block:hover .code-copy) { opacity: 1; }
.msg-text :deep(.code-copy:hover) { background: rgba(255, 255, 255, 0.2); color: #fff; }
.msg-text :deep(pre) {
  background: #0f172a;
  color: #e2e8f0;
  padding: 32px 12px 11px;
  border-radius: var(--dsh-r-sm);
  overflow-x: auto;
  margin: 8px 0;
  font-size: 12.5px;
}
.msg-text :deep(pre code) { background: none; padding: 0; color: inherit; }
.msg-text :deep(blockquote) {
  border-left: 3px solid var(--dsh-border-strong);
  padding-left: 10px;
  color: var(--dsh-text-3);
  margin: 8px 0;
}
.msg-text :deep(table) {
  border-collapse: collapse;
  display: block;
  overflow-x: auto;
  margin: 8px 0;
  font-size: var(--dsh-fs-base);
}
.msg-text :deep(th), .msg-text :deep(td) { border: 1px solid var(--dsh-border); padding: 4px 10px; }
.msg-text :deep(th) { background: var(--dsh-surface-2); }
.msg-text :deep(hr) { border: none; border-top: 1px solid var(--dsh-border); margin: 10px 0; }
.user .msg-text :deep(code) { background: rgba(255, 255, 255, 0.2); }
.user .msg-text :deep(a) { color: #dbe2ff; }
/* 用户气泡是蓝底，继承全局的链接色会看不清；这里统一为浅色并保留可辨识的下划线 */
.user .msg-text :deep(a:hover) { color: #fff; }
.user .msg-text :deep(a:not([class])) { text-decoration: underline; text-underline-offset: 2px; }

.msg-files {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}
.msg-file-chip {
  display: flex;
  align-items: center;
  gap: 4px;
  background: var(--dsh-surface-2);
  border: 1px solid var(--dsh-border);
  border-radius: var(--dsh-r-pill);
  padding: 2px 8px;
  font-size: var(--dsh-fs-sm);
  color: var(--dsh-text-2);
  max-width: 100%;
}
.user .msg-file-chip {
  background: rgba(255, 255, 255, 0.18);
  border-color: rgba(255, 255, 255, 0.32);
  color: var(--dsh-text-invert);
}
.msg-file-icon { font-size: 12px; flex-shrink: 0; }
.msg-file-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 200px;
}
.msg-file-tag {
  font-size: 10px;
  background: var(--dsh-warn-soft);
  color: var(--dsh-warn);
  border-radius: var(--dsh-r-pill);
  padding: 0 6px;
  line-height: 15px;
  flex-shrink: 0;
}
.user .msg-file-tag { background: rgba(255, 255, 255, 0.25); color: #fff; }

.output-area { margin-top: 10px; display: flex; flex-direction: column; gap: 7px; }
.preview-img {
  max-width: 100%;
  height: auto;
  object-fit: contain;
  border-radius: var(--dsh-r-sm);
  border: 1px solid var(--dsh-border);
}
.preview-video { width: 100%; max-height: 320px; border-radius: var(--dsh-r-sm); background: #000; }
.preview-missing {
  font-size: var(--dsh-fs-sm);
  color: var(--dsh-text-4);
  background: var(--dsh-surface-2);
  border: 1px dashed var(--dsh-border-strong);
  border-radius: var(--dsh-r-sm);
  padding: 8px 12px;
}
.download-link {
  font-size: var(--dsh-fs-base);
  font-weight: 500;
  align-self: flex-start;
}
</style>
