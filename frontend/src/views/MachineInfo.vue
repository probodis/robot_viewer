<template>
  <div class="page">
    <div class="topbar">
      <h1>{{ title }}</h1>

      <div class="actions">
        <input
          v-model="machineIdInput"
          type="text"
          autocomplete="off"
          placeholder="e.g. cb-3-0020"
          @keyup.enter="goToMachine"
        />
        <button @click="goToMachine" :disabled="!machineIdInput">Open</button>
        <button @click="reload" :disabled="isLoading">Reload</button>
      </div>
    </div>

    <div v-if="error" class="error">{{ error }}</div>

    <div class="layout">
      <VideoList
        class="list"
        :videos="videos"
        :selectedFilename="selectedVideo?.filename || ''"
        @select="selectVideo"
      ></VideoList>

      <VideoPlayer class="player" :video="selectedVideo"></VideoPlayer>
    </div>

    <div v-if="isLoading" class="loading">Loading...</div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../services/api'
import VideoList from '../components/VideoList.vue'
import VideoPlayer from '../components/VideoPlayer.vue'

const route = useRoute()
const router = useRouter()

const machine_id = computed(() => route.params.machine_id)
const title = computed(() => (machine_id.value ? `Machine: ${machine_id.value}` : 'Machine Info'))

const machineIdInput = ref('')
const videos = ref([])
const selectedVideo = ref(null)
const isLoading = ref(false)
const error = ref('')

async function loadVideos(machineId) {
  if (!machineId) return

  isLoading.value = true
  error.value = ''
  videos.value = []
  selectedVideo.value = null

  try {
    const resp = await api.getMachineVideos(machineId)
    videos.value = resp.data || []
    if (videos.value.length > 0) {
      selectedVideo.value = videos.value[videos.value.length - 1]
    }
  } catch (e) {
    console.error('Failed to load machine videos:', e)
    error.value = 'Failed to load videos.'
  } finally {
    isLoading.value = false
  }
}

function selectVideo(video) {
  selectedVideo.value = video
}

function goToMachine() {
  if (!machineIdInput.value) return
  router.push({ name: 'machine-info', params: { machine_id: machineIdInput.value } })
}

function reload() {
  loadVideos(machine_id.value)
}

watch(
  () => machine_id.value,
  (newId) => {
    machineIdInput.value = newId || ''
    loadVideos(newId)
  },
  { immediate: true }
)

onMounted(() => {
  machineIdInput.value = machine_id.value || ''
})
</script>

<style scoped>
.page {
  font-family: sans-serif;
  padding: 20px;
  max-width: 1800px;
  margin: 0 auto;
}

.topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.topbar h1 {
  margin: 0;
}

.actions {
  display: flex;
  gap: 8px;
  align-items: center;
}

.actions input {
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid #d1d5db;
  min-width: 220px;
}

.actions button {
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid #d1d5db;
  background: #fff;
  cursor: pointer;
}

.actions button:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.layout {
  display: grid;
  grid-template-columns: minmax(320px, 420px) 1fr;
  gap: 16px;
  align-items: start;
}

.error {
  background: #fef2f2;
  border: 1px solid #fecaca;
  color: #991b1b;
  padding: 10px 12px;
  border-radius: 10px;
  margin-bottom: 12px;
}

.loading {
  margin-top: 10px;
  color: #6b7280;
}
</style>
