import { describe, it, expect } from "vitest"
import { renderHook } from "@testing-library/react"
import { useLatestRequest } from "@/lib/useLatestRequest"

describe("useLatestRequest", () => {
  it("marks only the newest token as current", () => {
    const { result } = renderHook(() => useLatestRequest())

    const first = result.current.next()
    const second = result.current.next()

    expect(result.current.isCurrent(first)).toBe(false)
    expect(result.current.isCurrent(second)).toBe(true)
  })

  it("invalidates an in-flight token without starting a replacement request", () => {
    const { result } = renderHook(() => useLatestRequest())

    const token = result.current.next()
    result.current.invalidate()

    expect(result.current.isCurrent(token)).toBe(false)
  })

  it("invalidates the latest token on unmount", () => {
    const { result, unmount } = renderHook(() => useLatestRequest())

    const token = result.current.next()
    const isCurrent = result.current.isCurrent
    unmount()

    expect(isCurrent(token)).toBe(false)
  })
})
