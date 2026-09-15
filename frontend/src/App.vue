<script setup>
import { ref, onMounted, watch, nextTick, computed, onUnmounted } from 'vue'
import { useChat } from './composables/useChat'
import { useSettings } from './composables/useSettings'
import { useConversations } from './composables/useConversations'
import { api } from './api'
import MessageItem from './components/MessageItem.vue'
import FilePanel from './components/FilePanel.vue'
import SelectedFilesBar from './components/SelectedFilesBar.vue'
import SettingsModal from './components/SettingsModal.vue'
import ConversationList from './components/ConversationList.vue'

// ── 模式：ffmpeg 处理 / ffprobe 分析 ──
const mode = ref('ffmpeg')
// 两个侧栏都可折叠：对话列表在左，文件面板在右
const filesCollapsed = ref(false)
const convCollapsed = ref(false)

// ── 初始化状态（首次运行后台下载模型、构建知识库） ──
const initStatus = ref('ok')
const initError = ref('')
const initProgress = ref(0)
const initStep = ref('')
let healthTimer = null
let healthDelay = 3000
let healthTicks = 0

// 轮询节奏：初始化中 3s 一次；超过 ~1 分钟仍未就绪就退避到 30s 一次，
// 避免长时间建库/下载期间持续打后端。就绪或失败即停止（失败后不再有意义）。
const HEALTH_FAST_MS = 3000
const HEALTH_SLOW_MS = 30000
const HEALTH_SLOW_AFTER = 20

function stopHealthPolling() {
  if (healthTimer) clearInterval(healthTimer)
  healthTimer = null
}

function scheduleHealthPoll() {
  if (!healthTimer) return
  clearInterval(healthTimer)
  healthTimer = setInterval(pollHealth, healthDelay)
}

function pollHealth() {
  api
    .health()
    .then((h) => {
      initStatus.value = h.status || 'ok'
      initError.value = h.error || ''
      initProgress.value = h.progress || 0
      initStep.value = h.step || ''
      // 就绪：结束轮询
      if (initStatus.value === 'ok') {
        stopHealthPolling()
        return
      }
      // 初始化失败：继续轮询没有意义（/api/health 始终返回 200，不会触发 catch），
      // 旧实现在这种情况下会每 3 秒请求一次、永不停止。
      if (initStatus.value === 'error') {
        stopHealthPolling()
        return
      }
      healthTicks += 1
      if (healthTicks > HEALTH_SLOW_AFTER) {
        healthDelay = HEALTH_SLOW_MS
        scheduleHealthPoll()
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
  autoScroll,
  scrollToBottom,
  sendMessage,
  stopChat,
  setMessages,
  onChatScroll,
} = useChat(mode)

// ── 多对话 ──
// 消息由 useChat 拥有，这里只通过 onOpen 把某个对话的历史灌进去
const {
  conversations,
  currentId,
  current,
  loading: convLoading,
  listError,
  hasOverride,
  overrideFields,
  refresh: refreshConversations,
  reloadForMode,
  open: openConversation,
  create: createConversation,
  ensureId,
  rename: renameConversation,
  remove: removeConversation,
  setOverride,
} = useConversations(mode, { onOpen: (msgs) => setMessages(msgs) })

const {
  showSettings,
  savingSettings,
  configured,
  settings,
  convSettings,
  scope,
  overrideFields: settingsOverrideFields,
  settingsError,
  loadSettings,
  saveSettings,
  clearConversationOverride,
  fillConversationScope,
} = useSettings({
  onSaveConversation: (diff) => setOverride(diff),
  onClearConversation: () => setOverride(null),
})

// 弹窗里正在编辑哪一份草稿，取决于作用域
const activeSettings = computed(() => (scope.value === 'conversation' ? convSettings.value : settings.value))

/** 打开设置弹窗：默认改全局；已打开对话时可以在弹窗内切到"仅本对话"。 */
function openSettings() {
  scope.value = 'global'
  settingsError.value = ''
  fillConversationScope(current.value?.llm_effective, current.value?.llm_override)
  showSettings.value = true
}

/** 弹窗内切作用域：切到"仅本对话"时按当前生效配置重新预填。 */
function onScopeChange(next) {
  scope.value = next
  settingsError.value = ''
  if (next === 'conversation') {
    fillConversationScope(current.value?.llm_effective, current.value?.llm_override)
  }
}

async function selectConversation(id) {
  if (id === currentId.value) return
  // 正在生成时切换会中断任务：后端在流结束（含中断）时仍会把这一轮写进原对话
  if (sending.value) stopChat()
  selectedFiles.value = []
  await openConversation(id)
}

async function newConversation() {
  if (sending.value) stopChat()
  selectedFiles.value = []
  await createConversation()
  nextTick(() => textareaRef.value?.focus())
}

async function onDeleteConversation(id) {
  if (sending.value) stopChat()
  await removeConversation(id)
}

async function deleteCurrentConversation() {
  if (!currentId.value) return
  const title = current.value?.title || '当前对话'
  if (!window.confirm(`删除对话「${title}」？该对话的记录将不可恢复。`)) return
  await onDeleteConversation(currentId.value)
}

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
  try {
    // 懒创建：第一个问题才建对话，避免每次打开页面都留一个空对话
    const convId = await ensureId()
    selectedFiles.value = []
    await sendMessage(files, convId)
  } catch (e) {
    pushSystem(`无法创建对话：${e?.detail || e?.message || e}`)
    return
  }
  // 标题/更新时间/消息数都变了，刷新列表让侧栏跟上
  refreshConversations()
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

// 切换模式时，对话是按模式分开的：重新加载该模式的列表并打开最近一个
watch(mode, async () => {
  if (sending.value) stopChat()
  selectedFiles.value = []
  await reloadForMode()
})

onMounted(async () => {
  if (window.matchMedia?.('(max-width: 768px)').matches) {
    filesCollapsed.value = true
    convCollapsed.value = true
  }
  pollHealth()
  healthTimer = setInterval(pollHealth, healthDelay)
  loadSettings()
  // 恢复上次的对话：列表最新的一个（按 updated_at 排序）
  await refreshConversations()
  if (conversations.value.length) await openConversation(conversations.value[0].id)
})

onUnmounted(() => {
  stopHealthPolling()
})
</script>

<template>
  <div class="app">
    <!-- ── 顶栏 ── -->
    <header class="header">
      <div class="header-inner">
        <div class="brand">
          <span class="logo">🎬 FFmpeg Agent</span>
          <span class="subtitle">{{ mode === 'ffprobe' ? '自然语言 → FFprobe 分析' : '自然语言 → FFmpeg 命令' }}</span>
        </div>
        <div class="header-spacer" />
        <div class="mode-switch" role="tablist" aria-label="工作模式">
          <button
            class="mode-btn"
            :class="{ active: mode === 'ffmpeg' }"
            role="tab"
            :aria-selected="mode === 'ffmpeg'"
            @click="mode = 'ffmpeg'"
          ><span class="mode-dot" />FFmpeg 处理</button>
          <button
            class="mode-btn"
            :class="{ active: mode === 'ffprobe' }"
            role="tab"
            :aria-selected="mode === 'ffprobe'"
            @click="mode = 'ffprobe'"
          ><span class="mode-dot" />FFprobe 分析</button>
        </div>
        <button
          class="icon-btn"
          :class="{ active: !convCollapsed }"
          title="切换对话列表"
          aria-label="切换对话列表"
          @click="convCollapsed = !convCollapsed"
        >💬</button>
        <button
          class="icon-btn"
          :class="{ active: !filesCollapsed }"
          title="切换文件列表"
          aria-label="切换文件列表"
          @click="filesCollapsed = !filesCollapsed"
        >📁</button>
        <button
          class="icon-btn"
          title="删除当前对话"
          aria-label="删除当前对话"
          :disabled="!currentId"
          @click="deleteCurrentConversation"
        >🗑️</button>
        <button class="icon-btn" title="LLM 设置（可只对当前对话生效）" aria-label="LLM 设置" @click="openSettings">
          ⚙️
          <span
            v-if="configured !== null"
            class="status-dot"
            :class="configured ? 'on' : 'off'"
            :title="configured ? 'LLM 已配置' : 'LLM 未配置'"
          />
        </button>
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
      :settings="activeSettings"
      :scope="scope"
      :can-use-conversation-scope="!!currentId"
      :override-fields="settingsOverrideFields"
      :configured="configured"
      :saving="savingSettings"
      :error="settingsError"
      @update:scope="onScopeChange"
      @save="saveSettings"
      @clear-override="clearConversationOverride"
    />

    <!-- ── 三栏主体：对话列表 | 对话区 | 文件面板 ── -->
    <div class="body">
      <ConversationList
        :conversations="conversations"
        :current-id="currentId"
        :loading="convLoading"
        :error="listError"
        :sending="sending"
        :class="{ collapsed: convCollapsed }"
        @select="selectConversation"
        @create="newConversation"
        @rename="renameConversation"
        @remove="onDeleteConversation"
      />

      <!-- ===== 中栏：对话界面 ===== -->
      <main class="right-panel">
        <!-- chat-area 把"回到最新"锚定在对话视口内：锚到右栏会在出现
             已选文件栏时压住它（底栏高度会变） -->
        <div class="chat-area">
          <div ref="chatContainer" class="chat-messages" role="log" aria-label="对话记录" @scroll="onChatScroll">
            <div v-if="!messages.length" class="empty-chat">
              <div class="empty-icon">{{ mode === 'ffprobe' ? '🔍' : '💬' }}</div>
              <p class="empty-title">{{ mode === 'ffprobe' ? '想查看这个文件的哪些信息？' : '想对这个文件做什么？' }}</p>
              <p class="empty-hint">
                在左侧点击 <b>＋</b> 把文件加入工作区，再用自然语言描述需求；
                不选文件也可以直接提问 FFmpeg / FFprobe 知识。
              </p>
              <p class="examples">
                <template v-if="mode === 'ffprobe'">
                  <button class="example-chip" @click="useExample('查看视频的分辨率和编码')">
                    查看视频的分辨率和编码<span class="chip-arrow">→</span>
                  </button>
                  <button class="example-chip" @click="useExample('查看音频采样率')">
                    查看音频采样率<span class="chip-arrow">→</span>
                  </button>
                  <button class="example-chip" @click="useExample('列出所有流的详细信息')">
                    列出所有流的详细信息<span class="chip-arrow">→</span>
                  </button>
                </template>
                <template v-else>
                  <button class="example-chip" @click="useExample('把图片反色')">
                    把图片反色<span class="chip-arrow">→</span>
                  </button>
                  <button class="example-chip" @click="useExample('转成 mp4')">
                    转成 mp4<span class="chip-arrow">→</span>
                  </button>
                  <button class="example-chip" @click="useExample('裁剪中间 10 秒')">
                    裁剪中间 10 秒<span class="chip-arrow">→</span>
                  </button>
                  <button class="example-chip" @click="useExample('提取音频为 mp3')">
                    提取音频为 mp3<span class="chip-arrow">→</span>
                  </button>
                  <button class="example-chip" @click="useExample('缩放到 1280x720')">
                    缩放到 1280x720<span class="chip-arrow">→</span>
                  </button>
                </template>
              </p>
            </div>

            <MessageItem v-for="(msg, i) in messages" :key="i" :msg="msg" />
          </div>

          <!-- 用户往上翻看历史时，新内容仍在流入：给一个明确的"回到最新"入口 -->
          <button
            v-if="messages.length && !autoScroll"
            class="jump-btn"
            type="button"
            @click="scrollToBottom('smooth')"
          >↓ 回到最新</button>
        </div>

        <!-- ── 选中文件栏 + 输入栏 ── -->
        <SelectedFilesBar
          v-if="hasFiles"
          :files="selectedFiles"
          @remove="onChipRemove"
          @add="filePanel?.triggerUpload()"
        />
        <footer class="input-bar">
          <div class="input-shell">
            <textarea
              ref="textareaRef"
              v-model="question"
              :placeholder="mode === 'ffprobe' ? '输入你想查看的文件信息…' : '输入你对文件的处理需求…'"
              rows="1"
              :disabled="sending"
              aria-label="输入需求"
              @keydown="onKeydown"
              @input="autoResize"
            />
            <button v-if="!sending" class="send-btn" :disabled="!canSend" @click="onSend">发送</button>
            <button v-else class="send-btn stop" @click="stopChat">
              <span class="stop-icon" aria-hidden="true" />停止
            </button>
          </div>
          <div class="input-hint">
            <kbd>Enter</kbd> 发送 · <kbd>Shift</kbd>+<kbd>Enter</kbd> 换行
            <span v-if="current" class="hint-conv" :title="current.title">
              · 当前对话：{{ current.title }}
              <template v-if="hasOverride">（模型：{{ current.llm_override?.model || '自定义' }}）</template>
            </span>
          </div>
        </footer>
      </main>

      <FilePanel
        ref="filePanel"
        :selected-files="selectedFiles"
        :class="{ collapsed: filesCollapsed }"
        @notify="pushSystem"
        @select-output="addToWorkspace"
        @removed="onFileRemoved"
      />
    </div>
  </div>
</template>

<style>
/* ── Reset & Base ── */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html, body { height: 100%; }
body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans SC', 'PingFang SC', 'Microsoft YaHei', sans-serif;
  background: var(--dsh-bg);
  color: var(--dsh-text);
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}
#app { height: 100%; }
/* 只给正文里的链接加样式，避免影响用户气泡等自定义底色区域 */
a { color: var(--dsh-brand); text-decoration: none; }
a:hover { text-decoration: underline; }
button { font-family: inherit; }

/* ── App Layout ── */
.app {
  display: flex;
  flex-direction: column;
  height: 100vh;
}

/* ── Header ── */
.header {
  padding: 12px 20px;
  background: var(--dsh-surface);
  border-bottom: 1px solid var(--dsh-border);
  flex-shrink: 0;
  position: relative;
  z-index: 2;
}
.header-inner {
  display: flex;
  align-items: center;
  gap: 12px;
}
.brand { display: flex; align-items: baseline; gap: 10px; min-width: 0; }
.logo {
  font-size: 17px;
  font-weight: 700;
  color: var(--dsh-text);
  white-space: nowrap;
  letter-spacing: -0.01em;
}
.subtitle {
  font-size: var(--dsh-fs-base);
  color: var(--dsh-text-4);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.header-spacer { flex: 1; }

/* ── Header Controls ── */
.mode-switch {
  display: flex;
  background: var(--dsh-surface-3);
  border-radius: var(--dsh-r-md);
  padding: 3px;
  gap: 2px;
}
.mode-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  background: none;
  border: none;
  border-radius: var(--dsh-r-sm);
  padding: 6px 13px;
  font-size: var(--dsh-fs-base);
  font-weight: 500;
  color: var(--dsh-text-3);
  cursor: pointer;
  transition: color var(--dsh-dur) var(--dsh-ease), background var(--dsh-dur) var(--dsh-ease);
  white-space: nowrap;
}
.mode-btn:hover { color: var(--dsh-text-2); }
.mode-btn.active {
  background: var(--dsh-surface);
  color: var(--dsh-brand);
  box-shadow: var(--dsh-shadow-sm);
}
.mode-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  opacity: 0.55;
  flex-shrink: 0;
}
.mode-btn.active .mode-dot { opacity: 1; }

.icon-btn {
  position: relative;
  background: none;
  border: 1px solid var(--dsh-border);
  border-radius: var(--dsh-r-sm);
  cursor: pointer;
  font-size: 15px;
  padding: 5px 10px;
  line-height: 1.2;
  color: var(--dsh-text-2);
  transition: background var(--dsh-dur) var(--dsh-ease), border-color var(--dsh-dur) var(--dsh-ease);
}
.icon-btn:hover { background: var(--dsh-surface-3); border-color: var(--dsh-border-strong); }
.icon-btn.active { background: var(--dsh-brand-soft); border-color: var(--dsh-brand-line); }
/* 已配置 LLM 时在齿轮上点一个小绿点，省得用户反复打开设置确认 */
.icon-btn .status-dot {
  position: absolute;
  top: -3px;
  right: -3px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  border: 1.5px solid var(--dsh-surface);
}
.status-dot.on { background: var(--dsh-success); }
.status-dot.off { background: var(--dsh-danger); }

/* ── Body (two-column) ── */
.body {
  flex: 1;
  display: flex;
  overflow: hidden;
  min-height: 0;
}

/* 左侧对话栏的折叠规则在 ConversationList.vue 里（scoped）；
   文件面板在中栏之后，往右折叠 */
.file-panel { transition: margin-right 0.25s var(--dsh-ease); }
.file-panel.collapsed { margin-right: -341px; }

/* ===== Right Panel ===== */
.right-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-width: 0;
}

/* 对话视口：包裹消息区 + 浮层按钮，让按钮的定位基准不受底栏高度影响 */
.chat-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-height: 0;
  position: relative;
}

/* ── Chat Messages ── */
.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 20px 24px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.chat-messages::-webkit-scrollbar { width: 8px; }
.chat-messages::-webkit-scrollbar-thumb {
  background: var(--dsh-scroll-thumb);
  border-radius: var(--dsh-r-pill);
  border: 2px solid transparent;
  background-clip: content-box;
}
.chat-messages::-webkit-scrollbar-thumb:hover {
  background: var(--dsh-scroll-thumb-hover);
  background-clip: content-box;
}

/* ── 回到最新 ── */
.jump-btn {
  position: absolute;
  left: 50%;
  bottom: 14px;
  transform: translateX(-50%);
  z-index: 3;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 6px 14px;
  border: 1px solid var(--dsh-border);
  border-radius: var(--dsh-r-pill);
  background: var(--dsh-surface);
  color: var(--dsh-text-2);
  font-size: var(--dsh-fs-base);
  font-weight: 500;
  cursor: pointer;
  box-shadow: var(--dsh-shadow-md);
  animation: jump-in 0.18s var(--dsh-ease);
}
.jump-btn:hover { color: var(--dsh-brand); border-color: var(--dsh-brand-line); }
@keyframes jump-in {
  from { opacity: 0; transform: translate(-50%, 6px); }
  to { opacity: 1; transform: translate(-50%, 0); }
}

.empty-chat {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  gap: 6px;
  padding-bottom: 24px;
}
.empty-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 56px;
  height: 56px;
  border-radius: var(--dsh-r-xl);
  background: var(--dsh-surface);
  border: 1px solid var(--dsh-border);
  box-shadow: var(--dsh-shadow-sm);
  font-size: 26px;
  margin-bottom: 14px;
}
.empty-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--dsh-text);
  letter-spacing: -0.01em;
}
.empty-hint {
  font-size: var(--dsh-fs-base);
  color: var(--dsh-text-4);
  max-width: 460px;
  line-height: 1.6;
}
.examples {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
  margin-top: 20px;
  max-width: 720px;
}
.example-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--dsh-border);
  background: var(--dsh-surface);
  color: var(--dsh-text-2);
  border-radius: var(--dsh-r-pill);
  padding: 7px 15px;
  font-size: var(--dsh-fs-base);
  cursor: pointer;
  box-shadow: var(--dsh-shadow-xs);
  transition: all var(--dsh-dur) var(--dsh-ease);
}
.example-chip:hover {
  border-color: var(--dsh-brand-line);
  background: var(--dsh-brand-soft);
  color: var(--dsh-brand);
  box-shadow: var(--dsh-shadow-sm);
  transform: translateY(-1px);
}
.example-chip:active { transform: translateY(0); }
.example-chip .chip-arrow { opacity: 0.45; font-size: 11px; }
.example-chip:hover .chip-arrow { opacity: 1; }

/* ── Input Bar ── */
.input-bar {
  flex-shrink: 0;
  padding: 12px 20px 16px;
  background: var(--dsh-bg);
}
.input-shell {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  background: var(--dsh-surface);
  border: 1px solid var(--dsh-border-strong);
  border-radius: var(--dsh-r-lg);
  padding: 8px 8px 8px 14px;
  box-shadow: var(--dsh-shadow-sm);
  transition: border-color var(--dsh-dur) var(--dsh-ease), box-shadow var(--dsh-dur) var(--dsh-ease);
}
.input-shell:focus-within {
  border-color: var(--dsh-brand);
  box-shadow: var(--dsh-shadow-md), var(--dsh-ring);
}
.input-bar textarea {
  flex: 1;
  border: none;
  padding: 6px 0;
  font-size: var(--dsh-fs-md);
  font-family: inherit;
  line-height: 1.5;
  resize: none;
  outline: none;
  overflow: hidden;
  scrollbar-width: none;
  background: transparent;
  color: var(--dsh-text);
  max-height: 168px;
}
.input-bar textarea::placeholder { color: var(--dsh-text-4); }
.input-bar textarea::-webkit-scrollbar { display: none; }
.input-bar textarea:disabled { opacity: 0.6; }

.send-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 34px;
  padding: 0 16px;
  border: none;
  border-radius: var(--dsh-r-md);
  background: var(--dsh-brand);
  color: var(--dsh-text-invert);
  font-size: var(--dsh-fs-md);
  font-weight: 500;
  cursor: pointer;
  flex-shrink: 0;
  box-shadow: var(--dsh-shadow-brand);
  transition: all var(--dsh-dur) var(--dsh-ease);
}
.send-btn:hover:not(:disabled) { background: var(--dsh-brand-strong); }
.send-btn:active:not(:disabled) { transform: translateY(1px); }
.send-btn:disabled {
  background: var(--dsh-surface-3);
  color: var(--dsh-text-4);
  box-shadow: none;
  cursor: not-allowed;
}
.send-btn.stop {
  background: var(--dsh-danger);
  box-shadow: var(--dsh-shadow-danger);
}
.send-btn.stop:hover { background: var(--dsh-danger-strong); }
/* 用 CSS 方块代替 ⏹ emoji：emoji 在不同平台粗细/大小不一 */
.stop-icon {
  width: 9px;
  height: 9px;
  border-radius: 2px;
  background: currentColor;
  flex-shrink: 0;
}

.input-hint {
  margin-top: 7px;
  padding-left: 2px;
  font-size: var(--dsh-fs-sm);
  color: var(--dsh-text-4);
}
.input-hint kbd {
  font-family: inherit;
  font-size: var(--dsh-fs-xs);
  background: var(--dsh-surface);
  border: 1px solid var(--dsh-border);
  border-bottom-width: 2px;
  border-radius: var(--dsh-r-xs);
  padding: 0 5px;
  color: var(--dsh-text-3);
}
/* 当前所在对话：对话多了以后，光看中间区域容易忘记自己在哪一条 */
.hint-conv {
  margin-left: 4px;
  color: var(--dsh-text-3);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 46ch;
  display: inline-block;
  vertical-align: bottom;
}

/* ── Keyframes ── */
@keyframes spin { to { transform: rotate(360deg); } }

/* ── Init Banner ── */
.init-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 20px;
  background: var(--dsh-warn-soft);
  color: var(--dsh-warn);
  border-bottom: 1px solid var(--dsh-warn-line);
  font-size: var(--dsh-fs-base);
  flex-shrink: 0;
}
.init-banner.error {
  background: var(--dsh-danger-soft);
  color: var(--dsh-danger-strong);
  border-bottom-color: var(--dsh-danger-line);
  white-space: pre-wrap;
  word-break: break-all;
}
.init-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid var(--dsh-warn-accent);
  border-top-color: transparent;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  flex-shrink: 0;
}
.init-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 5px; }
.init-text { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.init-pct { font-variant-numeric: tabular-nums; flex-shrink: 0; font-weight: 600; }
.init-bar {
  height: 5px;
  background: var(--dsh-warn-line);
  border-radius: var(--dsh-r-pill);
  overflow: hidden;
}
.init-fill {
  height: 100%;
  background: var(--dsh-warn-accent);
  border-radius: var(--dsh-r-pill);
  transition: width 0.4s var(--dsh-ease);
}

/* ── Responsive ── */
@media (max-width: 900px) {
  .subtitle { display: none; }
}
@media (max-width: 640px) {
  .header { padding: 10px 12px; }
  .logo { font-size: 15px; }
  .chat-messages { padding: 14px 12px; }
  .input-bar { padding: 10px 12px 12px; }
  .input-hint { display: none; }
  .mode-btn { padding: 6px 10px; }
}
</style>
