// Dunne fetch-wrapper. Cookies zijn first-party (zelfde origin via nginx/Vite-proxy);
// elke wijzigende request stuurt de CSRF-header mee die de backend verplicht.

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

type Query = Record<string, string | number | boolean | null | undefined>;

function withQuery(path: string, query?: Query): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

async function request<T>(method: string, path: string, body?: unknown, query?: Query): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET") headers["X-VaultX-CSRF"] = "1";
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(withQuery(path, query), {
    method,
    headers,
    credentials: "same-origin",
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof data.detail === "string" ? data.detail : res.statusText;
    throw new ApiError(res.status, data.error ?? "error", detail);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string, query?: Query) => request<T>("GET", path, undefined, query),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body ?? {}),
  patch: <T>(path: string, body: unknown) => request<T>("PATCH", path, body),
  delete: (path: string) => request<void>("DELETE", path),
};

export function loginUrl(next = window.location.pathname + window.location.search): string {
  return `/auth/login?next=${encodeURIComponent(next)}`;
}

export async function logout(): Promise<void> {
  const res = await api.post<{ redirect_url: string }>("/auth/logout");
  window.location.assign(res.redirect_url);
}
