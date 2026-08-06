<script setup>
import { ref, onMounted, watch, nextTick, computed, onUnmounted } from 'vue'
import { useChat } from './composables/useChat'
import { useSettings } from './composables/useSettings'
import { api } from './api'
import MessageItem from './components/MessageItem.vue'
import FilePanel from './components/FilePanel.vue'
import SelectedFilesBar from './components/SelectedFilesBar.vue'
import SettingsModal from './components/SettingsModal.vue'

// ── 模式：ffmpeg 处理 / ffprobe 分析 ──
const mode = ref('ffmpeg')
const leftCollapsed = ref(false)

// ── 初始化状态（首次运行后台下载模型、构建知识库） ──
const initStatus = ref('ok')
const initError = ref('')
const initProgress = ref(0)
const initStep = ref('')
let healthTimer = null

function pollHealth() {
  api
    .health()
    .then((h) => {
      initStatus.value = h.status || 'ok'
      initError.value = h.error || ''
      initProgress.value = h.progress || 0
      initStep.value = h.step || ''
      if (initStatus.value === 'ok') {
        clearInterval(healthTimer)
        healthTimer = null
      }
    })
    .catch(() => {})
}

const {
  messages,
  question,
  sending,
  canSend,
  chatContainer,
  sendMessage,
  stopChat,
  clearMessages,
  onChatScroll,
} = useChat(mode)

const {
  showSettings,
  savingSettings,
  configured,
  settings,
  loadSettings,
  saveSettings,
} = useSettings()

const textareaRef = ref(null)
const filePanel = ref(null)

// ── 选中的待处理文件（仅本地选择态，不移除服务器文件） ──
// 元素：{ name: 文件名, src: 'upload' | 'output' }
const selectedFiles = ref([])

function addToWorkspace(name, src) {
  if (!selectedFiles.value.some((s) => s.src === src && s.name === name)) {
    selectedFiles.value.push({ name, src })
  }
}

function onChipRemove(item) {
  selectedFiles.value = selectedFiles.value.filter((s) => !(s.name === item.name && s.src === item.src))
}

function onFileRemoved(name, src = 'upload') {
  selectedFiles.value = selectedFiles.value.filter((s) => !(s.name === name && s.src === src))
}

const hasFiles = computed(() => selectedFiles.value.length > 0)

function pushSystem(text) {
  messages.value.push({ role: 'system', text })
}

function autoResize() {
  const el = textareaRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${el.scrollHeight}px`
}

// 发送后 question 被清空（不触发 input 事件），watch 确保高度复位
watch(question, () => nextTick(autoResize))

async function doSend() {
  const files = selectedFiles.value.map((s) => ({ ...s }))
  selectedFiles.value = []
  await sendMessage(files)
  await filePanel.value?.refreshOutputFiles()
}

function onKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    doSend()
  }
}

function useExample(text) {
  question.value = text
  autoResize()
  doSend()
}

async function onSend() {
  await doSend()
}

onMounted(() => {
  if (window.matchMedia?.('(max-width: 768px)').matches) leftCollapsed.value = true
  pollHealth()
  healthTimer = setInterval(pollHealth, 3000)
  loadSettings()
})

onUnmounted(() => {
  if (healthTimer) clearInterval(healthTimer)
})
</script>

<template>
  <div class="app">
    <!-- ── 顶栏 ── -->
    <header class="header">
      <div class="header-inner">
        <span class="logo">🎬 FFmpeg Agent</span>
        <span class="subtitle">{{ mode === 'ffprobe' ? '自然语言 → FFprobe 分析' : '自然语言 → FFmpeg 命令' }}</span>
        <div class="header-spacer" />
        <div class="mode-switch">
          <button class="mode-btn" :class="{ active: mode === 'ffmpeg' }" @click="mode = 'ffmpeg'">FFmpeg 处理</button>
          <button class="mode-btn" :class="{ active: mode === 'ffprobe' }" @click="mode = 'ffprobe'">FFprobe 分析</button>
        </div>
        <button
          class="icon-btn"
          :class="{ active: !leftCollapsed }"
          title="切换文件列表"
          @click="leftCollapsed = !leftCollapsed"
        >📁</button>
        <button class="icon-btn" title="清空对话" @click="clearMessages">🗑️</button>
        <button class="icon-btn" title="LLM 设置" @click="showSettings = true">⚙️</button>
      </div>
    </header>

    <!-- ── 初始化状态横幅（首次运行下载模型/构建知识库） ── -->
    <div v-if="initStatus === 'running'" class="init-banner">
      <span class="init-spinner"></span>
      <div class="init-body">
        <span class="init-text">{{ initStep || '正在初始化…' }}</span>
        <div class="init-bar"><div class="init-fill" :style="{ width: Math.max(3, initProgress) + '%' }"></div></div>
      </div>
      <span class="init-pct">{{ initProgress }}%</span>
    </div>
    <div v-else-if="initStatus === 'error'" class="init-banner error">
      ⚠️ 初始化失败：{{ initError.substring(0, 300) }}
    </div>

    <!-- ── 设置弹窗 ── -->
    <SettingsModal
      v-model:show="showSettings"
      v-model="settings"
      :configured="configured"
      :saving="savingSettings"
      @save="saveSettings"
    />

    <!-- ── 双栏主体 ── -->
    <div class="body">
      <FilePanel
        ref="filePanel"
        :selected-files="selectedFiles"
        :class="{ collapsed: leftCollapsed }"
        @notify="pushSystem"
        @select-output="addToWorkspace"
        @removed="onFileRemoved"
      />

      <!-- ===== 右栏：对话界面 ===== -->
      <main class="right-panel">
        <div ref="chatContainer" class="chat-messages" @scroll="onChatScroll">
          <div v-if="!messages.length" class="empty-chat">
            <div class="empty-icon">💬</div>
            <p>{{ mode === 'ffprobe' ? '选择文件后，告诉我你想查看文件的哪些信息' : '选择文件后，告诉我你想对文件做什么' }}</p>
            <p v-if="!hasFiles" class="empty-hint">在左侧面板点击 ＋ 将文件加入工作区，再输入需求</p>
            <p class="examples">
              <button v-if="mode === 'ffprobe'" class="example-chip" @click="useExample('查看视频的分辨率和编码')">查看视频的分辨率和编码</button>
              <button v-if="mode === 'ffprobe'" class="example-chip" @click="useExample('查看音频采样率')">查看音频采样率</button>
              <button v-if="mode !== 'ffprobe'" class="example-chip" @click="useExample('把图片反色')">把图片反色</button>
              <button v-if="mode !== 'ffprobe'" class="example-chip" @click="useExample('转成 mp4')">转成 mp4</button>
              <button v-if="mode !== 'ffprobe'" class="example-chip" @click="useExample('裁剪中间 10 秒')">裁剪中间 10 秒</button>
            </p>
          </div>

          <MessageItem v-for="(msg, i) in messages" :key="i" :msg="msg" />
        </div>

        <!-- ── 选中文件栏 + 输入栏 ── -->
        <SelectedFilesBar
          v-if="hasFiles"
          :files="selectedFiles"
          @remove="onChipRemove"
          @add="filePanel?.triggerUpload()"
        />
        <footer class="input-bar">
          <textarea
            ref="textareaRef"
            v-model="question"
            :placeholder="mode === 'ffprobe' ? '输入你想查看的文件信息…' : '输入你对文件的处理需求…'"
            rows="1"
            :disabled="sending"
            @keydown="onKeydown"
            @input="autoResize"
          />
          <button v-if="!sending" class="send-btn" :disabled="!canSend" @click="onSend">发送</button>
          <button v-else class="send-btn stop" @click="stopChat">⏹ 停止</button>
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
  align-items: center;
  gap: 12px;
}
.logo { font-size: 20px; font-weight: 700; color: #1a1a2e; white-space: nowrap; }
.subtitle { font-size: 13px; color: #9ca3af; white-space: nowrap; }
.header-spacer { flex: 1; }

/* ── Header Controls ── */
.mode-switch {
  display: flex;
  background: #f3f4f6;
  border-radius: 8px;
  padding: 3px;
  gap: 2px;
}
.mode-btn {
  background: none;
  border: none;
  border-radius: 6px;
  padding: 5px 12px;
  font-size: 13px;
  font-weight: 500;
  color: #6b7280;
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
}
.mode-btn:hover { color: #374151; }
.mode-btn.active {
  background: #fff;
  color: #4f6ef7;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
}
.icon-btn {
  background: none;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  cursor: pointer;
  font-size: 16px;
  padding: 4px 10px;
  line-height: 1;
  transition: all 0.15s;
}
.icon-btn:hover { background: #f3f4f6; border-color: #d1d5db; }
.icon-btn.active { background: #eef1ff; border-color: #4f6ef7; }

/* ── Body (two-column) ── */
.body {
  flex: 1;
  display: flex;
  overflow: hidden;
}

/* 左侧面板折叠（类名由父级传入，命中 FilePanel 根元素） */
.left-panel { transition: margin-left 0.25s ease; }
.left-panel.collapsed { margin-left: -361px; }

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
.chat-messages::-webkit-scrollbar { width: 8px; }
.chat-messages::-webkit-scrollbar-thumb { background: #d1d5db; border-radius: 4px; }

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
.empty-hint { font-size: 12px !important; color: #b8bfcc; }
.examples {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
  margin-top: 12px;
  max-width: 480px;
}
.example-chip {
  border: 1px solid #d1d5db;
  background: #fff;
  color: #4f6ef7;
  border-radius: 999px;
  padding: 6px 14px;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s;
}
.example-chip:hover { border-color: #4f6ef7; background: #eef1ff; }

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
  overflow: hidden;
  scrollbar-width: none;
  transition: border-color 0.2s;
  background: #fff;
}
.input-bar textarea::-webkit-scrollbar { display: none; }
.input-bar textarea:focus { border-color: #4f6ef7; }
.input-bar textarea:disabled { opacity: 0.5; }

.send-btn {
  padding: 0 20px;
  border: none;
  border-radius: 10px;
  background: #4f6ef7;
  color: #fff;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.2s;
  white-space: nowrap;
}
.send-btn:hover:not(:disabled) { background: #3b5de7; }
.send-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.send-btn.stop { background: #ef4444; }
.send-btn.stop:hover { background: #dc2626; }

/* ── Keyframes ── */
@keyframes spin { to { transform: rotate(360deg); } }

/* ── Init Banner ── */
.init-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 20px;
  background: #fff7e6;
  color: #b45309;
  border-bottom: 1px solid #fde68a;
  font-size: 13px;
  flex-shrink: 0;
}
.init-banner.error {
  background: #fef2f2;
  color: #b91c1c;
  border-bottom-color: #fecaca;
  white-space: pre-wrap;
  word-break: break-all;
}
.init-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid #f59e0b;
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  flex-shrink: 0;
}
.init-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.init-text { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.init-pct { font-variant-numeric: tabular-nums; flex-shrink: 0; }
.init-bar {
  height: 5px;
  background: #fde68a;
  border-radius: 999px;
  overflow: hidden;
}
.init-fill {
  height: 100%;
  background: #f59e0b;
  border-radius: 999px;
  transition: width 0.4s ease;
}

/* ── Responsive ── */
@media (max-width: 640px) {
  .subtitle { display: none; }
  .header { padding: 10px 12px; }
  .chat-messages { padding: 12px; }
  .input-bar { padding: 10px 12px 12px; }
}
</style>
