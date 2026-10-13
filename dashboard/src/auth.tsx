import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';
import { api, getCachedUser, getToken, onUnauthorized, setToken, type User } from './api';

interface AuthCtx {
  user: User | null;
  isAdmin: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

const Ctx = createContext<AuthCtx>({ user: null, isAdmin: false, login: async () => undefined, logout: () => undefined });

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(() => (getToken() ? getCachedUser() : null));

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
  }, []);

  useEffect(() => onUnauthorized(() => setUser(null)), []);

  useEffect(() => {
    if (!getToken()) return;
    api
      .me()
      .then((u) => {
        setUser(u);
        setToken(getToken(), u);
      })
      .catch(() => undefined); // 401 handled by onUnauthorized; network errors keep the cached user
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const r = await api.login(username, password);
    if (r.user.role !== 'ADMIN' && r.user.role !== 'SUPERVISOR') {
      throw new Error('The dashboard is for supervisors and admins. Workers and guards use the phone app.');
    }
    setToken(r.token, r.user);
    setUser(r.user);
  }, []);

  return <Ctx.Provider value={{ user, isAdmin: user?.role === 'ADMIN', login, logout }}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);
