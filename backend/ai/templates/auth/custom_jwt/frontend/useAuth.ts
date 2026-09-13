/**
 * useAuth hook - Custom JWT authentication state management.
 * Provides login, register, logout, and current user state.
 */

import { useState, useEffect, useCallback } from "react";
import { getToken, setToken, removeToken, isAuthenticated } from "@/lib/auth";

interface User {
  id: string;
  email: string;
}

interface AuthState {
  user: User | null;
  loading: boolean;
  isAuthenticated: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

export function useAuth(): AuthState {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (isAuthenticated()) {
      const token = getToken();
      if (token) {
        try {
          const payload = JSON.parse(atob(token.split(".")[1]));
          setUser({ id: payload.sub, email: payload.email });
        } catch {
          removeToken();
        }
      }
    }
    setLoading(false);
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) throw new Error("Invalid credentials");
    const data = await res.json();
    setToken(data.token);
    setUser({ id: data.user.id, email: data.user.email });
  }, []);

  const register = useCallback(async (email: string, password: string) => {
    const res = await fetch("/api/auth/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) throw new Error("Registration failed");
    const data = await res.json();
    setToken(data.token);
    setUser({ id: data.user.id, email: data.user.email });
  }, []);

  const logout = useCallback(() => {
    removeToken();
    setUser(null);
  }, []);

  return {
    user,
    loading,
    isAuthenticated: !!user,
    login,
    register,
    logout,
  };
}
