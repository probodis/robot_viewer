<template>
  <section class="order-diagnostics" aria-labelledby="diagnostics-title">
    <h3 id="diagnostics-title">Logs</h3>
    <p>Choose a machine log to view entries around this order.</p>
    <button :disabled="isListing" @click="listFiles">
      {{
        isListing
          ? 'Loading log list...'
          : files === null
            ? 'Browse machine logs'
            : 'Refresh log list'
      }}
    </button>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <p v-if="files !== null && files.length === 0">No text logs are available for this machine.</p>
    <div v-if="files?.length" class="file-picker">
      <label>
        Filter logs
        <input v-model="filter" type="search" placeholder="Filename or folder" />
      </label>
      <label>
        Log file
        <select v-model="selectedKey" :disabled="isReading">
          <option value="" disabled>Select a file</option>
          <option v-for="file in matchingFiles" :key="file.log_key" :value="file.log_key">
            {{ file.log_key }}
          </option>
        </select>
      </label>
      <button :disabled="!selectedKey || isReading" @click="loadWindow">
        {{ isReading ? 'Loading log window...' : 'Load order window' }}
      </button>
      <a v-if="selectedKey" :href="fullLogUrl" target="_blank" rel="noopener">Open full file</a>
      <p v-if="matchingFiles.length === 0">No files match this filter.</p>
    </div>
    <p v-if="notice" role="status">{{ notice }}</p>
    <p v-if="Object.keys(logs).length">Double-click a tab to open the full file.</p>
    <LogTabs v-if="Object.keys(logs).length" :logs="logs" />
  </section>
</template>

<script setup>
import { computed, onBeforeUnmount, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '../services/api'
import LogTabs from './LogTabs.vue'

const props = defineProps({
  machineId: { type: String, required: true },
  orderId: { type: String, required: true },
})
const router = useRouter()
const fullLogUrl = computed(
  () =>
    router.resolve({
      name: 'logfile-view',
      query: { machine_id: props.machineId, log_key: selectedKey.value },
    }).href,
)
const files = ref(null)
const filter = ref('')
const selectedKey = ref('')
const logs = ref({})
const isListing = ref(false)
const isReading = ref(false)
const error = ref('')
const notice = ref('')
const controller = new AbortController()
const matchingFiles = computed(() => {
  const search = filter.value.trim().toLowerCase()
  return (files.value || []).filter((file) => file.log_key.toLowerCase().includes(search))
})

async function listFiles() {
  isListing.value = true
  error.value = ''
  try {
    const response = await api.getMachineLogs(props.machineId, { signal: controller.signal })
    if (!controller.signal.aborted) {
      files.value = response.data.filter((file) => /\.(txt|log)(\.gz)?$/i.test(file.log_key))
      if (!files.value.some((file) => file.log_key === selectedKey.value)) selectedKey.value = ''
    }
  } catch {
    if (!controller.signal.aborted) error.value = 'Unable to load the log list. Please try again.'
  } finally {
    if (!controller.signal.aborted) isListing.value = false
  }
}

async function loadWindow() {
  if (!selectedKey.value || isReading.value) return
  isReading.value = true
  error.value = ''
  notice.value = ''
  const logKey = selectedKey.value
  try {
    const response = await api.getOrderLogWindow(props.machineId, props.orderId, logKey, {
      signal: controller.signal,
    })
    if (controller.signal.aborted) return
    const window = response.data
    logs.value = { ...logs.value, [logKey]: { path: logKey, text: window.text } }
    const notices = []
    if (window.status === 'no_timestamp')
      notices.push('This file has no supported timestamps for an order window.')
    if (window.status === 'empty')
      notices.push('No entries were found in this file around the order time.')
    if (window.truncated)
      notices.push('The log window reached the reading limit. Open the full file for more entries.')
    if (
      logKey.startsWith('sauce_weight/') &&
      window.extra_weight_points_status === 'not_available'
    ) {
      notices.push('Additional weight points are unavailable for this log.')
    }
    notice.value = notices.join(' ')
  } catch (failure) {
    if (!controller.signal.aborted) {
      error.value =
        failure.response?.status === 409
          ? 'This order has no recorded duration. Open the full file to inspect its entries.'
          : 'Unable to load this log window. Please try again.'
    }
  } finally {
    if (!controller.signal.aborted) isReading.value = false
  }
}

onBeforeUnmount(() => controller.abort())
</script>

<style scoped>
.order-diagnostics {
  margin: 30px 0 20px;
}
.file-picker {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: end;
  margin-top: 16px;
}
label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-width: 100%;
}
input,
select,
button {
  padding: 8px 12px;
}
select {
  max-width: 100%;
  width: 460px;
}
button {
  cursor: pointer;
}
button:disabled {
  cursor: default;
}
.error {
  color: #991b1b;
}
</style>
