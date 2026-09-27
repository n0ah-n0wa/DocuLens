import type { CredentialsRequest, TokenPair, User } from "@doculens/shared-types";

import { apiRequest, storeTokenPair } from "@/lib/api/client";
import { clearSessionTokens, getRefreshToken } from "@/lib/auth/session";

export async function registerAccount(credentials: CredentialsRequest): Promise<User> {
  return apiRequest<User>("/api/v1/auth/register", {
    method: "POST",
    body: credentials,
    auth: false,
  });
}

export async function loginWithCredentials(credentials: CredentialsRequest): Promise<TokenPair> {
  const pair = await apiRequest<TokenPair>("/api/v1/auth/login", {
    method: "POST",
    body: credentials,
    auth: false,
  });
  storeTokenPair(pair);
  return pair;
}

export async function logoutCurrentSession(): Promise<void> {
  const refreshToken = getRefreshToken();
  try {
    if (refreshToken) {
      await apiRequest<null>("/api/v1/auth/logout", {
        method: "POST",
        body: { refresh_token: refreshToken },
        auth: false,
        skipRefresh: true,
      });
    }
  } finally {
    clearSessionTokens();
  }
}

export async function fetchCurrentUser(signal?: AbortSignal): Promise<User> {
  const options = signal === undefined ? {} : { signal };
  return apiRequest<User>("/api/v1/users/me", options);
}
