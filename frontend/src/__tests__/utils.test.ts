import { describe, it, expect, vi, afterEach } from "vitest"
import { cn, errorMessage, getJsonSafe } from "@/lib/utils"

describe("cn utility", () => {
  it("merges class names", () => {
    expect(cn("px-2", "py-1")).toBe("px-2 py-1")
  })

  it("handles conditional classes", () => {
    const isHidden = false
    expect(cn("base", isHidden && "hidden", "extra")).toBe("base extra")
  })

  it("merges conflicting tailwind classes", () => {
    // tailwind-merge should keep only the last conflicting utility
    expect(cn("px-2", "px-4")).toBe("px-4")
  })

  it("handles undefined and null", () => {
    expect(cn("base", undefined, null, "end")).toBe("base end")
  })

  it("handles empty input", () => {
    expect(cn()).toBe("")
  })
})

describe("errorMessage utility", () => {
  it("preserves Error.message for single-argument callers", () => {
    expect(errorMessage(new Error("Boom"))).toBe("Boom")
  })

  it("preserves non-Error stringification without a fallback", () => {
    expect(errorMessage(404)).toBe("404")
  })

  it("uses a fallback for non-Error thrown values when supplied", () => {
    expect(errorMessage({ detail: "hidden" }, "Fallback message")).toBe("Fallback message")
  })

  it("still prefers Error.message over a fallback", () => {
    expect(errorMessage(new Error("Specific"), "Generic")).toBe("Specific")
  })

  it("uses a fallback for Error values with blank messages", () => {
    expect(errorMessage(new Error(""), "Fallback message")).toBe("Fallback message")
  })
})

describe("getJsonSafe utility", () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("resolves the parsed JSON body on a 2xx response", async () => {
    const payload = { general: { timezone: "Asia/Tokyo" } }
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(payload),
    })

    await expect(getJsonSafe("/api/settings")).resolves.toEqual(payload)
    expect(fetch).toHaveBeenCalledWith("/api/settings", { credentials: "same-origin" })
  })

  it("resolves null on a non-ok response instead of throwing", async () => {
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500, json: () => Promise.resolve({}) })
    await expect(getJsonSafe("/api/settings")).resolves.toBeNull()
  })

  it("resolves null when the fetch rejects (network failure)", async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error("network down"))
    await expect(getJsonSafe("/api/settings")).resolves.toBeNull()
  })

  it("resolves null when the body is not valid JSON", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.reject(new SyntaxError("Unexpected end of JSON input")),
    })
    await expect(getJsonSafe("/api/settings")).resolves.toBeNull()
  })
})
