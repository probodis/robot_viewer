import axios from 'axios'

const apiBaseURL = import.meta.env.VITE_API_BASE_URL || '/robot_viewer/api'
const apiClient = axios.create({
  baseURL: apiBaseURL,
  timeout: 20000,
  headers: { 'Content-Type': 'application/json' },
})

export default {
  /** Load a machine-scoped order. Credentials for PostgREST remain on the backend. */
  getOrderData: (machineId, orderId, options = {}) =>
    apiClient.get('/v1/orders/', {
      ...options,
      params: { machine_id: machineId, order_id: orderId },
    }),

  getLogFile: (machineId, logKey) =>
    apiClient.get('/v1/log', { params: { machine_id: machineId, log_key: logKey } }),

  /** Stream the original file without decoding it into the browser log view. */
  getLogDownloadUrl: (machineId, logKey) =>
    `${apiBaseURL.replace(/\/$/, '')}/v1/machine/logs/download?${new URLSearchParams({ machine_id: machineId, log_key: logKey })}`,

  /** List machine files only after the user opens diagnostics. */
  getMachineLogs: (machineId, options = {}) =>
    apiClient.get('/v1/machine/logs', { ...options, params: { machine_id: machineId } }),

  /** Extract a bounded order window from one explicitly selected file. */
  getOrderLogWindow: (machineId, orderId, logKey, options = {}) =>
    apiClient.get('/v1/orders/log-window', {
      ...options,
      params: { machine_id: machineId, order_id: orderId, log_key: logKey },
    }),

  /** Fetch the latest available videos for a machine. */
  getMachineVideos: (machineId) =>
    apiClient.get('/v1/machine/videos', { params: { machine_id: machineId } }),

  /** Fetch the scanner archive; 404 means no matching archive is available. */
  getScannerArchive: (machineId, orderId, options = {}) =>
    apiClient.get('/v1/orders/scanner', {
      ...options,
      params: { machine_id: machineId, order_id: orderId },
    }),

  getBackendVersion: () => apiClient.get('/v1/version'),
}
