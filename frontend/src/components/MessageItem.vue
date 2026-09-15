<script setup>
import { computed, ref, watch, onUnmounted } from 'vue'
import { marked } from 'marked'
import { API_BASE, authHeaders, getToken } from '../api'
import { isImage, isVideo, sanitizeHtml, fileKind } from '../utils'

const props = defineProps({
  msg: { type: Object, required: true },
})

// computed 缓存 markdown 渲染结果：流式期间只有当前消息的 MessageItem 会重渲染，
// 且同一文本不会重复解析
const rendered = computed(() => sanitizeHtml(marked.parse(props.msg.text || '')))

// 仅当本轮还没有任何可显示内容时显示"正在思考"：避免发出后出现一个空气泡
const showTyping = computed(
  () => props.msg.role === 'ai' && props.msg.pending && !props.msg.text && !props.msg.stage && !props.msg.error
)

// 阶段文案里带百分比时（"转码中 62%"）额外画一条进度条；解析不出来就退回纯文字 + 转圈
const stagePercent = computed(() => {
  const m = /(\d{1,3})\s*%/.exec(props.msg.stage || '')
  if (!m) return null
  const n = Number(m[1])
  return Number.isFinite(n) ? Math.min(100, Math.max(0, n)) : null
})

// ── 输出文件预览 ──
// 服务端配置 AUTH_TOKEN 时，<img>/<video>/<a download> 这类标签请求不会带自定义头，
// 预览和下载都会 401。所以启用鉴权时先用 fetch 取成 blob 再交给标签；未启用鉴权时
// 保持直链，视频才能走 range 流式播放（不必先整包下载）。
const previewSrc = ref('')
const previewFailed = ref(false)
let objectUrl = ''
const canPreview = computed(() => isImage(props.msg.outputFile) || isVideo(props.msg.outputFile))
const outputUrl = computed(() => `${API_BASE}/output/${encodeURIComponent(props.msg.outputFile || '')}`)
const downloadHref = computed(() => previewSrc.value || outputUrl.value)

function releaseObjectUrl() {
  if (objectUrl) {
    URL.revokeObjectURL(objectUrl)
    objectUrl = ''
  }
}

async function loadPreview() {
  const file = props.msg.outputFile
  releaseObjectUrl()
  previewFailed.value = false
  previewSrc.value = ''
  if (!file) return
  if (!getToken() || !canPreview.value) {
    previewSrc.value = `${API_BASE}/output/${encodeURIComponent(file)}`
    return
  }
  try {
    const res = await fetch(`${API_BASE}/output/${encodeURIComponent(file)}`, { headers: authHeaders() })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const blob = await res.blob()
    if (props.msg.outputFile !== file) return // 期间已切换到别的文件，丢弃这次结果
    objectUrl = URL.createObjectURL(blob)
    previewSrc.value = objectUrl
  } catch {
    if (props.msg.outputFile === file) previewFailed.value = true
  }
}

watch(() => props.msg.outputFile, loadPreview, { immediate: true })

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

// 整条回答的复制按钮（回答里常有好几段，逐个块复制很烦）。
// 太短的回答加按钮只是噪声，所以只有超过一行/30 字才显示。
const canCopyAnswer = computed(() => {
  if (props.msg.role !== 'ai' || props.msg.streaming) return false
  const t = props.msg.text || ''
  return t.includes('\n') || t.length > 30
})

const copied = ref(false)
let copiedTimer = null

function copyAnswer() {
  navigator.clipboard.writeText(props.msg.text || '').then(() => {
    copied.value = true
    clearTimeout(copiedTimer)
    copiedTimer = setTimeout(() => (copied.value = false), 1500)
  }).catch(() => {})
}

onUnmounted(() => {
  clearTimeout(copiedTimer)
  releaseObjectUrl()
})
</script>

<template>
  <div class="msg-row" :class="[msg.role, { streaming: msg.streaming }]" :aria-busy="msg.streaming ? 'true' : 'false'">
    <div class="avatar" aria-hidden="true">{{ msg.role === 'user' ? '👤' : msg.role === 'ai' ? '🤖' : '⚙️' }}</div>
    <div class="bubble">
      <div v-if="msg.files?.length" class="msg-files">
        <span v-for="f in msg.files" :key="`${f.src}-${f.name}`" class="msg-file-chip">
          <span class="msg-file-icon">{{ f.src === 'output' ? '🎯' : fileKind(f.name).icon }}</span>
          <span class="msg-file-name">{{ f.name }}</span>
          <span v-if="f.src === 'output'" class="msg-file-tag">输出</span>
        </span>
      </div>

      <!-- 等待首个事件（图谱还在跑、尚未推送 status）：三点动画 -->
      <div v-if="showTyping" class="msg-typing" role="status" aria-label="正在处理">
        <span class="typing-dot" />
        <span class="typing-dot" />
        <span class="typing-dot" />
      </div>

      <div v-if="msg.stage" class="msg-stage" role="status">
        <span class="stage-spinner" aria-hidden="true" />
        <div class="stage-body">
          <span class="stage-text">{{ msg.stage }}</span>
          <div v-if="stagePercent !== null" class="stage-bar" aria-hidden="true">
            <div class="stage-fill" :style="{ width: stagePercent + '%' }" />
          </div>
        </div>
      </div>

      <div v-if="msg.text" class="msg-text" v-html="rendered" @click="onMsgClick" />

      <!-- 错误独立成块：之前是把 "[错误] …" 拼进正文，混在 markdown 里很难被注意到 -->
      <div v-if="msg.error" class="msg-error" role="alert">
        <span class="error-icon" aria-hidden="true">⚠</span>
        <div class="error-text">{{ msg.error }}</div>
      </div>

      <div v-if="msg.outputFile" class="output-area">
        <div class="output-head">
          <span class="output-tag">输出</span>
          <span class="output-name" :title="msg.outputFile">{{ msg.outputFile }}</span>
          <a class="output-dl" :href="downloadHref" download>⬇ 下载</a>
        </div>
        <a
          v-if="isImage(msg.outputFile) && previewSrc && !previewFailed"
          class="preview-link"
          :href="previewSrc"
          target="_blank"
          rel="noreferrer"
          title="在新标签页打开原图"
        >
          <img
            :src="previewSrc"
            class="preview-img"
            loading="lazy"
            alt="输出预览"
            @error="onPreviewError"
          />
        </a>
        <!-- 视频不要套在 <a> 里：点击播放/进度条会被链接拦截 -->
        <video
          v-else-if="isVideo(msg.outputFile) && previewSrc && !previewFailed"
          :src="previewSrc"
          class="preview-video"
          controls
          @error="onPreviewError"
        />
        <span v-if="previewFailed" class="preview-missing">预览不可用（文件可能已被删除）</span>
      </div>

      <div v-if="canCopyAnswer" class="msg-actions">
        <button class="act-btn" type="button" @click="copyAnswer">{{ copied ? '✓ 已复制' : '⧉ 复制回答' }}</button>
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
/* 正在生成时给头像一个呼吸光环：比在正文里插光标更稳（不会打乱段落布局） */
.msg-row.ai.streaming .avatar { animation: avatar-pulse 1.8s var(--dsh-ease) infinite; }
@keyframes avatar-pulse {
  0%, 100% { box-shadow: 0 0 0 0 rgba(79, 110, 247, 0); }
  50% { box-shadow: 0 0 0 4px rgba(79, 110, 247, 0.16); }
}

.bubble {
  max-width: min(76%, 880px);
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

/* 等待首个事件的三点动画 */
.msg-typing { display: flex; align-items: center; gap: 4px; padding: 3px 0; }
.typing-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--dsh-text-4);
  animation: typing-bounce 1.2s var(--dsh-ease) infinite;
}
.typing-dot:nth-child(2) { animation-delay: 0.15s; }
.typing-dot:nth-child(3) { animation-delay: 0.3s; }
@keyframes typing-bounce {
  0%, 60%, 100% { transform: translateY(0); opacity: 0.45; }
  30% { transform: translateY(-4px); opacity: 1; }
}

.msg-text { white-space: normal; }
/* 阶段提示（"正在理解素材画面…"/"转码中 62%"）：与回答正文分开显示，不污染最终内容 */
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
.stage-body { display: flex; flex-direction: column; gap: 5px; min-width: 0; flex: 1; }
.stage-text { line-height: 1.4; }
.stage-bar {
  height: 4px;
  width: 100%;
  max-width: 220px;
  background: var(--dsh-surface-3);
  border-radius: var(--dsh-r-pill);
  overflow: hidden;
}
.stage-fill {
  height: 100%;
  background: var(--dsh-brand);
  border-radius: var(--dsh-r-pill);
  transition: width 0.35s var(--dsh-ease);
}

/* 错误块：红底 + 图标，一眼能从正常回答里区分出来 */
.msg-error {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  margin-top: 8px;
  padding: 9px 12px;
  background: var(--dsh-danger-soft);
  border: 1px solid var(--dsh-danger-line);
  border-left: 3px solid var(--dsh-danger);
  border-radius: var(--dsh-r-sm);
  color: var(--dsh-danger-strong);
  font-size: var(--dsh-fs-base);
  line-height: 1.6;
  white-space: pre-wrap;
}
.msg-error:first-child { margin-top: 0; }
.error-icon { flex-shrink: 0; font-size: 14px; line-height: 1.5; }
.error-text { min-width: 0; word-break: break-word; }

.msg-text :deep(p) { margin: 0 0 8px; }
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
/* 代码块：顶部工具条（语言 + 复制）常驻显示。
   旧实现把复制按钮绝对定位在右上角、悬停才出现，只好给 pre 留 32px 的空白带，
   且触屏设备根本看不到按钮。 */
.msg-text :deep(.code-block) {
  margin: 8px 0;
  border-radius: var(--dsh-r-sm);
  background: var(--dsh-code-bg);
  overflow: hidden;
}
.msg-text :deep(.code-bar) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-height: 26px;
  padding: 2px 6px 2px 12px;
  background: var(--dsh-code-veil);
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}
.msg-text :deep(.code-lang) {
  font-size: var(--dsh-fs-xs);
  color: var(--dsh-code-muted);
  opacity: 0.6;
  letter-spacing: 0.05em;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.msg-text :deep(.code-copy) {
  background: none;
  border: 1px solid transparent;
  color: var(--dsh-code-muted);
  border-radius: var(--dsh-r-xs);
  font-size: var(--dsh-fs-xs);
  font-family: inherit;
  padding: 2px 8px;
  cursor: pointer;
  flex-shrink: 0;
  transition: background var(--dsh-dur) var(--dsh-ease), color var(--dsh-dur) var(--dsh-ease);
}
.msg-text :deep(.code-copy:hover) { background: var(--dsh-code-hover); color: var(--dsh-text-invert); }
.msg-text :deep(pre) {
  background: none;
  color: var(--dsh-code-fg);
  padding: 11px 12px;
  border-radius: 0;
  overflow-x: auto;
  margin: 0;
  font-size: 12.5px;
  line-height: 1.6;
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
.user .msg-text :deep(code) { background: var(--dsh-code-hover); }
.user .msg-text :deep(a) { color: var(--dsh-on-brand-link); }
/* 用户气泡是蓝底，继承全局的链接色会看不清；这里统一为浅色并保留可辨识的下划线 */
.user .msg-text :deep(a:hover) { color: var(--dsh-text-invert); }
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
  background: var(--dsh-on-brand-veil);
  border-color: var(--dsh-on-brand-line);
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
.user .msg-file-tag { background: var(--dsh-on-brand-veil-strong); color: var(--dsh-text-invert); }

/* 输出文件：类型标签 + 文件名 + 下载按钮一行，预览图限高（否则整屏都被一张图占满） */
.output-area { margin-top: 10px; display: flex; flex-direction: column; gap: 7px; }
.output-head {
  display: flex;
  align-items: center;
  gap: 7px;
  min-width: 0;
  font-size: var(--dsh-fs-base);
}
.output-tag {
  flex-shrink: 0;
  font-size: var(--dsh-fs-xs);
  font-weight: 600;
  color: var(--dsh-success);
  background: var(--dsh-success-soft);
  border: 1px solid var(--dsh-success-line);
  border-radius: var(--dsh-r-pill);
  padding: 0 7px;
  line-height: 17px;
}
.output-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--dsh-text-2);
  font-family: Consolas, 'Courier New', monospace;
  font-size: var(--dsh-fs-sm);
}
.output-dl {
  flex-shrink: 0;
  margin-left: auto;
  font-weight: 500;
  padding: 2px 10px;
  border: 1px solid var(--dsh-brand-line);
  border-radius: var(--dsh-r-pill);
  background: var(--dsh-brand-soft);
  transition: background var(--dsh-dur) var(--dsh-ease), border-color var(--dsh-dur) var(--dsh-ease);
}
.output-dl:hover { background: var(--dsh-surface); border-color: var(--dsh-brand); text-decoration: none; }
.preview-link { display: block; line-height: 0; }
.preview-img {
  max-width: 100%;
  max-height: 360px;
  width: auto;
  height: auto;
  object-fit: contain;
  border-radius: var(--dsh-r-sm);
  border: 1px solid var(--dsh-border);
  background: var(--dsh-surface-2);
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

.msg-actions { display: flex; gap: 6px; margin-top: 8px; }
.act-btn {
  background: none;
  border: 1px solid var(--dsh-border);
  border-radius: var(--dsh-r-pill);
  color: var(--dsh-text-3);
  font-size: var(--dsh-fs-sm);
  font-family: inherit;
  padding: 3px 11px;
  cursor: pointer;
  transition: background var(--dsh-dur) var(--dsh-ease), color var(--dsh-dur) var(--dsh-ease),
    border-color var(--dsh-dur) var(--dsh-ease);
}
.act-btn:hover { background: var(--dsh-surface-3); color: var(--dsh-text); border-color: var(--dsh-border-strong); }
</style>
