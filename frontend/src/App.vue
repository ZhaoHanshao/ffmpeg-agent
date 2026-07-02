<script setup>
import { ref, nextTick, onMounted } from 'vue'

const API_BASE = '/api'

// ── 状态 ──
const uploadedFiles = ref([])
const outputFiles = ref([])
const messages = ref([])
const question = ref('')
const uploading = ref(false)
const sending = ref(false)
const dragOver = ref(false)
const loadingUpload = ref(false)
const loadingOutput = ref(false)

const messagesEnd = ref(null)
const fileInput = ref(null)
const textareaRef = ref(null)

function autoResize() {
  const el = textareaRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = el.scrollHeight + 'px'
}

// ── 文件列表刷新 ──
async function refreshUploadedFiles() {
  loadingUpload.value = true
  try {
    const res = await fetch(`${API_BASE}/upload`)
    const data = await res.json()
    uploadedFiles.value = data.files || []
  } catch (e) {
    console.error('获取上传文件列表失败:', e)
  } finally {
    loadingUpload.value = false
  }
}

async function refreshOutputFiles() {
  loadingOutput.value = true
  try {
    const res = await fetch(`${API_BASE}/output`)
    const data = await res.json()
    outputFiles.value = data.files || []
  } catch (e) {
    console.error('获取已完成文件列表失败:', e)
  } finally {
    loadingOutput.value = false
  }
}

// ── 文件上传 ──
function triggerUpload() {
  fileInput.value?.click()
}

function onFileChange(e) {
  const files = e.target.files
  if (files?.length) doUpload(files)
  e.target.value = ''
}

function onDrop(e) {
  dragOver.value = false
  const files = e.dataTransfer?.files
  if (files?.length) doUpload(files)
}

async function doUpload(files) {
  uploading.value = true
  try {
    const form = new FormData()
    for (const f of files) form.append('files', f)
    const res = await fetch(`${API_BASE}/upload`, { method: 'POST', body: form })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    await refreshUploadedFiles()
  } catch (e) {
    messages.value.push({ role: 'system', text: `上传失败: ${e.message}` })
  } finally {
    uploading.value = false
  }
}

// ── 文件删除 ──
async function deleteUploadedFile(filename) {
  try {
    const res = await fetch(`${API_BASE}/upload/${encodeURIComponent(filename)}`, { method: 'DELETE' })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    await refreshUploadedFiles()
  } catch (e) {
    messages.value.push({ role: 'system', text: `删除失败: ${e.message}` })
  }
}

// ── 对话（SSE 流式输出） ──
async function sendMessage() {
  const text = question.value.trim()
  if (!text || sending.value) return

  messages.value.push({ role: 'user', text })
  question.value = ''
  sending.value = true
  scrollBottom()

  // 创建占位 AI 消息，逐步填入 token
  const reply = { role: 'ai', text: '' }
  messages.value.push(reply)

  try {
    const form = new FormData()
    form.append('question', text)
    const res = await fetch(`${API_BASE}/chat`, { method: 'POST', body: form })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      // 保留最后一个不完整的行
      buffer = lines.pop() || ''

      for (const line of lines) {
        const trimmed = line.trim()
        if (!trimmed.startsWith('data: ')) continue

        const payload = trimmed.slice(6)
        if (payload === '{"event":"done"}') continue

        try {
          const data = JSON.parse(payload)
          if (data.event === 'token') {
            reply.text += data.text
            // 触发 Vue 响应式更新
            messages.value = [...messages.value]
            scrollBottom()
          } else if (data.event === 'meta' && data.output_file) {
            reply.outputFile = data.output_file
          }
        } catch {
          // 跳过不完整的 JSON
        }
      }
    }
  } catch (e) {
    reply.text = `请求失败: ${e.message}`
  } finally {
    sending.value = false
    scrollBottom()
    // 对话完成后 upload/ 已被后端清空，刷新两侧列表
    await refreshUploadedFiles()
    if (reply.outputFile) {
      await refreshOutputFiles()
    }
  }
}

function onKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    sendMessage()
  }
}

function scrollBottom() {
  nextTick(() => {
    messagesEnd.value?.scrollIntoView({ behavior: 'smooth' })
  })
}

function isImage(name) {
  return /\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(name)
}
function isVideo(name) {
  return /\.(mp4|webm|avi|mov|mkv)$/i.test(name)
}

// ── 初始化 ──
onMounted(() => {
  refreshUploadedFiles()
  refreshOutputFiles()
})
</script>

<template>
  <div class="app">
    <!-- ── 顶栏 ── -->
    <header class="header">
      <div class="header-inner">
        <span class="logo">🎬 FFmpeg Agent</span>
        <span class="subtitle">自然语言 → FFmpeg 命令</span>
      </div>
    </header>

    <!-- ── 双栏主体 ── -->
    <div class="body">
      <!-- ===== 左栏：文件管理 ===== -->
      <aside class="left-panel">
        <!-- 上传区域 -->
        <section class="upload-section">
          <div
            class="drop-zone"
            :class="{ 'drag-over': dragOver, 'has-files': uploadedFiles.length }"
            @dragover.prevent="dragOver = true"
            @dragleave="dragOver = false"
            @drop.prevent="onDrop"
            @click="triggerUpload"
          >
            <input
              ref="fileInput"
              type="file"
              multiple
              hidden
              @change="onFileChange"
            />
            <template v-if="!uploadedFiles.length">
              <span class="drop-icon">📁</span>
              <span class="drop-text">拖拽或点击上传文件</span>
            </template>
            <template v-else>
              <span class="drop-icon">✅</span>
              <span class="drop-text">{{ uploadedFiles.length }} 个文件已就绪</span>
            </template>
            <div v-if="uploading" class="uploading-overlay">
              <div class="spinner" />
              <span>上传中…</span>
            </div>
          </div>
        </section>

        <!-- 已上传文件 -->
        <section class="file-section">
          <div class="section-title">
            已上传文件
            <span v-if="uploadedFiles.length" class="section-count">{{ uploadedFiles.length }}</span>
          </div>
          <div v-if="loadingUpload" class="file-status">
            <span class="mini-spinner" /> 加载中…
          </div>
          <div v-else-if="!uploadedFiles.length" class="file-status empty">
            <span>暂无上传文件</span>
          </div>
          <div v-else class="file-list">
            <div v-for="f in uploadedFiles" :key="f" class="file-row">
              <span class="file-icon">📄</span>
              <span class="file-name" :title="f">{{ f }}</span>
              <div class="file-actions">
                <a
                  :href="`${API_BASE}/upload/${encodeURIComponent(f)}`"
                  class="file-btn download"
                  title="下载"
                  download
                >⬇</a>
                <button
                  class="file-btn delete"
                  title="删除"
                  @click="deleteUploadedFile(f)"
                >🗑</button>
              </div>
            </div>
          </div>
        </section>

        <!-- 已完成文件 -->
        <section class="file-section">
          <div class="section-title">
            已完成文件
            <span v-if="outputFiles.length" class="section-count">{{ outputFiles.length }}</span>
          </div>
          <div v-if="loadingOutput" class="file-status">
            <span class="mini-spinner" /> 加载中…
          </div>
          <div v-else-if="!outputFiles.length" class="file-status empty">
            <span>暂无完成文件</span>
          </div>
          <div v-else class="file-list">
            <div v-for="f in outputFiles" :key="f" class="file-row">
              <span class="file-icon">🎯</span>
              <span class="file-name" :title="f">{{ f }}</span>
              <div class="file-actions">
                <a
                  :href="`${API_BASE}/output/${encodeURIComponent(f)}`"
                  class="file-btn download"
                  title="下载"
                  download
                >⬇</a>
              </div>
            </div>
          </div>
        </section>
      </aside>

      <!-- ===== 右栏：对话界面 ===== -->
      <main class="right-panel">
        <div class="chat-messages">
          <div v-if="!messages.length" class="empty-chat">
            <div class="empty-icon">💬</div>
            <p>上传文件后，告诉我你想对文件做什么</p>
            <p class="examples">
              例如：<em>把图片反色</em> · <em>转成 mp4</em> · <em>裁剪中间 10 秒</em>
            </p>
          </div>

          <div v-for="(msg, i) in messages" :key="i" class="msg-row" :class="msg.role">
            <div class="avatar">{{ msg.role === 'user' ? '👤' : msg.role === 'ai' ? '🤖' : '⚙️' }}</div>
            <div class="bubble">
              <div class="msg-text" v-html="msg.text.replace(/\n/g, '<br>')" />
              <div v-if="msg.outputFile" class="output-area">
                <img
                  v-if="isImage(msg.outputFile)"
                  :src="`${API_BASE}/output/${encodeURIComponent(msg.outputFile)}`"
                  class="preview-img"
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

          <div ref="messagesEnd" />
        </div>

        <!-- ── 输入栏 ── -->
        <footer class="input-bar">
          <textarea
            ref="textareaRef"
            v-model="question"
            placeholder="输入你对文件的处理需求…"
            rows="1"
            :disabled="sending"
            @keydown="onKeydown"
            @input="autoResize"
          />
          <button class="send-btn" :disabled="!question.trim() || sending" @click="sendMessage">
            <span v-if="!sending">发送</span>
            <span v-else class="spinner-sm" />
          </button>
        </footer>
      </main>
    </div>
  </div>
</template>

<style>
/* ── Reset & Base ── */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans SC', sans-serif;
  background: #f0f2f5;
  color: #1a1a2e;
}
#app { height: 100%; }
a { color: #4f6ef7; text-decoration: none; }
a:hover { text-decoration: underline; }

/* ── App Layout ── */
.app {
  display: flex;
  flex-direction: column;
  height: 100vh;
}

/* ── Header ── */
.header {
  padding: 14px 20px;
  background: #fff;
  border-bottom: 1px solid #e5e7eb;
  flex-shrink: 0;
}
.header-inner {
  display: flex;
  align-items: baseline;
  gap: 12px;
}
.logo { font-size: 20px; font-weight: 700; color: #1a1a2e; }
.subtitle { font-size: 13px; color: #9ca3af; }

/* ── Body (two-column) ── */
.body {
  flex: 1;
  display: flex;
  overflow: hidden;
}

/* ===== Left Panel ===== */
.left-panel {
  width: 360px;
  flex-shrink: 0;
  overflow-y: auto;
  background: #fff;
  border-right: 1px solid #e5e7eb;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

/* ── Upload Zone ── */
.upload-section { flex-shrink: 0; }
.drop-zone {
  position: relative;
  border: 2px dashed #d1d5db;
  border-radius: 10px;
  padding: 16px;
  text-align: center;
  cursor: pointer;
  transition: all 0.2s;
  background: #fafbfc;
}
.drop-zone:hover { border-color: #4f6ef7; background: #f8f9ff; }
.drop-zone.drag-over { border-color: #4f6ef7; background: #eef1ff; }
.drop-zone.has-files { border-style: solid; border-color: #22c55e; background: #f0fdf4; }
.drop-icon { display: block; font-size: 24px; margin-bottom: 2px; }
.drop-text { display: block; font-size: 13px; font-weight: 500; color: #374151; }
.uploading-overlay {
  position: absolute; inset: 0;
  display: flex; align-items: center; justify-content: center; gap: 8px;
  background: rgba(255,255,255,.85);
  border-radius: 10px;
  font-size: 13px; color: #4f6ef7;
}
.uploading-overlay .spinner {
  width: 16px; height: 16px;
  border: 2px solid #e5e7eb;
  border-top-color: #4f6ef7;
  border-radius: 50%;
  animation: spin .6s linear infinite;
}

/* ── File Sections ── */
.file-section { flex-shrink: 0; }
.section-title {
  font-size: 13px;
  font-weight: 600;
  color: #6b7280;
  padding: 12px 0 6px;
  display: flex;
  align-items: center;
  gap: 6px;
  letter-spacing: 0.3px;
}
.section-count {
  font-size: 11px;
  font-weight: 500;
  color: #9ca3af;
  background: #f3f4f6;
  padding: 1px 7px;
  border-radius: 10px;
  line-height: 18px;
}
.file-status {
  padding: 8px 0;
  font-size: 13px;
  color: #9ca3af;
  display: flex;
  align-items: center;
  gap: 6px;
}
.file-status.empty { padding: 14px 0; text-align: center; justify-content: center; }

.mini-spinner {
  display: inline-block;
  width: 12px; height: 12px;
  border: 2px solid #e5e7eb;
  border-top-color: #4f6ef7;
  border-radius: 50%;
  animation: spin .6s linear infinite;
}

/* ── File List ── */
.file-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.file-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 6px;
  transition: background 0.15s;
  cursor: default;
}
.file-row:hover { background: #f0f2f5; }
.file-icon {
  font-size: 14px;
  flex-shrink: 0;
  line-height: 1;
}
.file-name {
  flex: 1;
  font-size: 13px;
  color: #374151;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}
.file-actions {
  display: flex;
  gap: 2px;
  flex-shrink: 0;
  opacity: 0;
  transition: opacity 0.15s;
}
.file-row:hover .file-actions { opacity: 1; }
.file-btn {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 14px;
  padding: 4px;
  border-radius: 4px;
  line-height: 1;
  transition: background 0.15s;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
}
.file-btn:hover { background: #e5e7eb; text-decoration: none; }
.file-btn.delete:hover { background: #fee2e2; }

/* ===== Right Panel ===== */
.right-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-width: 0;
}

/* ── Chat Messages ── */
.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.empty-chat {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: #9ca3af;
  text-align: center;
  gap: 4px;
}
.empty-icon { font-size: 48px; margin-bottom: 8px; }
.empty-chat p { font-size: 14px; }
.examples { font-size: 13px; color: #d1d5db; }
.examples em { font-style: normal; color: #4f6ef7; }

.msg-row { display: flex; gap: 10px; align-items: flex-start; }
.msg-row.user { flex-direction: row-reverse; }
.msg-row.system { justify-content: center; }
.msg-row.system .bubble {
  background: #f3f4f6; color: #6b7280; font-size: 13px; padding: 6px 14px; text-align: center;
}

.avatar {
  width: 32px; height: 32px;
  border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
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
.user .bubble {
  background: #4f6ef7;
  color: #fff;
  border-bottom-right-radius: 4px;
}
.ai .bubble {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-bottom-left-radius: 4px;
}
.msg-text { white-space: pre-wrap; }

/* ── Output preview ── */
.output-area { margin-top: 8px; display: flex; flex-direction: column; gap: 6px; }
.preview-img {
  max-width: 100%; max-height: 320px;
  border-radius: 8px;
  border: 1px solid #e5e7eb;
  cursor: zoom-in;
}
.preview-video { width: 100%; max-height: 320px; border-radius: 8px; }
.download-link { font-size: 13px; }

/* ── Thinking animation ── */
.dot-pulse {
  display: inline-block;
  width: 8px; height: 8px;
  border-radius: 50%;
  background: #4f6ef7;
  animation: pulse 1.2s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: .3; transform: scale(.8); }
  50% { opacity: 1; transform: scale(1.2); }
}

/* ── Input Bar ── */
.input-bar {
  display: flex;
  gap: 8px;
  padding: 12px 20px 16px;
  border-top: 1px solid #e5e7eb;
  background: #f0f2f5;
  flex-shrink: 0;
}
.input-bar textarea {
  flex: 1;
  border: 1px solid #d1d5db;
  border-radius: 10px;
  padding: 10px 14px;
  font-size: 14px;
  font-family: inherit;
  resize: none;
  outline: none;
  transition: border-color .2s;
  max-height: 120px;
  background: #fff;
}
.input-bar textarea:focus { border-color: #4f6ef7; }
.input-bar textarea:disabled { opacity: .5; }

.send-btn {
  padding: 0 20px;
  border: none;
  border-radius: 10px;
  background: #4f6ef7;
  color: #fff;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: background .2s;
  white-space: nowrap;
}
.send-btn:hover:not(:disabled) { background: #3b5de7; }
.send-btn:disabled { opacity: .4; cursor: not-allowed; }

.spinner-sm {
  display: inline-block;
  width: 16px; height: 16px;
  border: 2px solid rgba(255,255,255,.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin .6s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
</style>
