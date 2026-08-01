import { ref } from 'vue'
import { api } from '../api'

export function useSettings() {
  const showSettings = ref(false)
  const savingSettings = ref(false)
  const configured = ref(null) // null=未知, true/false
  const settings = ref({
    model: '',
    base_url: '',
    api_key: '',
    temperature: 0.2,
    max_tokens: 2048,
  })

  async function loadSettings() {
    try {
      const data = await api.getSettings()
      settings.value = { ...settings.value, ...data }
      configured.value = data.configured === true
      if (!configured.value) showSettings.value = true
    } catch (e) {
      console.error('加载设置失败:', e)
      configured.value = false
    }
  }

  async function saveSettings() {
    savingSettings.value = true
    try {
      const data = await api.putSettings(settings.value)
      settings.value = { ...settings.value, ...data }
      configured.value = data.configured === true
      if (configured.value) showSettings.value = false
    } catch (e) {
      console.error('保存设置失败:', e)
    } finally {
      savingSettings.value = false
    }
  }

  return { showSettings, savingSettings, configured, settings, loadSettings, saveSettings }
}
