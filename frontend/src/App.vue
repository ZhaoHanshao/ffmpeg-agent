<script setup>
import { ref, nextTick } from 'vue'

const API_BASE = '/api'

// ── 状态 ──
const uploadedFiles = ref([])
const messages = ref([])
const question = ref('')
const uploading = ref(false)
const sending = ref(false)
const dragOver = ref(false)

const messagesEnd = ref(null)
const fileInput = ref(null)
const textareaRef = ref(null)

function autoResize() {
  const el = textareaRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = el.scrollHeight + 'px'
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
    const data = await res.json()
    uploadedFiles.value = data.uploaded || []
  } catch (e) {
    messages.value.push({ role: 'system', text: `上传失败: ${e.message}` })
  } finally {
    uploading.value = false
  }
}

// ── 对话 ──
async function sendMessage() {
  const text = question.value.trim()
  if (!text || sending.value) return

  messages.value.push({ role: 'user', text })
  question.value = ''
  sending.value = true
  scrollBottom()

  try {
    const form = new FormData()
    form.append('question', text)
    const res = await fetch(`${API_BASE}/chat`, { method: 'POST', body: form })
    const data = await res.json()

    const reply = { role: 'ai', text: data.reply || '(无回复)' }
    if (data.output_file) reply.outputFile = data.output_file
    messages.value.push(reply)
  } catch (e) {
    messages.value.push({ role: 'system', text: `请求失败: ${e.message}` })
  } finally {
    sending.value = false
    scrollBottom()
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

    <!-- ── 主体 ── -->
    <main class="main">
      <!-- 上传区 -->
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
            <span class="drop-text">拖拽文件到此处，或点击选择</span>
            <span class="drop-hint">支持图片、视频、音频等任意文件</span>
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

        <!-- 已上传文件列表 -->
        <div v-if="uploadedFiles.length" class="file-chips">
          <div v-for="f in uploadedFiles" :key="f" class="chip">
            <span>📄 {{ f }}</span>
          </div>
        </div>
      </section>

      <!-- ── 消息区 ── -->
      <section class="chat-section">
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
            <!-- 文件预览 / 下载 -->
            <div v-if="msg.outputFile" class="output-area">
              <img v-if="isImage(msg.outputFile)" :src="`${API_BASE}/output/${msg.outputFile}`" class="preview-img" />
              <video v-else-if="isVideo(msg.outputFile)" :src="`${API_BASE}/output/${msg.outputFile}`" class="preview-video" controls />
              <a :href="`${API_BASE}/output/${msg.outputFile}`" class="download-link" download>⬇️ 下载 {{ msg.outputFile }}</a>
            </div>
          </div>
        </div>

        <div v-if="sending" class="msg-row ai">
          <div class="avatar">🤖</div>
          <div class="bubble thinking">
            <span class="dot-pulse" />
          </div>
        </div>

        <div ref="messagesEnd" />
      </section>
    </main>

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

/* ── Layout ── */
.app {
  display: flex;
  flex-direction: column;
  height: 100vh;
  max-width: 800px;
  margin: 0 auto;
  padding: 0 16px;
}

/* ── Header ── */
.header {
  padding: 16px 0 8px;
  border-bottom: 1px solid #e5e7eb;
}
.header-inner {
  display: flex;
  align-items: baseline;
  gap: 12px;
}
.logo { font-size: 20px; font-weight: 700; }
.subtitle { font-size: 13px; color: #9ca3af; }

/* ── Main ── */
.main {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 12px 0;
}

/* ── Upload ── */
.upload-section { flex-shrink: 0; }
.drop-zone {
  position: relative;
  border: 2px dashed #d1d5db;
  border-radius: 12px;
  padding: 20px;
  text-align: center;
  cursor: pointer;
  transition: all 0.2s;
  background: #fff;
}
.drop-zone:hover { border-color: #4f6ef7; background: #f8f9ff; }
.drop-zone.drag-over { border-color: #4f6ef7; background: #eef1ff; }
.drop-zone.has-files { border-style: solid; border-color: #22c55e; background: #f0fdf4; }
.drop-icon { display: block; font-size: 28px; margin-bottom: 4px; }
.drop-text { display: block; font-size: 14px; font-weight: 500; color: #374151; }
.drop-hint { display: block; font-size: 12px; color: #9ca3af; margin-top: 2px; }
.uploading-overlay {
  position: absolute; inset: 0;
  display: flex; align-items: center; justify-content: center; gap: 8px;
  background: rgba(255,255,255,.85);
  border-radius: 12px;
  font-size: 14px; color: #4f6ef7;
}
.uploading-overlay .spinner {
  width: 18px; height: 18px;
  border: 2px solid #e5e7eb;
  border-top-color: #4f6ef7;
  border-radius: 50%;
  animation: spin .6s linear infinite;
}

.file-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.chip {
  background: #eef2ff;
  color: #4f6ef7;
  font-size: 12px;
  padding: 4px 10px;
  border-radius: 6px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 240px;
}

/* ── Chat ── */
.chat-section {
  flex: 1;
  overflow-y: auto;
  padding: 4px 0;
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

/* ── Input ── */
.input-bar {
  display: flex;
  gap: 8px;
  padding: 12px 0 20px;
  border-top: 1px solid #e5e7eb;
  background: #f0f2f5;
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
