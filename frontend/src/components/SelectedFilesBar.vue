<script setup>
defineProps({
  files: { type: Array, default: () => [] },
})

const emit = defineEmits(['remove', 'add'])
</script>

<template>
  <div class="selected-bar">
    <span class="bar-label">已选择</span>
    <div class="chip-list">
      <span v-for="s in files" :key="`${s.src}-${s.name}`" class="chip" :title="s.name">
        <span class="chip-icon">{{ s.src === 'output' ? '🎯' : '📄' }}</span>
        <span class="chip-name">{{ s.name }}</span>
        <span v-if="s.src === 'output'" class="chip-tag">输出</span>
        <button class="chip-remove" title="移除选择（不删除文件）" @click="emit('remove', s)">✕</button>
      </span>
      <button class="chip-add" title="上传并添加文件" @click="emit('add')">＋ 添加</button>
    </div>
  </div>
</template>

<style scoped>
.selected-bar {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 24px 0;
  flex-shrink: 0;
}
.bar-label {
  font-size: var(--dsh-fs-sm);
  font-weight: 600;
  color: var(--dsh-text-3);
  line-height: 28px;
  white-space: nowrap;
  padding-top: 1px;
}
.chip-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  flex: 1;
  min-width: 0;
}
.chip {
  display: flex;
  align-items: center;
  gap: 5px;
  background: var(--dsh-brand-soft);
  border: 1px solid var(--dsh-brand-line);
  border-radius: var(--dsh-r-pill);
  padding: 3px 8px 3px 10px;
  font-size: var(--dsh-fs-sm);
  color: var(--dsh-text-2);
  max-width: 100%;
}
.chip-icon { font-size: 12px; flex-shrink: 0; }
.chip-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 200px;
}
.chip-tag {
  font-size: 10px;
  color: var(--dsh-warn);
  background: var(--dsh-warn-soft);
  border: 1px solid var(--dsh-warn-line);
  border-radius: var(--dsh-r-pill);
  padding: 0 6px;
  line-height: 15px;
  flex-shrink: 0;
}
.chip-remove {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 12px;
  color: var(--dsh-text-3);
  padding: 0 2px;
  line-height: 1;
  border-radius: 50%;
  flex-shrink: 0;
  transition: all var(--dsh-dur) var(--dsh-ease);
}
.chip-remove:hover { color: var(--dsh-danger); background: var(--dsh-danger-soft); }
.chip-add {
  background: var(--dsh-surface);
  border: 1px dashed var(--dsh-brand-line);
  border-radius: var(--dsh-r-pill);
  padding: 3px 12px;
  font-size: var(--dsh-fs-sm);
  color: var(--dsh-brand);
  cursor: pointer;
  transition: all var(--dsh-dur) var(--dsh-ease);
  white-space: nowrap;
}
.chip-add:hover { background: var(--dsh-brand-soft); border-color: var(--dsh-brand); }
</style>
