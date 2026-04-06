import { describe, it, expect } from "vitest"
import { cn } from "@/lib/utils"

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
