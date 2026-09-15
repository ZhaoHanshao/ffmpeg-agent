import { ref } from 'vue'
import { api, getToken, setToken } from '../api'

/** 服务端脱敏后的 key 形如 `sk-****abcd`：回显值不能被当成"用户新填的 key"提交。 */
function isMasked(v) {
  return /\*{4}/.test(String(v || ''))
}

function blankDraft() {
  return {
    model: '',
    base_url: '',
    api_key: '',
    temperature: 0.2,
    max_tokens: 2048,
  }
}

/**
 * LLM 设置，两种作用域：
 * - `global`：写入 backend/data/llm_settings.json，是所有对话的默认值
 * - `conversation`：只覆盖当前对话（后端 PATCH /api/conversations/{id}）
 *
 * 对话级保存走"只提交与全局不同的字段"的策略：这样没被改动的字段继续继承全局，
 * 全局改了模型，本对话也跟着变——如果全量提交，本对话会被永久钉死在保存那一刻的值。
 */
export function useSettings({ onSaveConversation, onClearConversation } = {}) {
  const showSettings = ref(false)
  const savingSettings = ref(false)
  const configured = ref(null) // null=未知, true/false
  // 保存/加载失败的原因（如"API Key 无效"），直接展示在弹窗里。
  // 此前只 console.error，用户点了保存却看不到任何反馈。
  const settingsError = ref('')
  const scope = ref('global')
  // 对话级草稿（与全局草稿分开存，切换作用域不会互相覆盖）
  const convSettings = ref(blankDraft())
  // 本对话已经覆盖了哪些字段（用于在弹窗里提示"哪些不再继承全局"）
  const overrideFields = ref([])
  // 全局配置的快照，用于计算"哪些字段和全局不同"
  const globalSnapshot = ref(blankDraft())

  const settings = ref({
    model: '',
    base_url: '',
    api_key: '',
    auth_token: '',
    temperature: 0.2,
    max_tokens: 2048,
  })

  async function loadSettings() {
    try {
      const data = await api.getSettings()
      settings.value = { ...settings.value, ...data }
      settings.value.auth_token = getToken()
      // 服务端只返回脱敏 key,无 key 时回显为空
      if (!data.key_configured) settings.value.api_key = ''
      configured.value = data.configured === true
      globalSnapshot.value = {
        model: data.model || '',
        base_url: data.base_url || '',
        api_key: data.key_configured ? (data.api_key || '') : '',
        temperature: data.temperature ?? 0.2,
        max_tokens: data.max_tokens ?? 2048,
      }
      if (!configured.value) {
        scope.value = 'global'
        showSettings.value = true
      }
    } catch (e) {
      console.error('加载设置失败:', e)
      configured.value = false
    }
  }

  /** 切到"仅本对话"时用当前**实际生效**的配置预填，用户改哪项就只覆盖哪项。 */
  function fillConversationScope(effective, override) {
    const src = effective || {}
    convSettings.value = {
      model: src.model || '',
      base_url: src.base_url || '',
      api_key: src.key_configured ? (src.api_key || '') : '',
      temperature: src.temperature ?? 0.2,
      max_tokens: src.max_tokens ?? 2048,
    }
    overrideFields.value = Object.keys(override || {})
  }

  function diffAgainstGlobal() {
    const g = globalSnapshot.value
    const d = convSettings.value
    const diff = {}
    if ((d.model || '') !== (g.model || '')) diff.model = (d.model || '').trim()
    if ((d.base_url || '') !== (g.base_url || '')) diff.base_url = (d.base_url || '').trim()
    // key：脱敏回显值与空值都表示"不改"，只有全新输入的明文才算覆盖
    if (d.api_key && !isMasked(d.api_key)) diff.api_key = String(d.api_key).trim()
    if (Number(d.temperature) !== Number(g.temperature)) diff.temperature = Number(d.temperature)
    if (Number(d.max_tokens) !== Number(g.max_tokens)) diff.max_tokens = Number(d.max_tokens)
    return diff
  }

  async function saveSettings() {
    savingSettings.value = true
    settingsError.value = ''
    try {
      if (scope.value === 'conversation') {
        const diff = diffAgainstGlobal()
        // 全部字段都等于全局 = 没有覆盖，等价于"恢复继承全局"
        if (Object.keys(diff).length === 0) {
          await onClearConversation?.()
        } else {
          await onSaveConversation?.(diff)
        }
        showSettings.value = false
        return
      }
      setToken(settings.value.auth_token || '')
      const { auth_token, ...body } = settings.value
      // 保存时后端会顺带做一次连通性检查；失败会返回 400 + 可读原因，
      // 这里把它显示出来而不是静默吞掉。
      const data = await api.putSettings(body)
      settings.value = { ...settings.value, ...data, auth_token }
      configured.value = data.configured === true
      globalSnapshot.value = {
        model: data.model || '',
        base_url: data.base_url || '',
        api_key: data.key_configured ? (data.api_key || '') : '',
        temperature: data.temperature ?? 0.2,
        max_tokens: data.max_tokens ?? 2048,
      }
      if (configured.value) showSettings.value = false
    } catch (e) {
      settingsError.value = e?.detail || e?.message || '保存失败'
      console.error('保存设置失败:', e)
      // 保存失败时保持弹窗打开，让用户能看到原因并修正
      showSettings.value = true
    } finally {
      savingSettings.value = false
    }
  }

  /** 清除本对话的覆盖，恢复继承全局。 */
  async function clearConversationOverride() {
    savingSettings.value = true
    settingsError.value = ''
    try {
      await onClearConversation?.()
      showSettings.value = false
    } catch (e) {
      settingsError.value = e?.detail || e?.message || '清除失败'
    } finally {
      savingSettings.value = false
    }
  }

  return {
    showSettings,
    savingSettings,
    configured,
    settings,
    convSettings,
    scope,
    overrideFields,
    settingsError,
    loadSettings,
    saveSettings,
    clearConversationOverride,
    fillConversationScope,
  }
}
