import axios from 'axios';

const apiClient = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
});

export default {
  getOrderData: (orderId) => apiClient.get(`/v1/orders/${orderId}`),
  // Use absolute path to match backend route (not proxied)
  getVideoUrl: (filename) => `/videos/${filename}`,
  getBackendVersion: () => apiClient.get('/v1/version'),
};
