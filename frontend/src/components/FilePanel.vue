<script setup>
import { ref, computed, onMounted } from 'vue'
import { api, API_BASE } from '../api'

const emit = defineEmits(['notify'])

const uploadedFiles = ref([])
const outputFiles = ref([])
const uploading = ref(false)
const loadingUpload = ref(false)
const loadingOutput = ref(false)
const selectedOutput = ref(new Set())
const batchProcessing = ref(false)
const dragOver = ref(false)
const fileInput = ref(null)

const allOutputSelected = computed(
  () => outputFiles.value.length > 0 && selectedOutput.value.size === outputFiles.value.length
)

function toggleOutputFile(file) {
  const s = new Set(selectedOutput.value)
  s.has(file) ? s.delete(file) : s.add(file)
  selectedOutput.value = s
}

function toggleSelectAllOutput() {
  selectedOutput.value =
    selectedOutput.value.size === outputFiles.value.length
      ? new Set()
      : new Set(outputFiles.value)
}

async function refreshUploadedFiles() {
  loadingUpload.value = true
  try {
    const data = await api.listUpload()
    uploadedFiles.value = data.files || []
  } catch (e) {
    console.error('获取上传文件列表失败:', e)
  } finally {
    loadingUpload.value = false
  }
}

async function refreshOutputFiles() {
  loadingOutput.value = true
  try {
    const data = await api.listOutput()
    outputFiles.value = data.files || []
  } catch (e) {
    console.error('获取已完成文件列表失败:', e)
  } finally {
    loadingOutput.value = false
  }
}

async function refreshAll() {
  await Promise.all([refreshUploadedFiles(), refreshOutputFiles()])
}

function triggerUpload() {
  fileInput.value?.click()
}

function onFileChange(e) {
  const files = e.target.files
  if (files?.length) doUpload(files)
  e.target.value = ''
}

function onDrop(e) {
  dragOver.value = false
  const files = e.dataTransfer?.files
  if (files?.length) doUpload(files)
}

async function doUpload(files) {
  uploading.value = true
  try {
    await api.upload(files)
    await refreshUploadedFiles()
  } catch (e) {
    emit('notify', `上传失败: ${e.message}`)
  } finally {
    uploading.value = false
  }
}

async function deleteUploadedFile(filename) {
  try {
    await api.deleteUpload(filename)
    await refreshUploadedFiles()
  } catch (e) {
    emit('notify', `删除失败: ${e.message}`)
  }
}

async function deleteOutputFile(filename) {
  try {
    await api.deleteOutput(filename)
    const s = new Set(selectedOutput.value)
    s.delete(filename)
    selectedOutput.value = s
    await refreshOutputFiles()
  } catch (e) {
    emit('notify', `删除失败: ${e.message}`)
  }
}

async function deleteSelectedOutput() {
  const files = [...selectedOutput.value]
  if (!files.length) return
  batchProcessing.value = true
  try {
    await api.batchDeleteOutput(files)
    selectedOutput.value = new Set()
    await refreshOutputFiles()
  } catch (e) {
    emit('notify', `批量删除失败: ${e.message}`)
  } finally {
    batchProcessing.value = false
  }
}

async function downloadSelectedOutput() {
  const files = [...selectedOutput.value]
  if (!files.length) return
  batchProcessing.value = true
  try {
    const blob = await api.batchDownloadOutput(files)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'outputs.zip'
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  } catch (e) {
    emit('notify', `批量下载失败: ${e.message}`)
  } finally {
    batchProcessing.value = false
  }
}

defineExpose({ refreshAll })

onMounted(refreshAll)
</script>

<template>
  <aside class="left-panel">
    <section class="upload-section">
      <div
        class="drop-zone"
        :class="{ 'drag-over': dragOver, 'has-files': uploadedFiles.length }"
        @dragover.prevent="dragOver = true"
        @dragleave="dragOver = false"
        @drop.prevent="onDrop"
        @click="triggerUpload"
      >
        <input ref="fileInput" type="file" multiple hidden @change="onFileChange" />
        <template v-if="!uploadedFiles.length">
          <span class="drop-icon">📁</span>
          <span class="drop-text">拖拽或点击上传文件</span>
        </template>
        <template v-else>
          <span class="drop-icon">✅</span>
          <span class="drop-text">{{ uploadedFiles.length }} 个文件已就绪</span>
        </template>
        <div v-if="uploading" class="uploading-overlay">
          <div class="spinner" />
          <span>上传中…</span>
        </div>
      </div>
    </section>

    <section class="file-section">
      <div class="section-title">
        已上传文件
        <span v-if="uploadedFiles.length" class="section-count">{{ uploadedFiles.length }}</span>
      </div>
      <div class="file-section-body">
        <div v-if="loadingUpload" class="file-status"><span class="mini-spinner" /> 加载中…</div>
        <div v-else-if="!uploadedFiles.length" class="file-status empty"><span>暂无上传文件</span></div>
        <div v-else class="file-list">
          <div v-for="f in uploadedFiles" :key="f" class="file-row">
            <span class="file-icon">📄</span>
            <span class="file-name" :title="f">{{ f }}</span>
            <div class="file-actions">
              <a
                :href="`${API_BASE}/upload/${encodeURIComponent(f)}`"
                class="file-btn download"
                title="下载"
                download
              >⬇</a>
              <button class="file-btn delete" title="删除" @click="deleteUploadedFile(f)">🗑</button>
            </div>
          </div>
        </div>
      </div>
    </section>

    <section class="file-section">
      <div class="section-title">
        <label v-if="outputFiles.length" class="select-all" @click.stop>
          <input type="checkbox" :checked="allOutputSelected" @change="toggleSelectAllOutput" />
        </label>
        已完成文件
        <span v-if="outputFiles.length" class="section-count">{{ outputFiles.length }}</span>
      </div>
      <div class="file-section-body">
        <div v-if="loadingOutput" class="file-status"><span class="mini-spinner" /> 加载中…</div>
        <div v-else-if="!outputFiles.length" class="file-status empty"><span>暂无完成文件</span></div>
        <div v-else class="file-list">
          <div
            v-for="f in outputFiles"
            :key="f"
            class="file-row"
            :class="{ selected: selectedOutput.has(f) }"
            @click="toggleOutputFile(f)"
          >
            <span
              class="file-checkbox"
              :class="{ checked: selectedOutput.has(f) }"
              @click.stop="toggleOutputFile(f)"
            />
            <span class="file-icon">🎯</span>
            <span class="file-name" :title="f">{{ f }}</span>
            <div class="file-actions">
              <a
                :href="`${API_BASE}/output/${encodeURIComponent(f)}`"
                class="file-btn download"
                title="下载"
                download
                @click.stop
              >⬇</a>
              <button class="file-btn delete" title="删除" @click.stop="deleteOutputFile(f)">🗑</button>
            </div>
          </div>
        </div>
      </div>
      <div v-if="selectedOutput.size > 0" class="batch-bar">
        <span class="batch-count">已选 {{ selectedOutput.size }} 项</span>
        <div class="batch-actions">
          <button
            class="batch-btn download"
            :disabled="batchProcessing"
            @click="downloadSelectedOutput"
          >⬇ 下载选中</button>
          <button
            class="batch-btn delete"
            :disabled="batchProcessing"
            @click="deleteSelectedOutput"
          >🗑 删除选中</button>
        </div>
      </div>
    </section>
  </aside>
</template>

<style scoped>
.left-panel {
  width: 360px;
  flex-shrink: 0;
  overflow: hidden;
  background: #fff;
  border-right: 1px solid #e5e7eb;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.upload-section { flex-shrink: 0; }
.drop-zone {
  position: relative;
  border: 2px dashed #d1d5db;
  border-radius: 10px;
  padding: 16px;
  text-align: center;
  cursor: pointer;
  transition: all 0.2s;
  background: #fafbfc;
}
.drop-zone:hover { border-color: #4f6ef7; background: #f8f9ff; }
.drop-zone.drag-over { border-color: #4f6ef7; background: #eef1ff; }
.drop-zone.has-files { border-style: solid; border-color: #22c55e; background: #f0fdf4; }
.drop-icon { display: block; font-size: 24px; margin-bottom: 2px; }
.drop-text { display: block; font-size: 13px; font-weight: 500; color: #374151; }
.uploading-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  background: rgba(255, 255, 255, 0.85);
  border-radius: 10px;
  font-size: 13px;
  color: #4f6ef7;
}
.uploading-overlay .spinner {
  width: 16px;
  height: 16px;
  border: 2px solid #e5e7eb;
  border-top-color: #4f6ef7;
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

.file-section {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.file-section-body {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}
.file-section-body::-webkit-scrollbar { width: 6px; }
.file-section-body::-webkit-scrollbar-thumb { background: #d1d5db; border-radius: 3px; }
.section-title {
  font-size: 13px;
  font-weight: 600;
  color: #6b7280;
  padding: 8px 0 4px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  letter-spacing: 0.3px;
}
.section-count {
  font-size: 11px;
  font-weight: 500;
  color: #9ca3af;
  background: #f3f4f6;
  padding: 1px 7px;
  border-radius: 10px;
  line-height: 18px;
}
.file-status {
  padding: 8px 0;
  font-size: 13px;
  color: #9ca3af;
  display: flex;
  align-items: center;
  gap: 6px;
}
.file-status.empty { padding: 14px 0; text-align: center; justify-content: center; }
.mini-spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  border: 2px solid #e5e7eb;
  border-top-color: #4f6ef7;
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

.file-list { display: flex; flex-direction: column; gap: 2px; }
.file-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 6px;
  transition: background 0.15s;
  cursor: default;
}
.file-row:hover { background: #f0f2f5; }
.file-icon { font-size: 14px; flex-shrink: 0; line-height: 1; }
.file-name {
  flex: 1;
  font-size: 13px;
  color: #374151;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}
.file-actions {
  display: flex;
  gap: 2px;
  flex-shrink: 0;
  opacity: 0;
  transition: opacity 0.15s;
}
.file-row:hover .file-actions,
.file-row.selected .file-actions { opacity: 1; }
.file-btn {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 14px;
  padding: 4px;
  border-radius: 4px;
  line-height: 1;
  transition: background 0.15s;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
}
.file-btn:hover { background: #e5e7eb; text-decoration: none; }
.file-btn.delete:hover { background: #fee2e2; }

.file-checkbox {
  width: 16px;
  height: 16px;
  border: 2px solid #d1d5db;
  border-radius: 3px;
  flex-shrink: 0;
  cursor: pointer;
  transition: all 0.15s;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #fff;
}
.file-checkbox.checked { background: #4f6ef7; border-color: #4f6ef7; }
.file-checkbox.checked::after {
  content: '';
  width: 4px;
  height: 8px;
  border: solid #fff;
  border-width: 0 2px 2px 0;
  transform: rotate(45deg) translateY(-1px);
}
.file-row:hover .file-checkbox { border-color: #4f6ef7; }
.file-row.selected { background: #eef1ff; }
.file-row.selected:hover { background: #e0e5ff; }

.select-all { display: flex; align-items: center; cursor: pointer; }
.select-all input { width: 14px; height: 14px; accent-color: #4f6ef7; cursor: pointer; }

.batch-bar {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px 10px;
  background: #fff;
  border-top: 1px solid #e5e7eb;
  margin: 0 -12px -12px;
  box-shadow: 0 -2px 8px rgba(0, 0, 0, 0.06);
}
.batch-count { font-size: 12px; color: #374151; font-weight: 500; white-space: nowrap; }
.batch-actions { display: flex; gap: 6px; }
.batch-btn {
  padding: 5px 12px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s;
  background: #fff;
  white-space: nowrap;
}
.batch-btn:hover:not(:disabled) { border-color: #4f6ef7; color: #4f6ef7; }
.batch-btn.download:hover:not(:disabled) { background: #eef1ff; }
.batch-btn.delete:hover:not(:disabled) { background: #fef2f2; border-color: #ef4444; color: #ef4444; }
.batch-btn:disabled { opacity: 0.4; cursor: not-allowed; }
</style>
