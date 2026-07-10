const API_BASE = "/api"

type ApiErrorDetail = unknown

const ERROR_DETAIL_KEYS = ["message", "msg", "error"] as const

function formatObjectErrorDetail(detail: Record<string, unknown>): string | null {
  for (const key of ERROR_DETAIL_KEYS) {
    const value = detail[key]
    if (typeof value === "string" && value) return value
  }
  return null
}

/**
 * Thrown by non-redirecting requests (e.g. `api.getSafe`) when the server
 * responds 401. Callers that must NOT bounce to /login (background pollers)
 * catch this typed marker to handle the auth failure on their own terms.
 */
export class UnauthorizedError extends Error {
  constructor(message = "Unauthorized") {
    super(message)
    this.name = "UnauthorizedError"
  }
}

interface RequestConfig {
  // When false, a 401 throws `UnauthorizedError` instead of redirecting to
  // /login. Defaults to true (redirect), matching `api.get`.
  redirectOn401?: boolean
}

function formatErrorDetail(detail: ApiErrorDetail): string | null {
  if (typeof detail === "string") return detail
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (typeof item === "string") return item
        if (item && typeof item === "object") {
          return formatObjectErrorDetail(item as Record<string, unknown>)
        }
        return null
      })
      .filter((msg): msg is string => Boolean(msg))
    return messages.length > 0 ? messages.join("; ") : null
  }
  if (detail && typeof detail === "object") {
    return formatObjectErrorDetail(detail as Record<string, unknown>)
  }
  return null
}

async function request<T>(path: string, options?: RequestInit, config?: RequestConfig): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    credentials: "same-origin",
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers },
  })
  if (res.status === 401 && !path.startsWith("/auth/")) {
    if (config?.redirectOn401 === false) {
      throw new UnauthorizedError()
    }
    window.location.href = "/login"
    throw new Error("Session expired")
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(formatErrorDetail(body?.detail) || `Request failed: ${res.status}`)
  }
  if (res.status === 204) return undefined as T
  // An empty (or whitespace-only) but non-204 body would make res.json() throw
  // an opaque SyntaxError, so read the raw text and JSON.parse only when it
  // holds non-whitespace content, returning undefined otherwise. Real Response
  // objects always expose text(); fall back to json() only for partial Response
  // stubs that omit it.
  if (typeof res.text === "function") {
    const text = await res.text()
    return (text.trim() ? JSON.parse(text) : undefined) as T
  }
  return res.json()
}

export function get<T>(path: string) {
  return request<T>(path)
}

// Non-redirecting GET: on 401 it throws `UnauthorizedError` rather than
// sending the browser to /login — for background pollers that must never
// trigger a navigation.
export function getSafe<T>(path: string) {
  return request<T>(path, undefined, { redirectOn401: false })
}

export function put<T>(path: string, body: unknown) {
  return request<T>(path, { method: "PUT", body: JSON.stringify(body) })
}

export function post<T>(path: string, body?: unknown) {
  return request<T>(path, {
    method: "POST",
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
}

export function del<T>(path: string) {
  return request<T>(path, { method: "DELETE" })
}
