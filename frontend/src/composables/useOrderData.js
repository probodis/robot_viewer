import { onBeforeUnmount, ref, watch } from 'vue'
import api from '../services/api'

function orderErrorMessage(error) {
  switch (error.response?.status) {
    case 404:
      return 'This order was not found for the selected machine.'
    case 400:
    case 422:
      return 'Check the machine ID and order UID, then try again.'
    case 502:
    case 503:
    case 504:
      return 'Order data is temporarily unavailable. Please try again shortly.'
    default:
      return 'Unable to load this order. Check your connection and try again.'
  }
}

/** Load a machine-scoped order; navigation cancels requests for the previous order. */
export function useOrderData(identity) {
  const orderData = ref(null)
  const isLoading = ref(false)
  const orderError = ref('')
  const scannerArchive = ref(null)
  const isScannerLoading = ref(false)
  const scannerError = ref('')
  const scannerSearchAttempted = ref(false)
  let controller

  async function loadOrder(machineId, orderId, signal) {
    try {
      const response = await api.getOrderData(machineId, orderId, { signal })
      if (!signal.aborted) orderData.value = response.data
    } catch (error) {
      if (!signal.aborted) orderError.value = orderErrorMessage(error)
    } finally {
      if (!signal.aborted) isLoading.value = false
    }
  }

  async function loadScanner(machineId, orderId, signal) {
    try {
      const response = await api.getScannerArchive(machineId, orderId, { signal })
      if (!signal.aborted) scannerArchive.value = response.data
    } catch (error) {
      if (!signal.aborted && error.response?.status !== 404) {
        scannerError.value = 'Scanner archive is temporarily unavailable.'
      }
    } finally {
      if (!signal.aborted) isScannerLoading.value = false
    }
  }

  function findScanner() {
    if (isScannerLoading.value) return
    const [orderId, machineId] = identity()
    if (!machineId || !orderId || !controller) return
    isScannerLoading.value = true
    scannerSearchAttempted.value = true
    scannerError.value = ''
    void loadScanner(machineId, orderId, controller.signal)
  }

  function reload() {
    controller?.abort()
    controller = new AbortController()
    orderData.value = null
    orderError.value = ''
    scannerArchive.value = null
    scannerError.value = ''
    scannerSearchAttempted.value = false
    const [orderId, machineId] = identity()
    isLoading.value = Boolean(machineId && orderId)
    isScannerLoading.value = false
    if (!isLoading.value) return
    const { signal } = controller
    void loadOrder(machineId, orderId, signal)
  }

  watch(identity, reload, { immediate: true })
  onBeforeUnmount(() => controller?.abort())
  return {
    orderData,
    isLoading,
    orderError,
    scannerArchive,
    isScannerLoading,
    scannerError,
    scannerSearchAttempted,
    findScanner,
    reload,
  }
}
