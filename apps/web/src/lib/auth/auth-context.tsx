"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { CredentialsRequest, User } from "@doculens/shared-types";

import {
  fetchCurrentUser,
  loginWithCredentials,
  logoutCurrentSession,
  registerAccount,
} from "@/lib/api/auth";
import { ApiError } from "@/lib/api/errors";
import { clearSessionTokens, hasSessionMaterial, onSessionCleared } from "@/lib/auth/session";

export type AuthStatus = "booting" | "authenticated" | "anonymous";

export interface AuthContextValue {
  status: AuthStatus;
  user: User | null;
  login: (credentials: CredentialsRequest) => Promise<void>;
  register: (credentials: CredentialsRequest) => Promise<void>;
  logout: () => Promise<void>;
  refreshProfile: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function isAbortError(error: unknown): boolean {
  return (
    (error instanceof DOMException && error.name === "AbortError") ||
    (error instanceof Error && error.name === "AbortError")
  );
}

export function AuthProvider({ children }: { children: ReactNode }) {
  // Always start as booting so server and client first paints match (no hydration mismatch).
  const [status, setStatus] = useState<AuthStatus>("booting");
  const [user, setUser] = useState<User | null>(null);

  useEffect(() => {
    return onSessionCleared(() => {
      setUser(null);
      setStatus("anonymous");
    });
  }, []);

  useEffect(() => {
    const controller = new AbortController();

    void (async () => {
      // Yield so the no-session path is not a synchronous setState inside the effect body.
      await Promise.resolve();
      if (controller.signal.aborted) {
        return;
      }

      if (!hasSessionMaterial()) {
        setStatus("anonymous");
        return;
      }

      try {
        const profile = await fetchCurrentUser(controller.signal);
        if (controller.signal.aborted) {
          return;
        }
        setUser(profile);
        setStatus("authenticated");
      } catch (error) {
        if (controller.signal.aborted || isAbortError(error)) {
          return;
        }
        if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
          clearSessionTokens();
        }
        // Network and other failures: keep any remaining tokens for a later retry, but treat
        // the UI as signed out until the user signs in again or reloads.
        setUser(null);
        setStatus("anonymous");
      }
    })();

    return () => {
      controller.abort();
    };
  }, []);

  const login = useCallback(async (credentials: CredentialsRequest) => {
    await loginWithCredentials(credentials);
    const profile = await fetchCurrentUser();
    setUser(profile);
    setStatus("authenticated");
  }, []);

  const register = useCallback(
    async (credentials: CredentialsRequest) => {
      await registerAccount(credentials);
      await login(credentials);
    },
    [login],
  );

  const logout = useCallback(async () => {
    try {
      await logoutCurrentSession();
    } finally {
      setUser(null);
      setStatus("anonymous");
    }
  }, []);

  const refreshProfile = useCallback(async () => {
    const profile = await fetchCurrentUser();
    setUser(profile);
    setStatus("authenticated");
  }, []);

  const value = useMemo(
    () => ({ status, user, login, register, logout, refreshProfile }),
    [status, user, login, register, logout, refreshProfile],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("useAuth must be used within AuthProvider.");
  }
  return value;
}
