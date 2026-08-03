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
  padding: 8px 20px 0;
  flex-shrink: 0;
}
.bar-label {
  font-size: 12px;
  font-weight: 600;
  color: #6b7280;
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
  background: #eef1ff;
  border: 1px solid #c7d2fe;
  border-radius: 999px;
  padding: 3px 8px 3px 10px;
  font-size: 12px;
  color: #374151;
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
  color: #b45309;
  background: #fef3c7;
  border-radius: 999px;
  padding: 0 6px;
  line-height: 16px;
  flex-shrink: 0;
}
.chip-remove {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 12px;
  color: #6b7280;
  padding: 0 2px;
  line-height: 1;
  border-radius: 50%;
  flex-shrink: 0;
  transition: all 0.15s;
}
.chip-remove:hover { color: #ef4444; background: #fee2e2; }
.chip-add {
  background: #fff;
  border: 1px dashed #c7d2fe;
  border-radius: 999px;
  padding: 3px 12px;
  font-size: 12px;
  color: #4f6ef7;
  cursor: pointer;
  transition: all 0.15s;
  white-space: nowrap;
}
.chip-add:hover { background: #eef1ff; border-color: #4f6ef7; }
</style>
