<script setup>
import { computed, ref, watch, onUnmounted } from 'vue'
import { previewKind, fileKind, formatSize } from '../utils'
import { useFileUrl, downloadFile, fetchFileBlob, decodeText } from '../composables/useFileUrl'

const props = defineProps({
  // 同一批文件，用于左右切换（从列表点进来时就是整个列表）
  files: { type: Array, default: () => [] },
  src: { type: String, default: 'upload' },
})
const show = defineModel('show', { type: Boolean, default: false })
const index = defineModel('index', { type: Number, default: 0 })

const current = computed(() => props.files[index.value] || '')
const kind = computed(() => previewKind(current.value))
const kindLabel = computed(() => fileKind(current.value).label)

// 无法预览的类型不预取内容：否则为了给一个下载按钮，先把整个文件拉进内存
const urlTarget = computed(() =>
  kind.value === 'none' ? { name: '', src: props.src } : { name: current.value, src: props.src }
)
const { url, loading, error, oversized } = useFileUrl(urlTarget)

// 未启用鉴权时 url 是直链、不经过 fetch，"文件已被删除"只能靠媒体标签的 error 事件发现
function onMediaError() {
  error.value = '文件无法加载（可能已被删除）'
}

// ── 文本预览 ──
// 只读前 400KB：字幕/日志动辄几十万行，整份塞进 DOM 会把页面卡死
const MAX_TEXT_BYTES = 400 * 1024
const text = ref('')
const textTruncated = ref(false)
const textLoading = ref(false)
let textSeq = 0

async function loadText() {
  const seq = ++textSeq
  text.value = ''
  textTruncated.value = false
  if (kind.value !== 'text' || !current.value) return
  textLoading.value = true
  try {
    const blob = await fetchFileBlob(current.value, props.src)
    const slice = blob.slice(0, MAX_TEXT_BYTES)
    const buf = await slice.arrayBuffer()
    if (seq !== textSeq) return
    text.value = decodeText(buf)
    textTruncated.value = blob.size > MAX_TEXT_BYTES
  } catch (e) {
    if (seq === textSeq) text.value = `读取失败：${e?.message || e}`
  } finally {
    if (seq === textSeq) textLoading.value = false
  }
}

const zoomed = ref(false)

watch([show, current], ([open]) => {
  zoomed.value = false
  if (open) loadText()
  else textSeq += 1
}, { immediate: true })

// ── 切换 / 关闭 ──
const hasSiblings = computed(() => props.files.length > 1)

function step(delta) {
  const n = props.files.length
  if (n < 2) return
  index.value = (index.value + delta + n) % n
}

function close() {
  show.value = false
}

function onKeydown(e) {
  if (!show.value) return
  if (e.key === 'Escape') {
    e.preventDefault()
    close()
  } else if (e.key === 'ArrowLeft') {
    e.preventDefault()
    step(-1)
  } else if (e.key === 'ArrowRight') {
    e.preventDefault()
    step(1)
  }
}

// 用 window 级监听而不是挂在元素上：焦点在弹窗里的哪个控件上都能响应
window.addEventListener('keydown', onKeydown)
onUnmounted(() => window.removeEventListener('keydown', onKeydown))

// ── 下载 ──
// 无法预览的类型没有预取内容，downloadFile 会按需取一次 blob；
// 这样开了 AUTH_TOKEN 也能下载（<a href=直链> 带不了鉴权头）。
function download() {
  downloadFile(current.value, props.src, url.value).catch(() => {})
}
</script>

<template>
  <div v-if="show && current" class="pv-overlay" @click.self="close">
    <div class="pv-modal" role="dialog" aria-modal="true" :aria-label="`预览 ${current}`">
      <header class="pv-head">
        <span class="pv-kind">{{ kindLabel }}</span>
        <span class="pv-name" :title="current">{{ current }}</span>
        <span v-if="hasSiblings" class="pv-pos">{{ index + 1 }} / {{ files.length }}</span>
        <button class="pv-icon-btn" type="button" title="下载" aria-label="下载" @click="download">⬇</button>
        <button class="pv-icon-btn" type="button" title="关闭（Esc）" aria-label="关闭" @click="close">✕</button>
      </header>

      <div class="pv-body" :class="`kind-${kind}`">
        <div v-if="loading || textLoading" class="pv-status"><span class="pv-spinner" />加载中…</div>
        <p v-else-if="error" class="pv-status pv-error">预览失败：{{ error }}</p>
        <!-- 开启鉴权时只能整包取回再交给标签，超大文件会卡住页面，所以直接劝下载 -->
        <div v-else-if="oversized" class="pv-none">
          <span class="pv-none-icon" aria-hidden="true">📦</span>
          <p>文件较大（{{ formatSize(oversized) }}），预览需要先完整下载到浏览器内存</p>
          <button class="pv-btn" type="button" @click="download">⬇ 下载文件</button>
        </div>

        <template v-else>
          <img
            v-if="kind === 'image'"
            :src="url"
            class="pv-image"
            :class="{ actual: zoomed }"
            alt="图片预览"
            :title="zoomed ? '点击适应窗口' : '点击查看原始尺寸'"
            @click="zoomed = !zoomed"
            @error="onMediaError"
          />
          <video v-else-if="kind === 'video'" :src="url" class="pv-video" controls autoplay @error="onMediaError" />
          <div v-else-if="kind === 'audio'" class="pv-audio-wrap">
            <span class="pv-audio-icon" aria-hidden="true">🎵</span>
            <audio :src="url" class="pv-audio" controls autoplay @error="onMediaError" />
          </div>
          <template v-else-if="kind === 'text'">
            <pre class="pv-text">{{ text }}</pre>
            <p v-if="textTruncated" class="pv-truncated">内容较大，仅显示前 400 KB。</p>
          </template>
          <div v-else class="pv-none">
            <span class="pv-none-icon" aria-hidden="true">📄</span>
            <p>这个类型无法在浏览器里预览</p>
            <button class="pv-btn" type="button" @click="download">⬇ 下载文件</button>
          </div>
        </template>
      </div>

      <button
        v-if="hasSiblings"
        class="pv-nav prev"
        type="button"
        title="上一个（←）"
        aria-label="上一个"
        @click="step(-1)"
      >‹</button>
      <button
        v-if="hasSiblings"
        class="pv-nav next"
        type="button"
        title="下一个（→）"
        aria-label="下一个"
        @click="step(1)"
      >›</button>
    </div>
  </div>
</template>

<style scoped>
.pv-overlay {
  position: fixed;
  inset: 0;
  z-index: 1100;
  background: rgba(15, 23, 42, 0.62);
  backdrop-filter: blur(3px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
}
.pv-modal {
  position: relative;
  background: var(--dsh-surface);
  border-radius: var(--dsh-r-xl);
  box-shadow: var(--dsh-shadow-lg);
  width: min(1100px, 100%);
  max-height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.pv-head {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 11px 14px;
  border-bottom: 1px solid var(--dsh-border);
  flex-shrink: 0;
  min-width: 0;
}
.pv-kind {
  flex-shrink: 0;
  font-size: var(--dsh-fs-xs);
  font-weight: 600;
  color: var(--dsh-brand);
  background: var(--dsh-brand-soft);
  border: 1px solid var(--dsh-brand-line);
  border-radius: var(--dsh-r-pill);
  padding: 1px 9px;
  line-height: 18px;
}
.pv-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--dsh-fs-md);
  font-weight: 500;
  color: var(--dsh-text);
  font-family: Consolas, 'Courier New', monospace;
}
.pv-pos {
  flex-shrink: 0;
  font-size: var(--dsh-fs-sm);
  color: var(--dsh-text-4);
  font-variant-numeric: tabular-nums;
}
.pv-icon-btn {
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: none;
  border: 1px solid transparent;
  border-radius: var(--dsh-r-sm);
  color: var(--dsh-text-3);
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  transition: all var(--dsh-dur) var(--dsh-ease);
}
.pv-icon-btn:hover { background: var(--dsh-surface-3); border-color: var(--dsh-border); color: var(--dsh-text); }

.pv-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  background: var(--dsh-surface-2);
  display: flex;
  flex-direction: column;
}
/* 图片/文字区居中；视频不要再居中，否则宽高比不对时上下留白很怪 */
.pv-body.kind-image, .pv-body.kind-audio, .pv-body.kind-none, .pv-body.kind-text {
  align-items: center;
  justify-content: center;
}
.pv-status {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 40px 20px;
  color: var(--dsh-text-3);
  font-size: var(--dsh-fs-base);
}
.pv-error { color: var(--dsh-danger-strong); }
.pv-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid var(--dsh-border);
  border-top-color: var(--dsh-brand);
  border-radius: 50%;
  animation: pv-spin 0.7s linear infinite;
}
@keyframes pv-spin { to { transform: rotate(360deg); } }

.pv-image {
  max-width: 100%;
  max-height: 70vh;
  object-fit: contain;
  cursor: zoom-in;
  display: block;
}
/* 原始尺寸：交给外层滚动，指针提示改成"点回去" */
.pv-image.actual {
  max-width: none;
  max-height: none;
  cursor: zoom-out;
}
.pv-video { width: 100%; max-height: 72vh; background: #000; display: block; }
.pv-audio-wrap {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16px;
  padding: 40px 24px;
  width: 100%;
}
.pv-audio-icon { font-size: 40px; opacity: 0.7; }
.pv-audio { width: min(520px, 100%); }
.pv-text {
  margin: 0;
  padding: 14px 16px;
  width: 100%;
  font-family: Consolas, 'Courier New', monospace;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--dsh-code-fg);
  background: var(--dsh-code-bg);
  white-space: pre-wrap;
  word-break: break-word;
  align-self: stretch;
}
.pv-truncated {
  align-self: stretch;
  padding: 7px 16px;
  font-size: var(--dsh-fs-sm);
  color: var(--dsh-warn);
  background: var(--dsh-warn-soft);
  border-top: 1px solid var(--dsh-warn-line);
}
.pv-none {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 48px 24px;
  color: var(--dsh-text-3);
  font-size: var(--dsh-fs-md);
}
.pv-none-icon { font-size: 40px; opacity: 0.6; }
.pv-btn {
  padding: 8px 18px;
  background: var(--dsh-brand);
  color: var(--dsh-text-invert);
  border: none;
  border-radius: var(--dsh-r-sm);
  font-size: var(--dsh-fs-md);
  font-family: inherit;
  font-weight: 500;
  cursor: pointer;
  box-shadow: var(--dsh-shadow-brand);
}
.pv-btn:hover { background: var(--dsh-brand-strong); }

/* 左右切换：贴在弹窗两侧垂直居中 */
.pv-nav {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  width: 36px;
  height: 52px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--dsh-surface);
  border: 1px solid var(--dsh-border);
  border-radius: var(--dsh-r-md);
  color: var(--dsh-text-2);
  font-size: 22px;
  line-height: 1;
  cursor: pointer;
  box-shadow: var(--dsh-shadow-md);
  transition: all var(--dsh-dur) var(--dsh-ease);
}
.pv-nav:hover { color: var(--dsh-brand); border-color: var(--dsh-brand-line); background: var(--dsh-brand-soft); }
.pv-nav.prev { left: 10px; }
.pv-nav.next { right: 10px; }

@media (max-width: 640px) {
  .pv-overlay { padding: 10px; }
  .pv-nav { display: none; }
}
</style>
