<template>
  <div class="video-list">
    <div class="header">
      <h3>Last videos</h3>
      <div class="meta" v-if="videos && videos.length">{{ videos.length }} shown</div>
    </div>

    <div v-if="!videos || videos.length === 0" class="empty">
      <p>No videos found for this machine.</p>
    </div>

    <ul v-else>
      <li
        v-for="video in videos"
        :key="video.filename"
        :class="{ selected: selectedFilename === video.filename }"
        @click="emit('select', video)"
      >
        <span class="filename">{{ video.filename }}</span>
      </li>
    </ul>
  </div>
</template>

<script setup>
const props = defineProps({
  videos: {
    type: Array,
    required: true,
  },
  selectedFilename: {
    type: String,
    default: '',
  },
});

const emit = defineEmits(['select']);
</script>

<style scoped>
.video-list {
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  background: #fff;
  padding: 12px;
  height: 100%;
  overflow: auto;
}

.header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
}

.header h3 {
  margin: 0;
}

.meta {
  color: #6b7280;
  font-size: 12px;
}

.empty {
  color: #6b7280;
}

ul {
  list-style: none;
  padding: 0;
  margin: 0;
}

li {
  padding: 10px 10px;
  border-radius: 8px;
  cursor: pointer;
  border: 1px solid transparent;
}

li:hover {
  background: #f9fafb;
}

li.selected {
  background: #eff6ff;
  border-color: #bfdbfe;
}

.filename {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
  font-size: 12px;
}
</style>
