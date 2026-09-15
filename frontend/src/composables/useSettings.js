import { ref, computed } from 'vue'
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

function draftFrom(cfg) {
  const src = cfg || {}
  return {
    model: src.model || '',
    base_url: src.base_url || '',
    api_key: src.key_configured ? (src.api_key || '') : '',
    temperature: src.temperature ?? 0.2,
    max_tokens: src.max_tokens ?? 2048,
  }
}

/**
 * LLM 设置：**两个正交的维度**
 *
 *   作用域 scope：global（全局默认，写 llm_settings.json）
 *                conversation（只覆盖当前对话）
 *   角色   role ：text（主模型：写命令/回答）
 *                vision（视觉模型：看画面/波形）
 *
 * 角色刻意分开的原因见后端 model.py：文本模型常常不收图，视觉模型写命令又未必更强，
 * 混着用会在两处都出错。这里也保持一致——两个角色各有各的草稿，互不写入。
 */
export function useSettings({ onSaveConversation, onClearConversation } = {}) {
  const showSettings = ref(false)
  const savingSettings = ref(false)
  const configured = ref(null) // null=未知, true/false
  const settingsError = ref('')
  const scope = ref('global')
  const role = ref('text')

  const settings = ref({ ...blankDraft(), auth_token: '' }) // 全局主模型
  const convSettings = ref(blankDraft())                    // 对话级主模型
  const vision = ref(blankDraft())                          // 全局视觉模型
  const convVision = ref(blankDraft())                      // 对话级视觉模型

  // 是否"单独配置视觉模型"。关掉 = 画面分析回退主模型（后端 vision=null）
  const visionEnabled = ref(false)
  const convVisionEnabled = ref(false)

  // 全局配置快照，用于算"哪些字段和全局不同"
  const globalSnapshot = ref(blankDraft())
  const globalVisionSnapshot = ref(blankDraft())
  const globalVisionSeparate = ref(false)

  // 对话已覆盖的字段名（用于在弹窗里提示"哪些不再继承全局"）
  const overrideFields = ref([])
  const visionOverrideFields = ref([])

  const isVision = computed(() => role.value === 'vision')
  const isConversation = computed(() => scope.value === 'conversation')
  // 当前正在编辑的草稿：四个组合各一份
  const activeSettings = computed(() => {
    if (isConversation.value) return isVision.value ? convVision.value : convSettings.value
    return isVision.value ? vision.value : settings.value
  })
  const visionToggle = computed({
    get: () => (isConversation.value ? convVisionEnabled.value : visionEnabled.value),
    set: (v) => { if (isConversation.value) convVisionEnabled.value = v; else visionEnabled.value = v },
  })

  /** 画面分析当前实际会用哪个模型（给用户一句直白的说明）。 */
  const visionSummary = computed(() => {
    if (isConversation.value) {
      if (convVisionEnabled.value) return `本对话使用视觉模型：${convVision.value.model || '（未填）'}`
      return '本对话未单独指定视觉模型，跟随全局设置'
    }
    if (visionEnabled.value) return `画面分析使用视觉模型：${vision.value.model || '（未填）'}`
    return `画面分析回退主模型：${settings.value.model || '（未填）'}`
  })

  async function loadSettings() {
    try {
      const data = await api.getSettings()
      settings.value = { ...settings.value, ...data }
      settings.value.auth_token = getToken()
      // 服务端只返回脱敏 key,无 key 时回显为空
      if (!data.key_configured) settings.value.api_key = ''
      configured.value = data.configured === true
      globalSnapshot.value = draftFrom({ ...data, key_configured: data.key_configured })

      const v = data.vision || {}
      visionEnabled.value = v.separate === true
      vision.value = draftFrom(v)
      globalVisionSnapshot.value = draftFrom(v)
      globalVisionSeparate.value = v.separate === true

      if (!configured.value) {
        scope.value = 'global'
        role.value = 'text'
        showSettings.value = true
      }
    } catch (e) {
      console.error('加载设置失败:', e)
      configured.value = false
    }
  }

  /** 切到"仅本对话"时用当前**实际生效**的配置预填，用户改哪项就只覆盖哪项。 */
  function fillConversationScope(textEffective, textOverride, visionEffective, visionOverride) {
    convSettings.value = draftFrom(textEffective)
    overrideFields.value = Object.keys(textOverride || {})
    convVisionEnabled.value = !!visionOverride
    convVision.value = draftFrom(visionOverride || visionEffective)
    visionOverrideFields.value = Object.keys(visionOverride || {})
  }

  /** 与全局快照比较，得出"要写进覆盖的字段"。只提交差异 = 其余字段继续继承全局。 */
  function diff(snapshot, draft) {
    const out = {}
    if ((draft.model || '') !== (snapshot.model || '')) out.model = (draft.model || '').trim()
    if ((draft.base_url || '') !== (snapshot.base_url || '')) out.base_url = (draft.base_url || '').trim()
    // key：脱敏回显值与空值都表示"不改"，只有全新输入的明文才算覆盖
    if (draft.api_key && !isMasked(draft.api_key)) out.api_key = String(draft.api_key).trim()
    if (Number(draft.temperature) !== Number(snapshot.temperature)) out.temperature = Number(draft.temperature)
    if (Number(draft.max_tokens) !== Number(snapshot.max_tokens)) out.max_tokens = Number(draft.max_tokens)
    return out
  }

  async function saveSettings() {
    savingSettings.value = true
    settingsError.value = ''
    try {
      if (isConversation.value) {
        // 两个角色的草稿一起构成这份覆盖：只保存当前角色会把另一个角色冲掉
        const llm = diff(globalSnapshot.value, convSettings.value)
        if (convVisionEnabled.value) {
          // 视觉层只需模型名即可（接口地址/Key 留空表示沿用主模型），
          // 但仍然把它算作"已分离"，所以模型名为空要拦住。
          const vmodel = (convVision.value.model || '').trim()
          if (!vmodel) throw new Error('已启用视觉模型，但模型名称为空')
          const vpart = { model: vmodel }
          const vsnap = globalVisionSnapshot.value
          if ((convVision.value.base_url || '') !== (vsnap.base_url || '')) {
            vpart.base_url = (convVision.value.base_url || '').trim()
          }
          if (convVision.value.api_key && !isMasked(convVision.value.api_key)) {
            vpart.api_key = String(convVision.value.api_key).trim()
          }
          if (Number(convVision.value.temperature) !== Number(vsnap.temperature)) {
            vpart.temperature = Number(convVision.value.temperature)
          }
          if (Number(convVision.value.max_tokens) !== Number(vsnap.max_tokens)) {
            vpart.max_tokens = Number(convVision.value.max_tokens)
          }
          llm.vision = vpart
        }
        if (Object.keys(llm).length === 0) await onClearConversation?.()
        else await onSaveConversation?.(llm)
        showSettings.value = false
        return
      }

      if (isVision.value) {
        if (!visionEnabled.value) {
          // 关掉"单独配置" = 清空视觉层，画面分析回到跟随主模型
          const data = await api.putSettings({ vision: null })
          applyGlobalResponse(data)
          showSettings.value = false
          return
        }
        if (!(vision.value.model || '').trim()) throw new Error('已启用视觉模型，但模型名称为空')
        const body = { model: (vision.value.model || '').trim() }
        // 只有与主模型不同的连接字段才需要显式写下来，留空表示沿用主模型
        if ((vision.value.base_url || '') !== (globalSnapshot.value.base_url || '')) {
          body.base_url = (vision.value.base_url || '').trim()
        }
        if (vision.value.api_key && !isMasked(vision.value.api_key)) {
          body.api_key = String(vision.value.api_key).trim()
        }
        if (Number(vision.value.temperature) !== Number(globalVisionSnapshot.value.temperature)) {
          body.temperature = Number(vision.value.temperature)
        }
        if (Number(vision.value.max_tokens) !== Number(globalVisionSnapshot.value.max_tokens)) {
          body.max_tokens = Number(vision.value.max_tokens)
        }
        const data = await api.putSettings({ vision: body })
        applyGlobalResponse(data)
        showSettings.value = false
        return
      }

      setToken(settings.value.auth_token || '')
      const { auth_token, ...body } = settings.value
      // 保存时后端会顺带做一次连通性检查；失败会返回 400 + 可读原因，
      // 这里把它显示出来而不是静默吞掉。
      const data = await api.putSettings(body)
      settings.value = { ...settings.value, ...data, auth_token }
      applyGlobalResponse(data)
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

  function applyGlobalResponse(data) {
    configured.value = data.configured === true
    globalSnapshot.value = draftFrom(data)
    const v = data.vision || {}
    visionEnabled.value = v.separate === true
    globalVisionSeparate.value = v.separate === true
    globalVisionSnapshot.value = draftFrom(v)
    if (v.separate) {
      // 保存成功后把表单同步成服务端的结果（脱敏 key、实际生效值）
      vision.value = draftFrom(v)
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
    vision,
    convVision,
    scope,
    role,
    isVision,
    isConversation,
    activeSettings,
    visionToggle,
    visionEnabled,
    convVisionEnabled,
    visionSummary,
    overrideFields,
    visionOverrideFields,
    settingsError,
    loadSettings,
    saveSettings,
    clearConversationOverride,
    fillConversationScope,
  }
}
