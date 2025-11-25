<template>
  <div class="logfile-view-container">
    <h2>Log File Viewer</h2>

    <div v-if="isLoading">Loading log file...</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else>
      <div class="logfile-meta">
        <span>Machine: <b>{{ machineId }}</b></span>
        <span style="margin-left:20px">File: <b>{{ logKey }}</b></span>
      </div>

      <pre class="logfile-content" v-html="highlighted"></pre>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import api from '../services/api'

const route = useRoute()
const machineId = route.query.machine_id || ''
const logKey = route.query.log_key || ''

const isLoading = ref(true)
const error = ref('')
const decoded = ref('')

function decodeBase64(text) {
  try {
    return decodeURIComponent(escape(window.atob(text)))
  } catch {
    return '[Failed to decode log file]'
  }
}

const highlighted = computed(() => {
  if (!decoded.value) return ''

  return decoded.value
    .replace(/error/gi, '<span class="log-error">$&</span>')
    .replace(/debug/gi, '<span class="log-debug">$&</span>')
    .replace(/warn(ing)?/gi, '<span class="log-warning">$&</span>')
})

async function fetchLog() {
  try {
    const resp = await api.getLogFile(machineId, logKey)
    decoded.value = decodeBase64(resp.data.text)
  } catch {
    error.value = 'Failed to load log file.'
  } finally {
    isLoading.value = false
  }
}

onMounted(fetchLog)
</script>

<style scoped>
.logfile-view-container {
  max-width: 1200px;
  margin: 0 auto;
  padding: 20px;
}

.logfile-meta {
  margin-bottom: 10px;
  color: #555;
}

.logfile-content {
  background: #222;
  color: #e0e0e0;
  padding: 16px;
  border-radius: 6px;
  font-size: 14px;
  overflow-x: auto;
  white-space: pre-wrap;
}

/* highlight (works with v-html only via :deep) */
:deep(.log-error) {
  color: #ff4e4e;
  font-weight: bold;
}

:deep(.log-debug) {
  color: #4ea3ff;
}

:deep(.log-warning) {
  color: #f6d96b;
}

.error {
  color: #c00;
  margin-bottom: 10px;
}
</style>
