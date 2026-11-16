<template>
  <div class="viewer-container">
    <h1>Robot Telemetry Viewer</h1>

    <div class="order-selector">
      <label for="machine-input">Enter machine ID:</label>
      <input
        id="machine-input"
        type="text"
        v-model="selectedMachineId"
        @keyup.enter="navigateToOrder"
        placeholder="e.g. cb-3-0020"
        style="margin-left: 5px"
        autocomplete="off"
      />
      <label for="order-input" style="margin-left: 10px">Enter order ID:</label>
      <input
        id="order-input"
        type="text"
        v-model="selectedOrderId"
        @keyup.enter="navigateToOrder"
        placeholder="Type order ID and press Enter"
        autocomplete="off"
        style="margin-left: 5px"
      />
      <button
        @click="navigateToOrder"
        :disabled="!selectedOrderId || !selectedMachineId"
        style="margin-left: 5px"
      >
        Load
      </button>
    </div>

    <div v-if="orderData">
      <div class="main-content">
        <!-- Left column: video only -->
        <div class="left-column">
          <div class="video-section">
            <h3>Order Video: {{ orderData.order_id }}</h3>
            <video
              v-if="orderData.video_path"
              :key="orderData.order_id"
              ref="videoPlayer"
              :src="orderData.video_path"
              controls
              @timeupdate="handleVideoTimeUpdate"
              @seeking="handleVideoSeeking"
              @pause="isPaused = true"
              @play="isPaused = false"
              muted
              @error="videoPlaybackError = true"
            ></video>
            <p v-if="!orderData.video_path" role="status">{{ videoStatusMessage }}</p>
            <p v-else-if="videoPlaybackError" role="alert">
              Video could not be played. Reload the order to refresh its video link.
              <button @click="reloadOrder">Reload order</button>
            </p>
          </div>
          <div class="scanner-section">
            <a
              v-if="scannerArchive"
              :href="scannerArchive.url"
              class="scanner-btn scanner-btn--ready"
              target="_blank"
              rel="noopener"
              >Download Scanner Archive</a
            >
            <button
              v-else
              class="scanner-btn scanner-btn--search"
              :disabled="isScannerLoading"
              @click="findScanner"
            >
              {{
                isScannerLoading
                  ? 'Searching scanner archive...'
                  : scannerError ||
                    (scannerSearchAttempted
                      ? 'Scanner archive not found — search again'
                      : 'Find scanner archive')
              }}
            </button>
          </div>
        </div>

        <!-- Right column: charts -->
        <div class="charts-section">
          <h3>Telemetry Charts</h3>

          <p v-if="telemetryStatusMessage" role="status">{{ telemetryStatusMessage }}</p>
          <template v-if="orderData.telemetry_status === 'available'">
            <WeightChart
              v-if="weightSeries.length > 0"
              title="Weight (grams)"
              :weightData="weightSeries[0].data"
              :extraPoints="weightExtraPoints"
              :currentTime="syncedTime"
              @time-updated="handleChartTimeUpdate"
            />

            <TelemetryChart
              title="Velocity"
              :chartData="velocitySeries"
              :currentTime="syncedTime"
              @time-updated="handleChartTimeUpdate"
            />

            <TelemetryChart
              title="Position"
              :chartData="positionSeries"
              :currentTime="syncedTime"
              @time-updated="handleChartTimeUpdate"
            />

            <TelemetryChart
              title="State"
              :chartData="stateSeries"
              :currentTime="syncedTime"
              yAxisType="category"
              @time-updated="handleChartTimeUpdate"
            />
          </template>
        </div>
      </div>

      <OrderDiagnostics
        :key="`${route.query.machine_id}:${orderData.order_id}`"
        :machine-id="String(route.query.machine_id)"
        :order-id="orderData.order_id"
      />
    </div>

    <div v-else-if="isLoading">
      <p>Loading data...</p>
    </div>

    <div v-else-if="orderError" class="order-error" role="alert">
      <p>{{ orderError }}</p>
      <button @click="reloadOrder">Try again</button>
    </div>

    <div v-else>
      <p>Please enter a machine ID and order UID to view.</p>
    </div>

    <div class="version-info">
      <span>Frontend: v{{ frontendVersion }}</span> | <span>Backend: v{{ backendVersion }}</span>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, watch, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { version as frontendVersion } from '../../package.json'
import api from '../services/api'
import TelemetryChart from '../components/TelemetryChart.vue'
import WeightChart from '../components/WeightChart.vue'
import OrderDiagnostics from '../components/OrderDiagnostics.vue'
import { useOrderData } from '../composables/useOrderData'

const router = useRouter()
const route = useRoute()

const selectedOrderId = ref('')
const selectedMachineId = ref('')
const isPaused = ref(true)

const videoPlayer = ref(null)
const syncedTime = ref(0)
const backendVersion = ref('N/A')

const videoPlaybackError = ref(false)
const identity = () => [route.query.order_id, route.query.machine_id]
const {
  orderData,
  isLoading,
  orderError,
  scannerArchive,
  isScannerLoading,
  scannerError,
  scannerSearchAttempted,
  findScanner,
  reload,
} = useOrderData(identity)

const telemetryStatusMessage = computed(
  () =>
    ({
      not_recorded: 'No telemetry is linked to this order. Available video can still be played.',
      not_uploaded: 'Telemetry has not arrived in storage. Try reloading the order later.',
      invalid: 'Telemetry could not be read for this order. Available video can still be played.',
      unavailable: 'Telemetry is temporarily unavailable. Try reloading the order shortly.',
    })[orderData.value?.telemetry_status] || '',
)

const videoStatusMessage = computed(() =>
  orderData.value?.video_status === 'not_recorded'
    ? 'No video is linked to this order.'
    : 'Video is temporarily unavailable. Try reloading the order shortly.',
)

function reloadOrder() {
  videoPlaybackError.value = false
  syncedTime.value = 0
  isPaused.value = true
  reload()
}

onMounted(async () => {
  try {
    if (route.query.order_id) selectedOrderId.value = route.query.order_id
    if (route.query.machine_id) selectedMachineId.value = route.query.machine_id
    const versionResponse = await api.getBackendVersion()
    backendVersion.value = versionResponse.data.version
  } catch (error) {
    console.error('Failed to perform initial data load:', error)
  }
})

watch(
  identity,
  ([newOrderId, newMachineId]) => {
    selectedOrderId.value = newOrderId || ''
    selectedMachineId.value = newMachineId || ''
    syncedTime.value = 0
    isPaused.value = true
    videoPlaybackError.value = false
  },
  { immediate: true },
)

function navigateToOrder() {
  if (selectedOrderId.value && selectedMachineId.value) {
    router.push({
      name: 'robot-viewer',
      query: { order_id: selectedOrderId.value, machine_id: selectedMachineId.value },
    })
  }
}

const velocitySeries = computed(() => {
  if (!orderData.value) return []
  return Object.entries(orderData.value.motors || {}).map(([motorName, motorData]) => ({
    name: motorName,
    data: motorData.velocity,
  }))
})

const positionSeries = computed(() => {
  if (!orderData.value) return []
  return Object.entries(orderData.value.motors || {}).map(([motorName, motorData]) => ({
    name: motorName,
    data: motorData.position,
  }))
})

const stateSeries = computed(() => {
  if (!orderData.value) return []
  return Object.entries(orderData.value.motors || {}).map(([motorName, motorData]) => ({
    name: motorName,
    data: motorData.state,
  }))
})

const weightSeries = computed(() => {
  if (!orderData.value) return []
  const series = []
  const screenMotor = orderData.value.motors?.screen
  if (screenMotor && screenMotor.weight) series.push({ name: 'weight', data: screenMotor.weight })
  return series
})

const weightExtraPoints = computed(() => {
  if (!orderData.value || !orderData.value.extra_weight_points) return []

  // extra_weight_points is expected to be a list of { name, time, value }
  return orderData.value.extra_weight_points
})

function handleVideoTimeUpdate(event) {
  syncedTime.value = event.target.currentTime
}
function handleVideoSeeking(event) {
  syncedTime.value = event.target.currentTime
}
function handleChartTimeUpdate(newTime) {
  if (!Number.isFinite(newTime)) return
  syncedTime.value = Math.max(0, newTime)
  if (videoPlayer.value) {
    if (!videoPlayer.value.paused) videoPlayer.value.pause()
    videoPlayer.value.currentTime = syncedTime.value
  }
}
</script>

<style scoped>
.viewer-container {
  font-family: sans-serif;
  padding: 20px;
  max-width: 1800px;
  margin: 0 auto;
}

.order-selector {
  margin-bottom: 20px;
}

.main-content {
  display: grid;
  grid-template-columns: minmax(500px, 1fr) 2fr;
  gap: 30px;
  align-items: start;
}

.left-column {
  display: flex;
  flex-direction: column;
  position: sticky;
  top: 20px;
  align-self: start;
}

.video-section {
  margin-bottom: 0;
}

.logs-section,
.logs-section-below {
  margin-top: 30px;
  margin-bottom: 20px;
  grid-column: 1 / -1;

  min-height: 200px;
  overflow: auto;
}

video {
  width: 100%;
  border: 1px solid #ccc;
}

.scanner-section {
  margin-top: 12px;
}

.scanner-btn {
  display: block;
  width: 100%;
  padding: 10px 16px;
  border-radius: 6px;
  font-size: 14px;
  font-weight: 500;
  text-align: center;
  text-decoration: none;
  box-sizing: border-box;
  border: none;
  transition: background-color 0.15s;
}

.scanner-btn--ready {
  background-color: #1976d2;
  color: #fff;
  cursor: pointer;
}

.scanner-btn--ready:hover {
  background-color: #1565c0;
}

.scanner-btn--search {
  background-color: #e5e7eb;
  color: #374151;
  cursor: pointer;
}

.scanner-btn--search:disabled {
  color: #9ca3af;
  cursor: default;
}

.charts-section {
  flex: 1;
}

.order-error {
  padding: 12px 16px;
  border: 1px solid #fecaca;
  background: #fef2f2;
  color: #991b1b;
  border-radius: 6px;
}

.version-info {
  position: fixed;
  bottom: 10px;
  right: 15px;
  font-size: 12px;
  color: #888;
}
</style>
