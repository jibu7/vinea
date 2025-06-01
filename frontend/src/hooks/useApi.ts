import axios, { AxiosInstance } from 'axios';
import { useAuth } from '@/contexts/AuthContext';
import { useEffect } from 'react';

export function useApi(): AxiosInstance {
  const { logout } = useAuth();

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
        logout();
      }
      return Promise.reject(error);
    }
  );

  return api;
} 