<template>
  <div class="player">
    <div class="header">
      <h3>Video</h3>
      <div class="meta" v-if="video && video.filename">{{ video.filename }}</div>
    </div>

    <div v-if="!video" class="placeholder">
      <p>Select a video on the left.</p>
    </div>

    <video
      v-else
      class="video"
      :src="video.url"
      controls
      playsinline
      preload="metadata"
    ></video>
  </div>
</template>

<script setup>
defineProps({
  video: {
    type: Object,
    default: null,
  },
});
</script>

<style scoped>
.player {
  border: 1px solid #e5e7eb;
  border-radius: 10px;
  background: #fff;
  padding: 12px;
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

.placeholder {
  color: #6b7280;
}

.video {
  width: 100%;
  max-height: 70vh;
  background: #111827;
  border-radius: 10px;
}
</style>
