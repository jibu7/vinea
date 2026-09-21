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

/**
 * Download a file the **server** produced, rather than one the screen assembled.
 *
 * `exportToCsv` is the right tool for a table already on the page: the rows are there, and
 * round-tripping them through the network to get the same bytes back would be ceremony. It is
 * the wrong tool for the VAT annexes, which are the authority's own listings — every fiscalized
 * sale with its receipt block, every purchase with its supplier TIN — computed by
 * `app/tax/annexes.py` over data no screen holds. A client that rebuilt them would be a second
 * implementation of a filing, and the two would part company the first time either changed.
 *
 * So the bytes are the server's, and the filename is the server's too when it sends one:
 * `Content-Disposition` already carries `vat-sales-20260301-20260331.csv`, and a name invented
 * here would be a third place the period is spelled.
 */
export async function downloadFromApi(path: string, fallbackFilename: string): Promise<void> {
  const res = await fetch(`${API_BASE}${path}`, { credentials: "include" });
  if (res.status === 401) {
    const refreshed = await refreshSession();
    if (refreshed) return downloadFromApi(path, fallbackFilename);
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(
      res.status,
      body ?? { code: "unknown_error", message: res.statusText, field_errors: {} },
    );
  }
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const named = /filename="?([^";]+)"?/.exec(disposition);
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.setAttribute("href", url);
  link.setAttribute("download", named?.[1] ?? fallbackFilename);
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
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

