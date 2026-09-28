const AUTH_KEY = "mobilemap.auth";

export type BasicAuth = { username: string; password: string };

export function loadAuth(): BasicAuth | null {
  const raw = sessionStorage.getItem(AUTH_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as BasicAuth;
    if (parsed.username && parsed.password) return parsed;
  } catch {
    /* ignore */
  }
  return null;
}

export function saveAuth(auth: BasicAuth): void {
  sessionStorage.setItem(AUTH_KEY, JSON.stringify(auth));
}

export function clearAuth(): void {
  sessionStorage.removeItem(AUTH_KEY);
}

export function authHeader(auth: BasicAuth): string {
  return `Basic ${btoa(`${auth.username}:${auth.password}`)}`;
}
