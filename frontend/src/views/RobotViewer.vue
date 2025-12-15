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
        style="margin-left: 5px;"
        autocomplete="off"
      />
      <label for="order-input" style="margin-left: 10px;">Enter order ID:</label>
      <input
        id="order-input"
        type="text"
        v-model="selectedOrderId"
        @keyup.enter="navigateToOrder"
        placeholder="Type order ID and press Enter"
        autocomplete="off"
        style="margin-left: 5px;"
      />
      <button
        @click="navigateToOrder" :disabled="!selectedOrderId || !selectedMachineId"
        style="margin-left: 5px;"
      >Load</button>
    </div>

    <div v-if="orderData">
      <div class="main-content">
        <!-- Left column: video only -->
        <div class="left-column">
          <div class="video-section">
            <h3>Order Video: {{ orderData.order_id }}</h3>
            <video
              ref="videoPlayer"
              :src="orderData.video_path"
              controls
              @timeupdate="handleVideoTimeUpdate"
              @seeking="handleVideoSeeking"
              @pause="isPaused = true"
              @play="isPaused = false"
              muted
            ></video>
          </div>
        </div>

        <!-- Right column: charts -->
        <div class="charts-section">
          <h3>Telemetry Charts</h3>

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
        </div>
      </div>

      <!-- Logs below both columns -->
      <div class="logs-section-below">
        <h3>Logs</h3>
        <LogTabs v-if="orderData?.logs" :logs="orderData.logs" />
      </div>
    </div>

    <div v-else-if="isLoading">
      <p>Loading data...</p>
    </div>

    <div v-else>
      <p>Please enter an order UID to view.</p>
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
import LogTabs from '../components/LogTabs.vue'

const router = useRouter()
const route = useRoute()

const selectedOrderId = ref('')
const selectedMachineId = ref('')
const orderData = ref(null)
const isLoading = ref(false)
const isPaused = ref(true)

const videoPlayer = ref(null)
const syncedTime = ref(0)
const backendVersion = ref('N/A')

onMounted(async () => {
  try {
    if (route.query.order_id) selectedOrderId.value = route.query.order_id
    if (route.query.machine_id) selectedMachineId.value = route.query.machine_id
    const versionResponse = await api.getBackendVersion();
    backendVersion.value = versionResponse.data.version;
  } catch (error) {
    console.error('Failed to perform initial data load:', error)
  }
})

async function fetchOrderData(machineId, orderId) {
  if (!machineId || !orderId) {
    orderData.value = null
    return
  }
  isLoading.value = true
  orderData.value = null
  syncedTime.value = 0
  try {
    const response = await api.getOrderData(machineId, orderId)
    orderData.value = response.data
  } catch (error) {
    console.error(`Failed to load data for machine ${machineId}, order ${orderId}:`, error)
  } finally {
    isLoading.value = false
  }
}

watch(
  () => [route.query.order_id, route.query.machine_id],
  ([newOrderId, newMachineId]) => {
    if (newOrderId) selectedOrderId.value = newOrderId
    if (newMachineId) selectedMachineId.value = newMachineId
    if (newOrderId && newMachineId) fetchOrderData(newMachineId, newOrderId)
  },
  { immediate: true }
)

function navigateToOrder() {
  if (selectedOrderId.value && selectedMachineId.value) {
    router.push({ name: 'robot-viewer', query: { order_id: selectedOrderId.value, machine_id: selectedMachineId.value } })
  }
}

const velocitySeries = computed(() => {
  if (!orderData.value) return []
  return Object.entries(orderData.value.motors).map(([motorName, motorData]) => ({
    name: motorName,
    data: motorData.velocity,
  }))
})

const positionSeries = computed(() => {
  if (!orderData.value) return []
  return Object.entries(orderData.value.motors).map(([motorName, motorData]) => ({
    name: motorName,
    data: motorData.position,
  }))
})

const stateSeries = computed(() => {
  if (!orderData.value) return []
  return Object.entries(orderData.value.motors).map(([motorName, motorData]) => ({
    name: motorName,
    data: motorData.state,
  }))
})

const weightSeries = computed(() => {
  if (!orderData.value) return []
  const series = []
  const screenMotor = orderData.value.motors.screen
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
  syncedTime.value = newTime;
  if (videoPlayer.value) {
    if (!videoPlayer.value.paused) videoPlayer.value.pause();
    videoPlayer.value.currentTime = newTime;
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

.charts-section {
  flex: 1;
}

.version-info {
  position: fixed;
  bottom: 10px;
  right: 15px;
  font-size: 12px;
  color: #888;
}
</style>
