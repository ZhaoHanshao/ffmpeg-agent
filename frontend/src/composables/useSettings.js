import { ref } from 'vue'
import { api, getToken, setToken } from '../api'

export function useSettings() {
  const showSettings = ref(false)
  const savingSettings = ref(false)
  const configured = ref(null) // null=未知, true/false
  // 保存/加载失败的原因（如"API Key 无效"），直接展示在弹窗里。
  // 此前只 console.error，用户点了保存却看不到任何反馈。
  const settingsError = ref('')
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
      if (!configured.value) showSettings.value = true
    } catch (e) {
      console.error('加载设置失败:', e)
      configured.value = false
    }
  }

  async function saveSettings() {
    savingSettings.value = true
    settingsError.value = ''
    try {
      setToken(settings.value.auth_token || '')
      const { auth_token, ...body } = settings.value
      // 保存时后端会顺带做一次连通性检查；失败会返回 400 + 可读原因，
      // 这里把它显示出来而不是静默吞掉。
      const data = await api.putSettings(body)
      settings.value = { ...settings.value, ...data, auth_token }
      configured.value = data.configured === true
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

  return {
    showSettings,
    savingSettings,
    configured,
    settings,
    settingsError,
    loadSettings,
    saveSettings,
  }
}
