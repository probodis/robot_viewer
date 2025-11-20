import axios from 'axios';

const apiClient = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
});

export default {
  /**
   * Fetch order telemetry data for a specific machine and order.
   * @param {string} machineId - Machine identifier (e.g., "cb-3-0020")
   * @param {number} orderId - Order timestamp
   * @returns {Promise} Axios response promise
   */
  getOrderData: (machineId, orderId) =>
    apiClient.get(`/v1/orders/`, { params: { machine_id: machineId, order_id: orderId } }),

  /**
   * Fetch backend version info.
   * @returns {Promise} Axios response promise
   */
  getBackendVersion: () => apiClient.get('/v1/version'),
};
