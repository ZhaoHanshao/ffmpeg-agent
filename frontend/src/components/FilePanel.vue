<script setup>
import { ref, computed, onMounted } from 'vue'
import { api } from '../api'
import { fileKind, formatSize } from '../utils'
import { fetchFileSize, downloadFile } from '../composables/useFileUrl'

const emit = defineEmits(['notify', 'select-output', 'removed', 'preview'])

const props = defineProps({
  selectedFiles: { type: Array, default: () => [] },
})

const uploadedFiles = ref([])
const outputFiles = ref([])
const uploading = ref(false)
const loadingUpload = ref(false)
const loadingOutput = ref(false)
const selectedOutput = ref(new Set())
const batchProcessing = ref(false)
const dragOver = ref(false)
const fileInput = ref(null)
// 文件名 → 体积（字节）。列表接口只返回文件名，这里对每个文件发一次 HEAD
// 取 Content-Length；失败就按未知处理，不影响列表展示。
const sizes = ref({})

const allOutputSelected = computed(
  () => outputFiles.value.length > 0 && selectedOutput.value.size === outputFiles.value.length
)

function sizeOf(name) {
  return formatSize(sizes.value[name])
}

async function loadSizes(names, base) {
  const targets = (names || []).filter((n) => sizes.value[n] === undefined)
  if (!targets.length) return
  // 走 Range 请求取体积：HEAD 在这个 FastAPI/Starlette 版本上会 404
  // （详见 useFileUrl.fetchFileSize 的注释），所以文件大小以前一直显示不出来。
  const results = await Promise.all(
    targets.map(async (n) => [n, await fetchFileSize(n, base)])
  )
  const next = { ...sizes.value }
  for (const [n, sz] of results) next[n] = sz
  sizes.value = next
}

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
    loadSizes(uploadedFiles.value, 'upload')
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
    loadSizes(outputFiles.value, 'output')
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

function addToWorkspace(filename, src) {
  emit('select-output', filename, src)
}

/** 请求弹窗预览；带上整个列表，弹窗里可以左右切换。 */
function requestPreview(filename, src, list) {
  emit('preview', { name: filename, src, list: [...list] })
}

// 下载统一走 useFileUrl.downloadFile：开了 AUTH_TOKEN 时 <a href=直链 download> 会 401
function download(filename, src) {
  downloadFile(filename, src).catch((e) => emit('notify', `下载失败: ${e?.message || e}`))
}

function isSelected(src, name) {
  return props.selectedFiles.some((s) => s.src === src && s.name === name)
}

async function deleteUploadedFile(filename) {
  try {
    await api.deleteUpload(filename)
    emit('removed', filename, 'upload')
    await refreshUploadedFiles()
  } catch (e) {
    emit('notify', `删除失败: ${e.message}`)
  }
}

async function deleteOutputFile(filename) {
  try {
    await api.deleteOutput(filename)
    emit('removed', filename, 'output')
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

defineExpose({ refreshAll, refreshOutputFiles, triggerUpload })

onMounted(refreshAll)
</script>

<template>
  <aside class="file-panel">
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
          <span class="drop-sub">支持视频 / 音频 / 图片，可多选</span>
        </template>
        <template v-else>
          <span class="drop-icon">＋</span>
          <span class="drop-text">继续添加文件</span>
          <span class="drop-sub">已就绪 {{ uploadedFiles.length }} 个</span>
        </template>
        <div v-if="uploading" class="uploading-overlay">
          <div class="spinner" />
          <span>上传中…</span>
        </div>
      </div>
    </section>

    <section class="file-section">
      <div class="section-title">
        <span>已上传文件</span>
        <span v-if="uploadedFiles.length" class="section-count">{{ uploadedFiles.length }}</span>
      </div>
      <div class="file-section-body">
        <div v-if="loadingUpload" class="file-status"><span class="mini-spinner" /> 加载中…</div>
        <div v-else-if="!uploadedFiles.length" class="file-status empty">
          <span class="empty-emoji">📂</span>
          <span>还没有上传文件</span>
        </div>
        <div v-else class="file-list">
          <div
            v-for="f in uploadedFiles"
            :key="f"
            class="file-row clickable"
            :class="{ selected: isSelected('upload', f) }"
            title="点击预览"
            @click="requestPreview(f, 'upload', uploadedFiles)"
          >
            <span class="file-icon" :title="fileKind(f).label">{{ fileKind(f).icon }}</span>
            <div class="file-meta">
              <span class="file-name" :title="f">{{ f }}</span>
              <span v-if="sizeOf(f)" class="file-size">{{ sizeOf(f) }}</span>
            </div>
            <div class="file-actions">
              <button
                class="file-btn preview"
                title="预览"
                aria-label="预览"
                @click.stop="requestPreview(f, 'upload', uploadedFiles)"
              >👁</button>
              <button
                class="file-btn add"
                :class="{ active: isSelected('upload', f) }"
                title="加入工作区"
                aria-label="加入工作区"
                @click.stop="addToWorkspace(f, 'upload')"
              >＋</button>
              <button
                class="file-btn download"
                title="下载"
                aria-label="下载"
                @click.stop="download(f, 'upload')"
              >⬇</button>
              <button class="file-btn delete" title="删除" aria-label="删除" @click.stop="deleteUploadedFile(f)">🗑</button>
            </div>
          </div>
        </div>
      </div>
    </section>

    <section class="file-section">
      <div class="section-title">
        <label v-if="outputFiles.length" class="select-all" @click.stop>
          <input
            type="checkbox"
            :checked="allOutputSelected"
            aria-label="全选已完成文件"
            @change="toggleSelectAllOutput"
          />
        </label>
        <span>已完成文件</span>
        <span v-if="outputFiles.length" class="section-count">{{ outputFiles.length }}</span>
      </div>
      <div class="file-section-body">
        <div v-if="loadingOutput" class="file-status"><span class="mini-spinner" /> 加载中…</div>
        <div v-else-if="!outputFiles.length" class="file-status empty">
          <span class="empty-emoji">🎯</span>
          <span>处理完成后，生成的文件会出现在这里</span>
        </div>
        <div v-else class="file-list">
          <div
            v-for="f in outputFiles"
            :key="f"
            class="file-row selectable"
            :class="{ selected: selectedOutput.has(f) }"
            @click="toggleOutputFile(f)"
          >
            <span
              class="file-checkbox"
              :class="{ checked: selectedOutput.has(f) }"
              role="checkbox"
              :aria-checked="selectedOutput.has(f)"
              @click.stop="toggleOutputFile(f)"
            />
            <span class="file-icon" :title="fileKind(f).label">{{ fileKind(f).icon }}</span>
            <div class="file-meta">
              <span class="file-name" :title="f">{{ f }}</span>
              <span v-if="sizeOf(f)" class="file-size">{{ sizeOf(f) }}</span>
            </div>
            <div class="file-actions">
              <button
                class="file-btn preview"
                title="预览"
                aria-label="预览"
                @click.stop="requestPreview(f, 'output', outputFiles)"
              >👁</button>
              <button
                class="file-btn add"
                :class="{ active: isSelected('output', f) }"
                title="加入工作区（用于下一步处理）"
                aria-label="加入工作区"
                @click.stop="addToWorkspace(f, 'output')"
              >＋</button>
              <button
                class="file-btn download"
                title="下载"
                aria-label="下载"
                @click.stop="download(f, 'output')"
              >⬇</button>
              <button class="file-btn delete" title="删除" aria-label="删除" @click.stop="deleteOutputFile(f)">🗑</button>
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
.file-panel {
  width: 340px;
  flex-shrink: 0;
  overflow: hidden;
  background: var(--dsh-surface);
  /* 文件面板现在在最右侧，分隔线改到左边 */
  border-left: 1px solid var(--dsh-border);
  padding: 14px 14px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-height: 0;
}

/* ── 上传区 ── */
.upload-section { flex-shrink: 0; }
.drop-zone {
  position: relative;
  border: 1.5px dashed var(--dsh-border-strong);
  border-radius: var(--dsh-r-lg);
  padding: 18px 16px;
  text-align: center;
  cursor: pointer;
  transition: border-color var(--dsh-dur) var(--dsh-ease), background var(--dsh-dur) var(--dsh-ease);
  background: var(--dsh-surface-2);
}
.drop-zone:hover { border-color: var(--dsh-brand-line); background: var(--dsh-brand-soft); }
.drop-zone.drag-over {
  border-color: var(--dsh-brand);
  background: var(--dsh-brand-soft);
  box-shadow: var(--dsh-ring);
}
.drop-zone.has-files {
  border-style: solid;
  border-color: var(--dsh-success-line);
  background: var(--dsh-success-soft);
  padding: 13px 16px;
}
.drop-icon { display: block; font-size: 22px; margin-bottom: 3px; line-height: 1.2; }
.drop-zone.has-files .drop-icon { font-size: 17px; color: var(--dsh-success); font-weight: 600; }
.drop-text { display: block; font-size: var(--dsh-fs-base); font-weight: 600; color: var(--dsh-text-2); }
.drop-sub { display: block; font-size: var(--dsh-fs-sm); color: var(--dsh-text-4); margin-top: 2px; }
.uploading-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  background: rgba(255, 255, 255, 0.88);
  border-radius: var(--dsh-r-lg);
  font-size: var(--dsh-fs-base);
  color: var(--dsh-brand);
  font-weight: 500;
}
.uploading-overlay .spinner {
  width: 16px;
  height: 16px;
  border: 2px solid var(--dsh-border);
  border-top-color: var(--dsh-brand);
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
  padding-right: 2px;
}
.file-section-body::-webkit-scrollbar { width: 6px; }
.file-section-body::-webkit-scrollbar-thumb {
  background: #dfe3ea;
  border-radius: var(--dsh-r-pill);
}
.section-title {
  font-size: var(--dsh-fs-sm);
  font-weight: 600;
  color: var(--dsh-text-3);
  padding: 10px 2px 6px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  letter-spacing: 0.02em;
}
.section-count {
  font-size: var(--dsh-fs-xs);
  font-weight: 600;
  color: var(--dsh-text-3);
  background: var(--dsh-surface-3);
  padding: 1px 7px;
  border-radius: var(--dsh-r-pill);
  line-height: 17px;
}
.file-status {
  padding: 10px 2px;
  font-size: var(--dsh-fs-base);
  color: var(--dsh-text-4);
  display: flex;
  align-items: center;
  gap: 7px;
}
.file-status.empty {
  flex-direction: column;
  gap: 4px;
  padding: 26px 12px;
  text-align: center;
  line-height: 1.6;
}
.file-status.empty .empty-emoji { font-size: 20px; opacity: 0.6; }
.mini-spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  border: 2px solid var(--dsh-border);
  border-top-color: var(--dsh-brand);
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}

.file-list { display: flex; flex-direction: column; gap: 1px; }
.file-row {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 7px 8px;
  border-radius: var(--dsh-r-sm);
  transition: background var(--dsh-dur) var(--dsh-ease);
  cursor: default;
  min-width: 0;
}
.file-row:hover { background: var(--dsh-surface-2); }
.file-row.selectable { cursor: pointer; }
/* 已上传列表的行没有别的用途，整行点击即预览 */
.file-row.clickable { cursor: pointer; }
.file-row.selected { background: var(--dsh-brand-soft); }
.file-row.selected:hover { background: #e3e8ff; }
.file-icon { font-size: 15px; flex-shrink: 0; line-height: 1; }
.file-meta { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
.file-name {
  font-size: var(--dsh-fs-base);
  color: var(--dsh-text-2);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}
.file-row.selected .file-name { color: var(--dsh-text); font-weight: 500; }
.file-size {
  font-size: var(--dsh-fs-xs);
  color: var(--dsh-text-4);
  font-variant-numeric: tabular-nums;
}
.file-actions {
  display: flex;
  gap: 1px;
  flex-shrink: 0;
  opacity: 0;
  transition: opacity var(--dsh-dur) var(--dsh-ease);
}
.file-row:hover .file-actions,
.file-row.selected .file-actions,
.file-row:focus-within .file-actions { opacity: 1; }
.file-btn {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 13px;
  padding: 0;
  border-radius: var(--dsh-r-xs);
  line-height: 1;
  transition: background var(--dsh-dur) var(--dsh-ease);
  display: flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  color: var(--dsh-text-3);
}
.file-btn:hover { background: #e6e8ee; text-decoration: none; }
.file-btn.preview:hover { background: var(--dsh-brand-soft); color: var(--dsh-brand); }
.file-btn.add:hover { background: var(--dsh-brand-soft); color: var(--dsh-brand); }
.file-btn.add.active { background: var(--dsh-brand); color: #fff; }
.file-btn.delete:hover { background: var(--dsh-danger-soft); color: var(--dsh-danger); }
/* 触屏没有 hover：操作按钮必须常驻，否则预览/加号/删除永远点不到 */
@media (hover: none) {
  .file-actions { opacity: 1; }
}

.file-checkbox {
  width: 16px;
  height: 16px;
  border: 1.5px solid var(--dsh-border-strong);
  border-radius: 4px;
  flex-shrink: 0;
  cursor: pointer;
  transition: all var(--dsh-dur) var(--dsh-ease);
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--dsh-surface);
}
.file-checkbox.checked { background: var(--dsh-brand); border-color: var(--dsh-brand); }
.file-checkbox.checked::after {
  content: '';
  width: 4px;
  height: 8px;
  border: solid #fff;
  border-width: 0 2px 2px 0;
  transform: rotate(45deg) translateY(-1px);
}
.file-row:hover .file-checkbox { border-color: var(--dsh-brand); }

.select-all { display: flex; align-items: center; cursor: pointer; }
.select-all input { width: 14px; height: 14px; accent-color: var(--dsh-brand); cursor: pointer; }

.batch-bar {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 9px 10px;
  background: var(--dsh-surface);
  border-top: 1px solid var(--dsh-border);
  margin: 6px -14px -12px;
  box-shadow: 0 -3px 10px rgba(16, 24, 40, 0.06);
}
.batch-count { font-size: var(--dsh-fs-sm); color: var(--dsh-text-2); font-weight: 600; white-space: nowrap; }
.batch-actions { display: flex; gap: 6px; }
.batch-btn {
  padding: 5px 12px;
  border: 1px solid var(--dsh-border-strong);
  border-radius: var(--dsh-r-sm);
  font-size: var(--dsh-fs-sm);
  font-weight: 500;
  cursor: pointer;
  transition: all var(--dsh-dur) var(--dsh-ease);
  background: var(--dsh-surface);
  color: var(--dsh-text-2);
  white-space: nowrap;
}
.batch-btn:hover:not(:disabled) { border-color: var(--dsh-brand); color: var(--dsh-brand); }
.batch-btn.download:hover:not(:disabled) { background: var(--dsh-brand-soft); }
.batch-btn.delete:hover:not(:disabled) {
  background: var(--dsh-danger-soft);
  border-color: var(--dsh-danger);
  color: var(--dsh-danger);
}
.batch-btn:disabled { opacity: 0.45; cursor: not-allowed; }
</style>
