"use client";

import {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  type ReactNode,
} from "react";
import * as authClient from "@/lib/auth";

interface AuthContextType {
  user: { email: string } | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextType | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<{ email: string } | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = authClient.getAccessToken();
    if (token) {
      try {
        const payload = JSON.parse(atob(token.split(".")[1]));
        setUser({ email: payload.email || payload.sub });
      } catch {
        authClient.clearTokens();
      }
    }
    setLoading(false);
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    await authClient.login(email, password);
    setUser({ email });
  }, []);

  const register = useCallback(async (email: string, password: string) => {
    await authClient.register(email, password);
    setUser({ email });
  }, []);

  const logout = useCallback(() => {
    authClient.logout();
    setUser(null);
  }, []);

  return AuthContext.Provider({ value: { user, loading, login, register, logout }, children });
}

export function useAuth(): AuthContextType {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
