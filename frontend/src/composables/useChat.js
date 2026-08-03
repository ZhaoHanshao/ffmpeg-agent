import { ref, reactive, computed, nextTick } from 'vue'
import { API_BASE } from '../api'

const NEAR_BOTTOM_THRESHOLD = 120

export function useChat(mode) {
  const messages = ref([])
  const question = ref('')
  const sending = ref(false)
  const chatContainer = ref(null)
  const autoScroll = ref(true)
  let controller = null
  let rafId = 0

  const canSend = computed(() => !sending.value && question.value.trim() !== '')

  function isNearBottom() {
    const el = chatContainer.value
    if (!el) return true
    return el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_THRESHOLD
  }

  function scrollToBottom(behavior = 'auto') {
    const el = chatContainer.value
    if (!el) return
    el.scrollTo({ top: el.scrollHeight, behavior })
  }

  // 流式期间用 rAF 节流滚动，避免每个 token 都触发平滑滚动动画
  function scheduleScroll() {
    if (rafId) return
    rafId = requestAnimationFrame(() => {
      rafId = 0
      if (autoScroll.value) scrollToBottom()
    })
  }

  function onChatScroll() {
    autoScroll.value = isNearBottom()
  }

  function clearMessages() {
    messages.value = []
    autoScroll.value = true
  }

  async function sendMessage(files = []) {
    const text = question.value.trim()
    if (!text || sending.value) return ''

    messages.value.push({ role: 'user', text })
    question.value = ''
    const reply = reactive({ role: 'ai', text: '', outputFile: '' })
    messages.value.push(reply)
    sending.value = true
    autoScroll.value = true
    nextTick(() => scrollToBottom('smooth'))

    const ac = new AbortController()
    controller = ac
    let lastStatus = ''
    const endpoint = mode.value === 'ffprobe' ? '/probe/chat' : '/chat'

    try {
      const form = new FormData()
      form.append('question', text)
      for (const f of files) form.append('files', f)
      const res = await fetch(`${API_BASE}${endpoint}`, {
        method: 'POST',
        body: form,
        signal: ac.signal,
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          const trimmed = line.trim()
          if (!trimmed.startsWith('data: ')) continue
          try {
            const data = JSON.parse(trimmed.slice(6))
            if (data.event === 'done') continue
            if (data.event === 'status') {
              lastStatus = data.text
              reply.text = `⏳ ${data.text}`
            } else if (data.event === 'token') {
              if (lastStatus) {
                reply.text = `${lastStatus}\n\n`
                lastStatus = ''
              }
              reply.text += data.text
            } else if (data.event === 'meta' && data.output_file) {
              reply.outputFile = data.output_file
            } else if (data.event === 'error') {
              reply.text += `\n[错误] ${data.text}`
            }
          } catch {
            // 不完整的 JSON，等待下一个数据块补全
          }
        }
        scheduleScroll()
      }
    } catch (e) {
      if (e.name !== 'AbortError') reply.text = `请求失败: ${e.message}`
    } finally {
      if (ac.signal.aborted && reply.text) reply.text += '\n\n⏹ 已停止'
      sending.value = false
      controller = null
      cancelAnimationFrame(rafId)
      rafId = 0
      scrollToBottom('smooth')
    }

    return reply.outputFile
  }

  function stopChat() {
    controller?.abort()
  }

  return {
    messages,
    question,
    sending,
    canSend,
    chatContainer,
    sendMessage,
    stopChat,
    clearMessages,
    onChatScroll,
  }
}
