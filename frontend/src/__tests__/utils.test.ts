import { describe, it, expect } from "vitest"
import { cn, errorMessage } from "@/lib/utils"

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
