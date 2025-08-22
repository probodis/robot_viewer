import axios from 'axios';

// Use the environment variable for the API URL, with a fallback for local development.
const baseURL = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';

const apiClient = axios.create({
  baseURL: baseURL,
  headers: {
    'Content-Type': 'application/json',
  },
});

export default {
  getOrderList() {
    return apiClient.get('/api/v1/orders');
  },
  getOrderData(orderId) {
    return apiClient.get(`/api/v1/orders/${orderId}`);
  },
  getVideoUrl(filename) {
    // Construct the full URL for the video file.
    return `${apiClient.defaults.baseURL}/videos/${filename}`;
  },
  getBackendVersion() {
    return apiClient.get('/api/v1/version');
  }
};
