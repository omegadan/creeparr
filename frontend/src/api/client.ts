export class ApiError extends Error {
  code: string;
  status: number;
  detail?: string | null;
  constructor(status: number, code: string, message: string, detail?: string | null) {
    super(message);
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

const BASE = "/api/v1";

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json", ...(init.headers as Record<string, string>) };
  if (init.body && !(init.body instanceof FormData)) headers["Content-Type"] = "application/json";
  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/auth/")) {
      window.dispatchEvent(new CustomEvent("patrearr-unauthorized"));
    }
    const err = (data as { error?: { code?: string; message?: string; detail?: string } } | null)?.error;
    throw new ApiError(res.status, err?.code ?? "http_error", err?.message ?? `HTTP ${res.status}`, err?.detail);
  }
  return data as T;
}

export const get = <T,>(path: string) => api<T>(path);
export const post = <T,>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const put = <T,>(path: string, body?: unknown) =>
  api<T>(path, { method: "PUT", body: body === undefined ? undefined : JSON.stringify(body) });
export const patch = <T,>(path: string, body?: unknown) =>
  api<T>(path, { method: "PATCH", body: body === undefined ? undefined : JSON.stringify(body) });
export const del = <T,>(path: string) => api<T>(path, { method: "DELETE" });

export function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    sp.set(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}
