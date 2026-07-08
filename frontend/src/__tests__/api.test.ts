import { describe, it, expect, vi, beforeEach } from "vitest"
import { api, UnauthorizedError } from "@/lib/api"

describe("api client", () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it("GET request calls fetch with correct URL", async () => {
    const mockResponse = { status: "ok" }
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockResponse),
    })

    const result = await api.get("/health")
    expect(fetch).toHaveBeenCalledWith("/api/health", expect.objectContaining({
      headers: expect.objectContaining({ "Content-Type": "application/json" }),
    }))
    expect(result).toEqual(mockResponse)
  })

  it("PUT request sends JSON body", async () => {
    const body = { base_domain: "test.com" }
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ general: { base_domain: "test.com" } }),
    })

    await api.put("/settings/general", body)
    expect(fetch).toHaveBeenCalledWith("/api/settings/general", expect.objectContaining({
      method: "PUT",
      body: JSON.stringify(body),
    }))
  })

  it("POST request works without body", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ success: true }),
    })

    await api.post("/settings/test/docker")
    expect(fetch).toHaveBeenCalledWith("/api/settings/test/docker", expect.objectContaining({
      method: "POST",
      body: undefined,
    }))
  })

  it("POST request preserves falsy JSON bodies", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ success: true }),
    })

    await api.post("/settings/value", 0)
    expect(fetch).toHaveBeenCalledWith("/api/settings/value", expect.objectContaining({
      method: "POST",
      body: "0",
    }))
  })

  it("keeps the default JSON Content-Type on a PUT whose options carry only method and body", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ success: true }),
    })

    await api.put("/settings/general", { base_domain: "example.com" })
    expect(fetch).toHaveBeenCalledWith("/api/settings/general", expect.objectContaining({
      headers: expect.objectContaining({ "Content-Type": "application/json" }),
    }))
  })

  it("throws on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 422,
      json: () => Promise.resolve({ detail: "Validation error" }),
    })

    await expect(api.get("/bad")).rejects.toThrow("Validation error")
  })

  it("formats FastAPI validation detail arrays", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 422,
      json: () => Promise.resolve({
        detail: [
          { loc: ["query", "limit"], msg: "Input should be greater than or equal to 1" },
          { loc: ["query", "offset"], msg: "Input should be greater than or equal to 0" },
        ],
      }),
    })

    await expect(api.get("/bad")).rejects.toThrow(
      "Input should be greater than or equal to 1; Input should be greater than or equal to 0"
    )
  })

  it("formats object error details", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: () => Promise.resolve({ detail: { message: "Could not read logs" } }),
    })

    await expect(api.get("/bad")).rejects.toThrow("Could not read logs")
  })

  it("joins an array of plain-string error details (FL2)", async () => {
    // A handler may return `detail` as a bare string array rather than the
    // {loc,msg} objects; each non-blank string is kept and joined, not dropped.
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: () => Promise.resolve({ detail: ["first problem", "second problem"] }),
    })

    await expect(api.get("/bad")).rejects.toThrow("first problem; second problem")
  })

  it("prefers the 'error' key when an object detail lacks msg/message (FL2)", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 400,
      json: () => Promise.resolve({ detail: { error: "boom from error key" } }),
    })

    await expect(api.get("/bad")).rejects.toThrow("boom from error key")
  })

  it("falls back to the generic message for a detail object with no known key (FL2)", async () => {
    // A well-formed body whose `detail` carries none of msg/message/error must
    // not surface "[object Object]" or an empty message — it falls through to
    // the status-coded generic message.
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 418,
      json: () => Promise.resolve({ detail: { code: "teapot" } }),
    })

    await expect(api.get("/bad")).rejects.toThrow("Request failed: 418")
  })

  it("falls back to the generic message for an empty detail array (FL2)", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 422,
      json: () => Promise.resolve({ detail: [] }),
    })

    await expect(api.get("/bad")).rejects.toThrow("Request failed: 422")
  })

  it("throws generic message when response has no detail", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      json: () => Promise.reject(new Error("not json")),
    })

    await expect(api.get("/bad")).rejects.toThrow("Request failed: 500")
  })

  it("DELETE request uses correct method", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({}),
    })

    await api.delete("/services/svc_123")
    expect(fetch).toHaveBeenCalledWith("/api/services/svc_123", expect.objectContaining({
      method: "DELETE",
    }))
  })

  it("redirects to /login and throws on a 401 for a non-/auth/ path", async () => {
    const original = window.location
    Object.defineProperty(window, "location", { configurable: true, value: { href: "" } })
    try {
      global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 401 })

      await expect(api.get("/settings")).rejects.toThrow("Session expired")
      expect(window.location.href).toBe("/login")
    } finally {
      Object.defineProperty(window, "location", { configurable: true, value: original })
    }
  })

  it("does not redirect a 401 on an /auth/ path", async () => {
    const original = window.location
    Object.defineProperty(window, "location", { configurable: true, value: { href: "" } })
    try {
      // An /auth/ 401 (e.g. bad login) falls through to the normal error path
      // so the page can surface the message instead of bouncing to /login.
      global.fetch = vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: () => Promise.resolve({ detail: "Invalid credentials" }),
      })

      await expect(api.post("/auth/login", { username: "x" })).rejects.toThrow("Invalid credentials")
      expect(window.location.href).toBe("")
    } finally {
      Object.defineProperty(window, "location", { configurable: true, value: original })
    }
  })

  it("getSafe throws UnauthorizedError on a 401 without redirecting", async () => {
    const original = window.location
    Object.defineProperty(window, "location", { configurable: true, value: { href: "" } })
    try {
      global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 401 })

      await expect(api.getSafe("/dashboard/summary")).rejects.toBeInstanceOf(UnauthorizedError)
      expect(window.location.href).toBe("")
    } finally {
      Object.defineProperty(window, "location", { configurable: true, value: original })
    }
  })

  it("getSafe resolves the parsed body on a 200", async () => {
    const payload = { services: { total: 1, healthy: 1, warning: 0, error: 0 } }
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(payload),
    })

    await expect(api.getSafe("/dashboard/summary")).resolves.toEqual(payload)
    expect(fetch).toHaveBeenCalledWith("/api/dashboard/summary", expect.objectContaining({
      credentials: "same-origin",
    }))
  })

  it("dashboard.summary fetches the summary endpoint and returns the payload", async () => {
    const payload = {
      services: { total: 2, healthy: 1, warning: 0, error: 1 },
      expiring_certs: [],
      recent_errors: [],
      recent_events: [],
    }
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve(payload),
    })

    const result = await api.dashboard.summary()
    expect(fetch).toHaveBeenCalledWith("/api/dashboard/summary", expect.any(Object))
    expect(result).toEqual(payload)
  })

  it("dashboard.summary redirects to /login on a 401 (unlike getSafe)", async () => {
    const original = window.location
    Object.defineProperty(window, "location", { configurable: true, value: { href: "" } })
    try {
      global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 401 })

      await expect(api.dashboard.summary()).rejects.toThrow("Session expired")
      expect(window.location.href).toBe("/login")
    } finally {
      Object.defineProperty(window, "location", { configurable: true, value: original })
    }
  })

  it("returns undefined for an empty (non-204) ok body instead of throwing", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: () => Promise.resolve(""),
    })

    await expect(api.get("/settings/empty")).resolves.toBeUndefined()
  })

  it("returns undefined for a whitespace-only ok body instead of throwing a SyntaxError", async () => {
    // A non-204 body of only whitespace is truthy, so a bare `text ? JSON.parse`
    // guard would feed " \n " to JSON.parse and throw an opaque "Unexpected EOF".
    // It must be treated like an empty body and resolve undefined.
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: () => Promise.resolve("  \n\t "),
    })

    await expect(api.get("/settings/blank")).resolves.toBeUndefined()
  })

  it("events.list builds the query string, omitting blank filters and keeping limit/offset", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, text: () => Promise.resolve("{}") })
    global.fetch = fetchMock

    await api.events.list({ search: "cert", level: "", kind: "cert_issued", limit: 50, offset: 100 })
    const url = fetchMock.mock.calls[0][0] as string
    expect(url.startsWith("/api/events?")).toBe(true)
    const qs = new URLSearchParams(url.slice(url.indexOf("?") + 1))
    expect(qs.get("search")).toBe("cert")
    expect(qs.has("level")).toBe(false) // blank filter omitted
    expect(qs.get("kind")).toBe("cert_issued")
    expect(qs.get("limit")).toBe("50")
    expect(qs.get("offset")).toBe("100")
  })

  it("events.list emits a bare /events when no filters are set", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, text: () => Promise.resolve("{}") })
    global.fetch = fetchMock

    await api.events.list({ limit: 50, offset: 0 })
    const url = fetchMock.mock.calls[0][0] as string
    const qs = new URLSearchParams(url.slice(url.indexOf("?") + 1))
    expect(qs.has("search")).toBe(false)
    expect(qs.has("level")).toBe(false)
    expect(qs.has("kind")).toBe(false)
    expect(qs.get("limit")).toBe("50")
    expect(qs.get("offset")).toBe("0")
  })

  it("jobs.list omits a blank kind but always sends limit/offset", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, text: () => Promise.resolve("{}") })
    global.fetch = fetchMock

    await api.jobs.list({ kind: "", limit: 25, offset: 0 })
    const url = fetchMock.mock.calls[0][0] as string
    const qs = new URLSearchParams(url.slice(url.indexOf("?") + 1))
    expect(qs.has("kind")).toBe(false)
    expect(qs.get("limit")).toBe("25")
    expect(qs.get("offset")).toBe("0")
  })

  it("jobs.retry and jobs.dismiss URL-encode the job id in the path segment", async () => {
    // Consistency with servicePath/profiles.detect: a runtime id interpolated
    // into a path segment must be encoded so a reserved char (e.g. a stray '#'
    // or space) can't split the path or open a query. Job ids are `job_<hex>`
    // today, but the encode guards the contract, not the current id shape.
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, text: () => Promise.resolve("{}") })
    global.fetch = fetchMock

    await api.jobs.retry("job a#1")
    expect(fetchMock.mock.calls[0][0]).toBe("/api/jobs/job%20a%231/retry")

    await api.jobs.dismiss("job a#1")
    expect(fetchMock.mock.calls[1][0]).toBe("/api/jobs/job%20a%231")
  })

  it("discovery.containers stringifies runningOnly, forces hide_managed, and omits a blank search", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, text: () => Promise.resolve("{}") })
    global.fetch = fetchMock

    await api.discovery.containers({ runningOnly: false })
    const url = fetchMock.mock.calls[0][0] as string
    const qs = new URLSearchParams(url.slice(url.indexOf("?") + 1))
    expect(qs.get("running_only")).toBe("false")
    expect(qs.get("hide_managed")).toBe("true")
    expect(qs.has("search")).toBe(false)
  })

  it("profiles.detect URL-encodes the image reference (tag colon + slash)", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, text: () => Promise.resolve("{}") })
    global.fetch = fetchMock

    await api.profiles.detect("ghcr.io/owner/app:1.2")
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/profiles/detect?image=ghcr.io%2Fowner%2Fapp%3A1.2",
      expect.any(Object),
    )
  })

  it("services.remove appends cleanup_dns only when requested", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 204, json: () => Promise.resolve(), text: () => Promise.resolve("") })
    global.fetch = fetchMock

    await api.services.remove("svc 1")
    expect(fetchMock.mock.calls[0][0]).toBe("/api/services/svc%201")
    await api.services.remove("svc 1", { cleanupDns: true })
    expect(fetchMock.mock.calls[1][0]).toBe("/api/services/svc%201?cleanup_dns=true")
  })

  it("services.renewCert appends force=true only when requested, encoding the id (FL1)", async () => {
    // Parallels services.remove's cleanup_dns branch: the force flag is a real
    // query-building fork, and the interpolated id segment must be encoded.
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, text: () => Promise.resolve("{}") })
    global.fetch = fetchMock

    await api.services.renewCert("svc 1")
    expect(fetchMock.mock.calls[0][0]).toBe("/api/services/svc%201/renew-cert")
    await api.services.renewCert("svc 1", { force: true })
    expect(fetchMock.mock.calls[1][0]).toBe("/api/services/svc%201/renew-cert?force=true")
  })

  it("returns undefined on a 204 No Content without reading the body", async () => {
    // DELETE endpoints reply 204; the early return must short-circuit before any
    // body read so an empty response never throws (json()/text() never called).
    const json = vi.fn(() => Promise.reject(new Error("should not parse")))
    const text = vi.fn(() => Promise.reject(new Error("should not read")))
    global.fetch = vi.fn().mockResolvedValue({ ok: true, status: 204, json, text })

    await expect(api.delete("/services/svc_123")).resolves.toBeUndefined()
    expect(json).not.toHaveBeenCalled()
    expect(text).not.toHaveBeenCalled()
  })
})
