import axios from 'axios';

const apiClient = axios.create({
  baseURL: '/api',
  headers: { 'Content-Type': 'application/json' },
});

export default {
  getOrderList: () => apiClient.get('/v1/orders'),
  getOrderData: (orderId) => apiClient.get(`/v1/orders/${orderId}`),
  getVideoUrl: (filename) => `${apiClient.defaults.baseURL}/videos/${filename}`,
  getBackendVersion: () => apiClient.get('/v1/version'),
};
