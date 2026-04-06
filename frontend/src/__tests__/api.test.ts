import { describe, it, expect, vi, beforeEach } from "vitest"
import { api } from "@/lib/api"

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

  it("throws on non-ok response", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 422,
      json: () => Promise.resolve({ detail: "Validation error" }),
    })

    await expect(api.get("/bad")).rejects.toThrow("Validation error")
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
})
