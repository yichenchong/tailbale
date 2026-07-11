import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { act, renderHook } from "@testing-library/react"
import { useTransientMessage } from "@/lib/useTransientMessage"

/**
 * Behavior contract for the auto-clearing transient-message hook (action
 * toasts). Covers the timer discipline the hook exists to own: auto-clear on
 * cadence, re-show replacing the pending timer, `clear` soft-hiding without
 * cancelling the timer, and cancelling the pending timer on unmount (no leaked
 * timer / setState-after-unmount).
 */
describe("useTransientMessage", () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it("starts with no message", () => {
    const { result } = renderHook(() => useTransientMessage(3000))
    expect(result.current.message).toBeNull()
  })

  it("shows a message and auto-clears it exactly at the duration boundary", () => {
    const { result } = renderHook(() => useTransientMessage(3000))
    act(() => result.current.show("saved"))
    expect(result.current.message).toBe("saved")

    act(() => {
      vi.advanceTimersByTime(2999)
    })
    expect(result.current.message).toBe("saved") // one tick short — still up

    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(result.current.message).toBeNull() // cleared at 3000ms
  })

  it("re-showing replaces the pending timer so the newer message's clock governs", () => {
    const { result } = renderHook(() => useTransientMessage(3000))
    act(() => result.current.show("first"))
    act(() => {
      vi.advanceTimersByTime(2000) // 2s into "first"
    })
    act(() => result.current.show("second")) // cancels "first"'s timer, re-arms
    expect(result.current.message).toBe("second")

    // "first"'s original 3s deadline (at 2000+1000) would fire here, but it was
    // cancelled — "second" is untouched.
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(result.current.message).toBe("second")

    // "second"'s own fresh 3s deadline clears it.
    act(() => {
      vi.advanceTimersByTime(2000)
    })
    expect(result.current.message).toBeNull()
  })

  it("clear() soft-hides immediately and leaves the pending timer to fire harmlessly", () => {
    // clear only nulls the message; it deliberately does NOT cancel the timer so
    // a subsequently-shown message's timer stays the sole authority.
    const { result } = renderHook(() => useTransientMessage(3000))
    act(() => result.current.show("x"))
    act(() => result.current.clear())
    expect(result.current.message).toBeNull()

    act(() => {
      vi.advanceTimersByTime(3000) // stale timer fires; message already null
    })
    expect(result.current.message).toBeNull()
  })

  it("cancels the pending timer on unmount (no leaked timer)", () => {
    const clearSpy = vi.spyOn(window, "clearTimeout")
    const { result, unmount } = renderHook(() => useTransientMessage(3000))
    act(() => result.current.show("pending"))

    clearSpy.mockClear()
    unmount()
    // The unmount cleanup clears the still-pending timeout instead of leaking it.
    expect(clearSpy).toHaveBeenCalled()

    // Firing past the old deadline triggers no state update on the unmounted hook.
    act(() => {
      vi.advanceTimersByTime(3000)
    })
  })
})
