/**
 * Provisional token storage (OQ-19 / ADR-021).
 *
 * Access tokens stay in memory. Refresh tokens live in sessionStorage so a
 * reload in the same tab can resume the session until httpOnly cookie transport
 * is decided with hosting.
 *
 * Never put tokens in React state, URLs, or logs. XSS can still read
 * sessionStorage; that risk closes with httpOnly cookie refresh.
 */

const REFRESH_TOKEN_KEY = "doculens.refresh_token";

let accessToken: string | null = null;

type SessionClearedListener = () => void;
const sessionClearedListeners = new Set<SessionClearedListener>();

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function getRefreshToken(): string | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    return window.sessionStorage.getItem(REFRESH_TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setRefreshToken(token: string | null): void {
  if (typeof window === "undefined") {
    return;
  }
  try {
    if (token === null) {
      window.sessionStorage.removeItem(REFRESH_TOKEN_KEY);
    } else {
      window.sessionStorage.setItem(REFRESH_TOKEN_KEY, token);
    }
  } catch {
    // Private mode or blocked storage: the session lasts only for this page load.
  }
}

export function clearSessionTokens(): void {
  const hadSession = accessToken !== null || getRefreshToken() !== null;
  setAccessToken(null);
  setRefreshToken(null);
  if (hadSession) {
    for (const listener of sessionClearedListeners) {
      listener();
    }
  }
}

/** Subscribe to involuntary session clears (failed refresh, unrecovered 401). */
export function onSessionCleared(listener: SessionClearedListener): () => void {
  sessionClearedListeners.add(listener);
  return () => {
    sessionClearedListeners.delete(listener);
  };
}

export function hasSessionMaterial(): boolean {
  return getAccessToken() !== null || getRefreshToken() !== null;
}
