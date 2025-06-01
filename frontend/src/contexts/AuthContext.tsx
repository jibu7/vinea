'use client';

import React, { createContext, useContext, useState, useEffect, ReactNode } from 'react';
import { User, LoginCredentials, TokenResponse } from '@/types/auth';
import { useApi } from '@/hooks/useApi';
import { useRouter } from 'next/navigation';

interface AuthContextType {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (credentials: LoginCredentials) => Promise<void>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};

interface AuthProviderProps {
  children: ReactNode;
}

export const AuthProvider: React.FC<AuthProviderProps> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const router = useRouter();
  const api = useApi();

  const isAuthenticated = !!user;

  const refreshUser = async () => {
    try {
      // Check if we have a token stored
      const token = localStorage.getItem('access_token');
      if (!token) {
        setUser(null);
        return;
      }
      
      const response = await api.get('/api/auth/me');
      setUser(response.data);
    } catch (error) {
      // If token is invalid or expired, clear it
      localStorage.removeItem('access_token');
      setUser(null);
    }
  };

  const login = async (credentials: LoginCredentials) => {
    try {
      const response = await api.post<TokenResponse>('/api/auth/login', credentials);
      const { access_token, user } = response.data;
      
      // Store the token in localStorage
      localStorage.setItem('access_token', access_token);
      
      setUser(user);
      router.push('/dashboard');
    } catch (error: any) {
      console.error('Login error:', error);
      throw error;
    }
  };

  const logout = async () => {
    try {
      await api.post('/api/auth/logout');
    } finally {
      // Clear the token from localStorage
      localStorage.removeItem('access_token');
      setUser(null);
      router.push('/login');
    }
  };

  useEffect(() => {
    const initAuth = async () => {
      try {
        await refreshUser();
      } catch (error) {
        console.error('Failed to refresh user:', error);
      }
      setIsLoading(false);
    };

    initAuth();
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated,
        isLoading,
        login,
        logout,
        refreshUser,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}; 