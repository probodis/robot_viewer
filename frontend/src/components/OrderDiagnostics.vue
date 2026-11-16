<template>
  <section class="order-diagnostics" aria-labelledby="diagnostics-title">
    <div class="logs-heading">
      <h3 id="diagnostics-title">Logs</h3>
      <button :disabled="isAutoLoading" @click="loadDefaultLogs">
        {{ isAutoLoading ? 'Loading logs...' : autoError ? 'Retry automatic logs' : 'Reload logs' }}
      </button>
    </div>
    <p v-if="isAutoLoading" role="status">Loading order logs...</p>
    <p v-if="autoError" role="alert" class="error">{{ autoError }}</p>
    <p v-if="autoNotice" role="status">{{ autoNotice }}</p>
    <p v-if="!isAutoLoading && !autoError && !Object.keys(logs).length" role="status">
      No automatic log files are available for this order.
    </p>
    <p v-if="Object.keys(logs).length">Double-click a tab to open the full file.</p>
    <LogTabs v-if="Object.keys(logs).length" v-model:active-key="activeLogKey" :logs="logs" />

    <details class="manual-selection">
      <summary>Choose another log file</summary>
      <p>Add a specific file to the tabs, or open its full contents.</p>
      <button :disabled="isListing" @click="listFiles">
        {{
          isListing
            ? 'Loading log list...'
            : files === null
              ? 'Browse machine logs'
              : 'Refresh log list'
        }}
      </button>
      <p v-if="listError" role="alert" class="error">{{ listError }}</p>
      <p v-if="files !== null && files.length === 0">
        No text logs are available for this machine.
      </p>
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
      <p v-if="manualError" role="alert" class="error">{{ manualError }}</p>
      <p v-if="manualNotice" role="status">{{ manualNotice }}</p>
    </details>
  </section>
</template>

<script setup>
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import LogTabs from './LogTabs.vue'
import { useOrderDiagnostics } from '../composables/useOrderDiagnostics'

const props = defineProps({
  machineId: { type: String, required: true },
  orderId: { type: String, required: true },
})
const router = useRouter()
const filter = ref('')
const identity = () => [props.machineId, props.orderId]
const {
  logs,
  files,
  selectedKey,
  activeLogKey,
  isAutoLoading,
  autoError,
  autoNotice,
  isListing,
  listError,
  isReading,
  manualError,
  manualNotice,
  loadDefaultLogs,
  listFiles,
  loadWindow,
} = useOrderDiagnostics(identity)
const fullLogUrl = computed(
  () =>
    router.resolve({
      name: 'logfile-view',
      query: { machine_id: props.machineId, log_key: selectedKey.value },
    }).href,
)
const matchingFiles = computed(() => {
  const search = filter.value.trim().toLowerCase()
  return (files.value || []).filter((file) => file.log_key.toLowerCase().includes(search))
})
</script>

<style scoped>
.order-diagnostics {
  margin: 30px 0 20px;
}
.logs-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.manual-selection {
  margin-top: 16px;
}
summary {
  cursor: pointer;
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
