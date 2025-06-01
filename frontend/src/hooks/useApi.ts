import axios, { AxiosInstance } from 'axios';

export function useApi(): AxiosInstance {
  const api = axios.create({
    baseURL: process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000',
    withCredentials: true,
  });

  // Request interceptor to add auth token
  api.interceptors.request.use(
    (config) => {
      // Get token from localStorage or cookie
      const token = localStorage.getItem('access_token');
      if (token) {
        config.headers.Authorization = `Bearer ${token}`;
        console.log('Adding auth token to request:', config.url);
      } else {
        console.warn('No auth token found for request:', config.url);
      }
      return config;
    },
    (error) => {
      return Promise.reject(error);
    }
  );

  // Response interceptor to handle auth errors
  api.interceptors.response.use(
    (response) => response,
    (error) => {
      if (error.response?.status === 401) {
        // Token expired or invalid
        // Clear token and redirect to login
        localStorage.removeItem('access_token');
        window.location.href = '/login';
      }
      return Promise.reject(error);
    }
  );

  return api;
} 