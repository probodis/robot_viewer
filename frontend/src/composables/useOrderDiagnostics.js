import { computed, onBeforeUnmount, ref, watch } from 'vue'
import api from '../services/api'

/** Keep automatic diagnostics and manual windows independent from the order/media request. */
export function useOrderDiagnostics(identity, client = api) {
  const automaticLogs = ref({})
  const manualLogs = ref({})
  const files = ref(null)
  const selectedKey = ref('')
  const activeLogKey = ref(null)
  const isAutoLoading = ref(false)
  const autoError = ref('')
  const autoNotice = ref('')
  const isListing = ref(false)
  const listError = ref('')
  const isReading = ref(false)
  const manualError = ref('')
  const manualNotice = ref('')
  const controllers = {}
  let disposed = false

  // An explicit file choice always wins over a slower automatic response or retry.
  const logs = computed(() => ({ ...automaticLogs.value, ...manualLogs.value }))

  function begin(kind) {
    controllers[kind]?.abort()
    const controller = new AbortController()
    controllers[kind] = controller
    return controller
  }

  function current(kind, controller) {
    return !disposed && controllers[kind] === controller && !controller.signal.aborted
  }

  async function loadDefaultLogs() {
    const [machineId, orderId] = identity()
    if (!machineId || !orderId || disposed) return
    const controller = begin('automatic')
    isAutoLoading.value = true
    autoError.value = ''
    autoNotice.value = ''
    try {
      const response = await client.getOrderLogs(machineId, orderId, {
        signal: controller.signal,
      })
      if (!current('automatic', controller)) return
      const result = response.data
      if (!result?.logs || typeof result.logs !== 'object' || Array.isArray(result.logs)) {
        throw new Error('Invalid automatic logs response')
      }
      automaticLogs.value = result.logs
      if (result.status === 'partial' || result.truncated) {
        autoNotice.value =
          'Some automatic logs could not be fully loaded. Retry the logs or choose a file below.'
      }
    } catch (failure) {
      if (current('automatic', controller)) {
        autoError.value =
          failure.response?.status === 409
            ? 'This order has no recorded duration for automatic log windows. You can open a full file below.'
            : 'Automatic logs could not be loaded. Try again or choose a file below.'
      }
    } finally {
      if (current('automatic', controller)) isAutoLoading.value = false
    }
  }

  async function listFiles() {
    if (isListing.value || disposed) return
    const [machineId] = identity()
    const controller = begin('list')
    isListing.value = true
    listError.value = ''
    try {
      const response = await client.getMachineLogs(machineId, { signal: controller.signal })
      if (!current('list', controller)) return
      if (!Array.isArray(response.data)) throw new Error('Invalid machine logs response')
      files.value = response.data.filter(
        (file) => typeof file.log_key === 'string' && /\.(txt|log)(\.gz)?$/i.test(file.log_key),
      )
      if (!files.value.some((file) => file.log_key === selectedKey.value)) selectedKey.value = ''
    } catch {
      if (current('list', controller)) {
        listError.value = 'Unable to load the log list. Please try again.'
      }
    } finally {
      if (current('list', controller)) isListing.value = false
    }
  }

  async function loadWindow() {
    if (!selectedKey.value || isReading.value || disposed) return
    const [machineId, orderId] = identity()
    const logKey = selectedKey.value
    const controller = begin('manual')
    isReading.value = true
    manualError.value = ''
    manualNotice.value = ''
    try {
      const response = await client.getOrderLogWindow(machineId, orderId, logKey, {
        signal: controller.signal,
      })
      if (!current('manual', controller)) return
      const window = response.data
      manualLogs.value = { ...manualLogs.value, [logKey]: { path: logKey, text: window.text } }
      activeLogKey.value = logKey
      const notices = []
      if (window.status === 'no_timestamp') {
        notices.push('This file has no supported timestamps for an order window.')
      }
      if (window.status === 'empty') {
        notices.push('No entries were found in this file around the order time.')
      }
      if (window.truncated) {
        notices.push(
          'The log window reached the reading limit. Open the full file for more entries.',
        )
      }
      if (
        logKey.startsWith('sauce_weight/') &&
        window.extra_weight_points_status === 'not_available'
      ) {
        notices.push('Additional weight points are unavailable for this log.')
      }
      manualNotice.value = notices.join(' ')
    } catch (failure) {
      if (current('manual', controller)) {
        manualError.value =
          failure.response?.status === 409
            ? 'This order has no recorded duration. Open the full file to inspect its entries.'
            : 'Unable to load this log window. Please try again.'
      }
    } finally {
      if (current('manual', controller)) isReading.value = false
    }
  }

  function resetAndLoad() {
    Object.values(controllers).forEach((controller) => controller.abort())
    automaticLogs.value = {}
    manualLogs.value = {}
    files.value = null
    selectedKey.value = ''
    activeLogKey.value = null
    isAutoLoading.value = false
    autoError.value = ''
    autoNotice.value = ''
    isListing.value = false
    listError.value = ''
    isReading.value = false
    manualError.value = ''
    manualNotice.value = ''
    void loadDefaultLogs()
  }

  watch(identity, resetAndLoad, { immediate: true })
  onBeforeUnmount(() => {
    disposed = true
    Object.values(controllers).forEach((controller) => controller.abort())
  })

  return {
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
  }
}
