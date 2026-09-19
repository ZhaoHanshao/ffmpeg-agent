<script setup>
import { computed } from 'vue'
import ModelPicker from './ModelPicker.vue'

const show = defineModel('show', { type: Boolean, default: false })
const props = defineProps({
  // 当前作用域 × 角色下正在编辑的草稿，由父组件决定绑哪一份
  settings: { type: Object, required: true },
  scope: { type: String, default: 'global' },
  role: { type: String, default: 'text' },
  // 视觉角色的接口地址可以留空（沿用主模型），此时拉列表要用主模型的地址
  fallbackBaseUrl: { type: String, default: '' },
  // 是否单独配置视觉模型（仅 vision 角色有意义）
  visionEnabled: { type: Boolean, default: false },
  // 没有打开任何对话时不能选"仅本对话"
  canUseConversationScope: { type: Boolean, default: false },
  // 本对话已覆盖的字段名，用于提示"哪些不再继承全局"
  overrideFields: { type: Array, default: () => [] },
  visionOverrideFields: { type: Array, default: () => [] },
  // 一句话说明画面分析当前实际用哪个模型
  visionSummary: { type: String, default: '' },
  configured: { type: Boolean, default: false },
  saving: { type: Boolean, default: false },
  error: { type: String, default: '' },
})
const emit = defineEmits(['save', 'clear-override', 'update:scope', 'update:role', 'update:visionEnabled'])

const FIELD_LABELS = {
  model: '模型',
  base_url: '接口地址',
  api_key: 'API Key',
  temperature: '温度',
  max_tokens: '最大 token',
}

const isConversation = computed(() => props.scope === 'conversation')
const isVision = computed(() => props.role === 'vision')
const overriddenText = computed(() =>
  props.overrideFields.map((f) => FIELD_LABELS[f] || f).join('、')
)
const visionOverriddenText = computed(() =>
  props.visionOverrideFields.map((f) => FIELD_LABELS[f] || f).join('、')
)
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
        <!-- 作用域：全局默认 / 只改这一个对话 -->
        <div v-if="canUseConversationScope" class="scope-switch" role="tablist" aria-label="设置作用域">
          <button
            class="scope-btn"
            :class="{ active: !isConversation }"
            role="tab"
            :aria-selected="!isConversation"
            @click="emit('update:scope', 'global')"
          >全局默认</button>
          <button
            class="scope-btn"
            :class="{ active: isConversation }"
            role="tab"
            :aria-selected="isConversation"
            @click="emit('update:scope', 'conversation')"
          >仅本对话</button>
        </div>

        <!-- 角色：主模型（写命令/回答） / 视觉模型（看画面/波形）。
             两个角色分开配置，互不借用——文本模型往往不收图，
             视觉模型写 ffmpeg 命令又未必更准。 -->
        <div class="role-switch" role="tablist" aria-label="模型角色">
          <button
            class="scope-btn"
            :class="{ active: !isVision }"
            role="tab"
            :aria-selected="!isVision"
            @click="emit('update:role', 'text')"
          >主模型<span class="role-sub">写命令 / 回答</span></button>
          <button
            class="scope-btn"
            :class="{ active: isVision }"
            role="tab"
            :aria-selected="isVision"
            @click="emit('update:role', 'vision')"
          >视觉模型<span class="role-sub">看画面 / 波形</span></button>
        </div>

        <p v-if="!configured" class="settings-hint">请先填写 LLM 模型信息以开始使用</p>
        <p v-if="error" class="settings-error">{{ error }}</p>

        <!-- ── 主模型 ── -->
        <template v-if="!isVision">
          <template v-if="isConversation">
            <p class="settings-note">
              只覆盖你想改的字段，其余字段继续跟随全局配置。<b>把某项改回与全局相同即表示不再覆盖它。</b>
            </p>
            <p v-if="overrideFields.length" class="settings-note overridden">
              本对话已覆盖：{{ overriddenText }}
            </p>
          </template>
          <p v-else class="settings-note">配置保存在服务端 <code>backend/data/llm_settings.json</code>，不读取 .env。</p>

          <label class="settings-field">
            <span>模型名称</span>
            <ModelPicker
              v-model="settings.model"
              :base-url="settings.base_url"
              :api-key="settings.api_key"
              role="text"
              placeholder="如 gpt-4o-mini / deepseek-chat"
            />
          </label>
          <label class="settings-field">
            <span>接口地址 <span class="field-hint">OpenAI 兼容的 BASE_URL</span></span>
            <input v-model="settings.base_url" placeholder="如 https://api.openai.com/v1" />
          </label>
          <label class="settings-field">
            <span>API Key <span v-if="isConversation" class="field-hint">留空表示沿用全局</span></span>
            <input v-model="settings.api_key" type="password" placeholder="sk-..." />
          </label>
          <label v-if="!isConversation" class="settings-field">
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
        </template>

        <!-- ── 视觉模型 ── -->
        <template v-else>
          <label class="vision-toggle">
            <input
              type="checkbox"
              :checked="visionEnabled"
              @change="emit('update:visionEnabled', $event.target.checked)"
            />
            <span>单独配置视觉模型</span>
          </label>
          <p class="settings-note">
            开启后：素材画面/波形<b>只</b>送给这个模型，主模型不再接触图像。
            关闭则回退主模型（若主模型不支持图像，会自动跳过画面并注明）。
          </p>
          <p class="settings-note vision-where">{{ visionSummary }}</p>
          <p v-if="isConversation && visionOverrideFields.length" class="settings-note overridden">
            本对话已覆盖视觉模型：{{ visionOverriddenText }}
          </p>

          <template v-if="visionEnabled">
            <label class="settings-field">
              <span>视觉模型名称 <span class="field-hint">列表里带「可用视觉」的更适合</span></span>
              <ModelPicker
                v-model="settings.model"
                :base-url="settings.base_url || fallbackBaseUrl"
                :api-key="settings.api_key"
                role="vision"
                placeholder="如 gpt-4o / qwen-vl-max"
              />
            </label>
            <label class="settings-field">
              <span>接口地址 <span class="field-hint">留空表示与主模型相同</span></span>
              <input v-model="settings.base_url" placeholder="留空沿用主模型" />
            </label>
            <label class="settings-field">
              <span>API Key <span class="field-hint">留空沿用主模型</span></span>
              <input v-model="settings.api_key" type="password" placeholder="留空沿用主模型" />
            </label>
            <label class="settings-field">
              <span>Temperature (温度)</span>
              <input v-model.number="settings.temperature" type="number" step="0.1" min="0" max="2" />
            </label>
            <label class="settings-field">
              <span>Max Tokens (最大 token 数)</span>
              <input v-model.number="settings.max_tokens" type="number" step="1" min="1" />
            </label>
          </template>
        </template>
      </div>
      <div class="modal-footer">
        <button
          v-if="isConversation && (overrideFields.length || visionOverrideFields.length)"
          class="btn-cancel btn-clear"
          :disabled="saving"
          @click="emit('clear-override')"
        >恢复继承全局</button>
        <button v-if="configured" class="btn-cancel" @click="show = false">取消</button>
        <button class="btn-save" :disabled="saving" @click="emit('save')">
          {{ saving ? '保存中…' : (isConversation ? '保存到本对话' : '保存') }}
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

/* ── 作用域 / 角色切换 ── */
.scope-switch, .role-switch {
  display: flex;
  background: var(--dsh-surface-3);
  border-radius: var(--dsh-r-md);
  padding: 3px;
  gap: 2px;
}
.scope-btn {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1px;
  background: none;
  border: none;
  border-radius: var(--dsh-r-sm);
  padding: 7px 10px;
  font-size: var(--dsh-fs-base);
  font-family: inherit;
  font-weight: 500;
  color: var(--dsh-text-3);
  cursor: pointer;
  transition: color var(--dsh-dur) var(--dsh-ease), background var(--dsh-dur) var(--dsh-ease);
}
.scope-btn:hover { color: var(--dsh-text-2); }
.scope-btn.active {
  background: var(--dsh-surface);
  color: var(--dsh-brand);
  box-shadow: var(--dsh-shadow-sm);
}
.role-sub {
  font-size: var(--dsh-fs-xs);
  font-weight: 400;
  color: var(--dsh-text-4);
}
.scope-btn.active .role-sub { color: var(--dsh-brand-line); }

/* ── 视觉模型开关 ── */
.vision-toggle {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--dsh-fs-md);
  font-weight: 500;
  color: var(--dsh-text);
  cursor: pointer;
}
.vision-toggle input { width: 15px; height: 15px; accent-color: var(--dsh-brand); cursor: pointer; }
.settings-note.overridden { color: var(--dsh-warn); }
.settings-note.vision-where {
  color: var(--dsh-brand);
  background: var(--dsh-brand-soft);
  border: 1px solid var(--dsh-brand-line);
  border-radius: var(--dsh-r-sm);
  padding: 7px 10px;
}
.btn-clear { margin-right: auto; }
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
.settings-note {
  font-size: var(--dsh-fs-sm);
  color: var(--dsh-text-4);
  line-height: 1.5;
}
.settings-note code {
  background: var(--dsh-surface-3);
  border-radius: 4px;
  padding: 1px 5px;
  font-size: 11.5px;
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
/* 保存/校验失败的原因（如"API Key 无效或已过期"） */
.settings-error {
  font-size: var(--dsh-fs-base);
  color: var(--dsh-danger-strong);
  background: var(--dsh-danger-soft);
  border: 1px solid var(--dsh-danger-line);
  border-radius: var(--dsh-r-sm);
  padding: 9px 12px;
  line-height: 1.5;
  word-break: break-word;
}
.modal-overlay.mandatory .modal {
  box-shadow: var(--dsh-shadow-lg), 0 0 0 2px var(--dsh-brand);
}
</style>
