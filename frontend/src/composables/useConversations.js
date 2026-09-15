import { ref, computed } from 'vue'
import { api } from '../api'

/** 服务端消息 → 前端消息结构。
 *
 * 服务端用 snake_case 且只存"结果"，前端 MessageItem 还需要 pending/streaming 这类
 * 运行时字段；映射集中在这里，避免各组件各写一份。
 */
export function fromServer(rec) {
  return {
    role: rec.role === 'user' ? 'user' : rec.role === 'system' ? 'system' : 'ai',
    text: rec.text || '',
    files: rec.files || [],
    outputFile: rec.output_file || '',
    error: rec.error || '',
    stage: '',
    pending: false,
    streaming: false,
    ts: rec.ts || 0,
  }
}

/** 消息里引用的文件引用（{name, src}）转成后端要的 "upload:x" / "download:x"。 */
export function toServerRef(f) {
  return `${f.src === 'output' ? 'download' : 'upload'}:${f.name}`
}

/**
 * 多对话状态：列表、当前对话、模型覆盖。
 *
 * 为什么消息不放在这里：消息由 useChat 拥有（流式写入要改它），
 * 这里只负责"对话这条记录"以及和它的接口交互，两边通过 App.vue 的
 * onOpen 回调对接，避免两个 composable 互相持有对方的状态。
 */
export function useConversations(mode, { onOpen } = {}) {
  const conversations = ref([])
  const currentId = ref('')
  const current = ref(null)
  const loading = ref(false)
  const listError = ref('')

  const hasOverride = computed(() => !!current.value?.llm_override)
  const overrideFields = computed(() => Object.keys(current.value?.llm_override || {}))

  /** 打开某个对话：拉全量记录（含消息）并交给上层渲染。 */
  async function open(id) {
    if (!id) {
      currentId.value = ''
      current.value = null
      onOpen?.([])
      return
    }
    loading.value = true
    listError.value = ''
    try {
      const data = await api.getConversation(id)
      currentId.value = data.id
      current.value = data
      onOpen?.((data.messages || []).map(fromServer))
    } catch (e) {
      listError.value = e?.detail || e?.message || '打开对话失败'
      // 记录已被删除（比如在另一个标签页删的）：退回空状态并刷新列表
      currentId.value = ''
      current.value = null
      onOpen?.([])
      await refresh()
    } finally {
      loading.value = false
    }
  }

  async function refresh() {
    try {
      const data = await api.listConversations(mode.value)
      conversations.value = data.conversations || []
      listError.value = ''
    } catch (e) {
      listError.value = e?.detail || e?.message || '加载对话列表失败'
      return
    }
    // 当前对话不在列表里（被删掉，或切模式后不属于该模式）
    if (currentId.value && !conversations.value.some((c) => c.id === currentId.value)) {
      currentId.value = ''
      current.value = null
      onOpen?.([])
    }
  }

  async function create() {
    const data = await api.createConversation(mode.value)
    await refresh()
    await open(data.id)
    return data.id
  }

  /** 发送前用：没有当前对话就新建一个（懒创建，避免每次打开页面都留下空对话）。 */
  async function ensureId() {
    if (currentId.value) return currentId.value
    return create()
  }

  async function rename(id, title) {
    const data = await api.updateConversation(id, { title })
    if (currentId.value === id) current.value = data
    await refresh()
  }

  async function remove(id) {
    await api.deleteConversation(id)
    if (currentId.value === id) {
      currentId.value = ''
      current.value = null
      onOpen?.([])
    }
    await refresh()
  }

  /** 设置/清除本对话的模型覆盖。llm 传 null 表示恢复继承全局。 */
  async function setOverride(llm) {
    if (!currentId.value) throw new Error('当前没有打开任何对话')
    const data = await api.updateConversation(currentId.value, { llm })
    current.value = data
    await refresh()
    return data
  }

  /** 切模式后重新加载该模式下的对话列表。 */
  async function reloadForMode() {
    await refresh()
    if (!currentId.value && conversations.value.length) {
      await open(conversations.value[0].id)
    }
  }

  return {
    conversations,
    currentId,
    current,
    loading,
    listError,
    hasOverride,
    overrideFields,
    refresh,
    reloadForMode,
    open,
    create,
    ensureId,
    rename,
    remove,
    setOverride,
  }
}
