const API_BASE = `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/api/v1`;

export interface ApiErrorBody {
  code: string;
  message: string;
  field_errors: Record<string, string[]>;
}

export class ApiError extends Error {
  code: string;
  fieldErrors: Record<string, string[]>;
  status: number;

  constructor(status: number, body: ApiErrorBody) {
    super(body.message);
    this.status = status;
    this.code = body.code;
    this.fieldErrors = body.field_errors;
  }
}

let refreshInFlight: Promise<boolean> | null = null;

async function refreshSession(): Promise<boolean> {
  refreshInFlight ??= fetch(`${API_BASE}/auth/refresh`, {
    method: "POST",
    credentials: "include",
  })
    .then((r) => r.ok)
    .finally(() => {
      refreshInFlight = null;
    });
  return refreshInFlight;
}

async function request<T>(path: string, init: RequestInit = {}, retried = false): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "include",
    headers: {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...init.headers,
    },
  });

  if (res.status === 401 && !retried && path !== "/auth/refresh" && path !== "/auth/login") {
    const refreshed = await refreshSession();
    if (refreshed) return request<T>(path, init, true);
  }

  if (res.status === 204) return undefined as T;

  const body = await res.json().catch(() => null);
  if (!res.ok) {
    throw new ApiError(res.status, body ?? { code: "unknown_error", message: res.statusText, field_errors: {} });
  }
  return body as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, data?: unknown, headers?: HeadersInit) =>
    request<T>(path, { method: "POST", body: data !== undefined ? JSON.stringify(data) : undefined, headers }),
  patch: <T>(path: string, data?: unknown, headers?: HeadersInit) =>
    request<T>(path, { method: "PATCH", body: data !== undefined ? JSON.stringify(data) : undefined, headers }),
  put: <T>(path: string, data?: unknown, headers?: HeadersInit) =>
    request<T>(path, { method: "PUT", body: data !== undefined ? JSON.stringify(data) : undefined, headers }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};

