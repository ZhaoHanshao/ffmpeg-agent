<script setup>
const settings = defineModel({ type: Object, required: true })
const show = defineModel('show', { type: Boolean, default: false })
defineProps({
  configured: { type: Boolean, default: false },
  saving: { type: Boolean, default: false },
})
const emit = defineEmits(['save'])
</script>

<template>
  <div
    v-if="show"
    class="modal-overlay"
    :class="{ mandatory: !configured }"
    @click.self="configured && (show = false)"
  >
    <div class="modal">
      <div class="modal-header">
        <span>LLM 设置</span>
        <button v-if="configured" class="modal-close" @click="show = false">&times;</button>
      </div>
      <div class="modal-body">
        <p v-if="!configured" class="settings-hint">请先填写 LLM 模型信息以开始使用</p>
        <label class="settings-field">
          <span>模型名称 (MODEL_NAME)</span>
          <input v-model="settings.model" placeholder="如 gpt-4o-mini" />
        </label>
        <label class="settings-field">
          <span>接口地址 (BASE_URL)</span>
          <input v-model="settings.base_url" placeholder="如 https://api.openai.com/v1" />
        </label>
        <label class="settings-field">
          <span>API Key (API_KEY)</span>
          <input v-model="settings.api_key" type="password" placeholder="sk-..." />
        </label>
        <label class="settings-field">
          <span>访问令牌 (AUTH_TOKEN, 服务端配置后必填)</span>
          <input v-model="settings.auth_token" type="password" placeholder="与后端 AUTH_TOKEN 一致,可选" />
        </label>
        <label class="settings-field">
          <span>Temperature (温度)</span>
          <input v-model.number="settings.temperature" type="number" step="0.1" min="0" max="2" />
        </label>
        <label class="settings-field">
          <span>Max Tokens (最大 token 数)</span>
          <input v-model.number="settings.max_tokens" type="number" step="1" min="1" />
        </label>
      </div>
      <div class="modal-footer">
        <button v-if="configured" class="btn-cancel" @click="show = false">取消</button>
        <button class="btn-save" :disabled="saving" @click="emit('save')">
          {{ saving ? '保存中…' : '保存' }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.modal-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: rgba(15, 23, 42, 0.42);
  backdrop-filter: blur(2px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
}
.modal {
  background: var(--dsh-surface);
  border-radius: var(--dsh-r-xl);
  width: 460px;
  max-width: 100%;
  max-height: 90vh;
  overflow-y: auto;
  box-shadow: var(--dsh-shadow-lg);
  display: flex;
  flex-direction: column;
}
.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid var(--dsh-border);
  font-size: var(--dsh-fs-lg);
  font-weight: 600;
  color: var(--dsh-text);
  position: sticky;
  top: 0;
  background: var(--dsh-surface);
  border-radius: var(--dsh-r-xl) var(--dsh-r-xl) 0 0;
}
.modal-close {
  background: none;
  border: none;
  font-size: 22px;
  cursor: pointer;
  color: var(--dsh-text-4);
  line-height: 1;
  border-radius: var(--dsh-r-xs);
  padding: 0 4px;
  transition: color var(--dsh-dur) var(--dsh-ease);
}
.modal-close:hover { color: var(--dsh-text-2); }
.modal-body { padding: 18px 20px; display: flex; flex-direction: column; gap: 13px; }
.settings-field {
  display: flex;
  flex-direction: column;
  gap: 5px;
  font-size: var(--dsh-fs-base);
  font-weight: 500;
  color: var(--dsh-text-2);
}
.settings-field .field-hint {
  font-size: var(--dsh-fs-sm);
  font-weight: 400;
  color: var(--dsh-text-4);
}
.settings-field input {
  padding: 9px 12px;
  border: 1px solid var(--dsh-border-strong);
  border-radius: var(--dsh-r-sm);
  font-size: var(--dsh-fs-md);
  font-family: inherit;
  outline: none;
  color: var(--dsh-text);
  background: var(--dsh-surface);
  transition: border-color var(--dsh-dur) var(--dsh-ease), box-shadow var(--dsh-dur) var(--dsh-ease);
}
.settings-field input:focus {
  border-color: var(--dsh-brand);
  box-shadow: var(--dsh-ring);
}
.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 14px 20px;
  border-top: 1px solid var(--dsh-border);
  position: sticky;
  bottom: 0;
  background: var(--dsh-surface);
  border-radius: 0 0 var(--dsh-r-xl) var(--dsh-r-xl);
}
.btn-cancel, .btn-save {
  padding: 8px 20px;
  border-radius: var(--dsh-r-sm);
  font-size: var(--dsh-fs-md);
  font-weight: 500;
  cursor: pointer;
  border: 1px solid var(--dsh-border-strong);
  transition: all var(--dsh-dur) var(--dsh-ease);
}
.btn-cancel { background: var(--dsh-surface); color: var(--dsh-text-2); }
.btn-cancel:hover { background: var(--dsh-surface-3); }
.btn-save {
  background: var(--dsh-brand);
  color: var(--dsh-text-invert);
  border-color: var(--dsh-brand);
  box-shadow: var(--dsh-shadow-brand);
}
.btn-save:hover:not(:disabled) { background: var(--dsh-brand-strong); border-color: var(--dsh-brand-strong); }
.btn-save:disabled { opacity: 0.5; cursor: not-allowed; box-shadow: none; }
.settings-hint {
  text-align: center;
  font-size: var(--dsh-fs-md);
  color: var(--dsh-danger-strong);
  font-weight: 500;
  background: var(--dsh-danger-soft);
  border: 1px solid var(--dsh-danger-line);
  border-radius: var(--dsh-r-sm);
  padding: 9px 12px;
}
.modal-overlay.mandatory .modal {
  box-shadow: var(--dsh-shadow-lg), 0 0 0 2px var(--dsh-brand);
}
</style>
