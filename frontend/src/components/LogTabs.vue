<template>
  <div class="log-tabs">
    <!-- Tabs -->
    <div class="tabs" role="tablist" aria-label="Logs">
      <button
        v-for="(item, filename) in logs"
        :key="filename"
        :class="['tab', { active: activeTab === filename }]"
        @click="activeTab = filename"
        @dblclick="() => openLogFile(filename)"
        role="tab"
        :aria-selected="activeTab === filename"
        title="Double-click to open full log file"
      >
        {{ filename }}
      </button>
    </div>

    <!-- Content -->
    <div v-if="activeTab" class="log-content" role="region">
      <pre>{{ decodedLogs[activeTab] }}</pre>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, computed } from "vue"
import { useRouter, useRoute } from "vue-router"

const props = defineProps({
  logs: {
    type: Object,
    required: true
  }
});

const activeTab = ref(null)
const router = useRouter()
const route = useRoute()

const decodedLogs = computed(() => {
  const result = {}
  for (const [filename, entry] of Object.entries(props.logs || {})) {
    // defensive: entry may be null / missing text
    try {
      result[filename] = entry?.text ? atob(entry.text) : "";
    } catch (e) {
      result[filename] = entry?.text ?? ""
    }
  }
  return result
})

watch(
  () => props.logs,
  (logs) => {
    if (logs && Object.keys(logs).length > 0) {
      activeTab.value = Object.keys(logs)[0]
    } else {
      activeTab.value = null
    }
  },
  { immediate: true }
)

/**
 * Opens the log file in a new browser tab/window.
 *
 * @param {string} key - The log file key to open.
 */
function openLogFile(key) {
    // Build the target route location using current machine_id and log_key
    const machine_id = route.query.machine_id || "";

    // Generate the URL using the router's resolve method
    const location = router.resolve({
        name: "logfile-view",
        query: {
            machine_id,
            log_key: key
        }
    });

    // Open the resolved URL in a new browser tab
    window.open(location.href, "_blank");
}
</script>

<style scoped>

.log-tabs {
  border: 1px solid #e0e0e0;
  border-radius: 8px;
  background: #fafbfc;
  max-width: 100%;
  display: flex;
  flex-direction: column;

  min-height: 180px;
  height: 500px;
  box-sizing: border-box;
}


.tabs {
  display: flex;
  gap: 4px;
  align-items: center;
  padding: 8px;
  border-bottom: 1px solid #e0e0e0;
  background: #f5f7fa;
  overflow-x: auto;

  overflow-y: hidden;
}


.tab {
  flex: 0 0 auto;
  padding: 6px 12px;
  cursor: pointer;
  background: none;
  border: 1px solid rgba(0,0,0,0.08);
  border-radius: 6px;
  outline: none;
  font-size: 0.95rem;
  color: #555;
  transition: background 0.12s, color 0.12s, transform 0.08s;
  white-space: nowrap;
  border-bottom: 2px solid transparent; /* fix height jump on active tab */
}

.tab.active {
  background: #fff;
  color: #1976d2;
  border-bottom: 2px solid #1976d2;
  font-weight: 600;
  transform: translateY(-1px);
}

.tab:not(.active):hover {
  background: #f0f0f0;
}

.log-content {
  padding: 16px;
  background: #fff;
  color: #222;
  font-family: "Fira Mono", "Consolas", monospace;
  font-size: 0.76rem;
  border-radius: 0 0 8px 8px;
  overflow: auto;
  flex: 1 1 0%;
  min-height: 0;
  box-sizing: border-box;
  margin-top: 8px;
  /* always fill available space, do not resize based on content */
  height: 100%;
}

pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  line-height: 1.4;
  color: #111;
}

.log-content::-webkit-scrollbar {
  width: 10px;
}
.log-content::-webkit-scrollbar-thumb {
  background: rgba(0,0,0,0.2);
  border-radius: 6px;
}
</style>
