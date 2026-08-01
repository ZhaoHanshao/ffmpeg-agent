<script setup>
import { defineModel } from 'vue'

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
  background: rgba(0, 0, 0, 0.35);
  display: flex;
  align-items: center;
  justify-content: center;
}
.modal {
  background: #fff;
  border-radius: 14px;
  width: 480px;
  max-width: 90vw;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.15);
  display: flex;
  flex-direction: column;
}
.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid #e5e7eb;
  font-size: 16px;
  font-weight: 600;
}
.modal-close {
  background: none;
  border: none;
  font-size: 22px;
  cursor: pointer;
  color: #9ca3af;
  line-height: 1;
}
.modal-close:hover { color: #374151; }
.modal-body { padding: 20px; display: flex; flex-direction: column; gap: 14px; }
.settings-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 13px;
  font-weight: 500;
  color: #374151;
}
.settings-field input {
  padding: 8px 12px;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  font-size: 14px;
  font-family: inherit;
  outline: none;
  transition: border-color 0.2s;
}
.settings-field input:focus { border-color: #4f6ef7; }
.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 14px 20px;
  border-top: 1px solid #e5e7eb;
}
.btn-cancel, .btn-save {
  padding: 8px 20px;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  border: 1px solid #d1d5db;
  transition: all 0.15s;
}
.btn-cancel { background: #fff; color: #374151; }
.btn-cancel:hover { background: #f3f4f6; }
.btn-save { background: #4f6ef7; color: #fff; border-color: #4f6ef7; }
.btn-save:hover:not(:disabled) { background: #3b5de7; }
.btn-save:disabled { opacity: 0.5; cursor: not-allowed; }
.settings-hint {
  text-align: center;
  font-size: 14px;
  color: #ef4444;
  font-weight: 500;
  padding-bottom: 4px;
}
.modal-overlay.mandatory .modal {
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.25);
  border: 2px solid #4f6ef7;
}
</style>
