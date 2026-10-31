import { createContext, useContext, useState, useEffect, ReactNode, useCallback } from "react";
import { authApi, tokenManager, User } from "../api/client";

interface AuthContextType {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<User>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const refreshUser = useCallback(async () => {
    try {
      // 1. Try fetching current user directly
      const data = await authApi.me();
      setUser(data);
    } catch {
      // 2. Try rotating refresh token if access token missing or expired
      try {
        await authApi.refresh();
        const data = await authApi.me();
        setUser(data);
      } catch {
        setUser(null);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Setup expired token handler
    tokenManager.setOnAuthExpired(() => {
      setUser(null);
    });

    refreshUser();

    return () => {
      tokenManager.setOnAuthExpired(null);
    };
  }, [refreshUser]);

  const login = async (email: string, password: string): Promise<User> => {
    await authApi.login(email, password);
    const currentUser = await authApi.me();
    setUser(currentUser);
    return currentUser;
  };

  const logout = async () => {
    try {
      await authApi.logout();
    } finally {
      setUser(null);
    }
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, refreshUser }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
