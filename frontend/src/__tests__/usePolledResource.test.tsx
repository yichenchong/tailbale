import { describe, it, expect, vi, afterEach } from "vitest"
import { act, renderHook, waitFor } from "@testing-library/react"
import { usePolledResource } from "@/lib/usePolledResource"

afterEach(() => {
  vi.restoreAllMocks()
})

/**
 * Behavior contract for `usePolledResource` — `useResource` plus a
 * "last good fetch" timestamp. The interesting logic is `markFresh`: it stamps
 * `lastSuccessAt` on every WINNING (stored) response but must defer to a
 * caller-supplied `onData` that CLAIMS the response (returns `true`, e.g. a
 * pagination clamp that skips storing an empty page) by NOT stamping — that was
 * not a successful store.
 */
describe("usePolledResource", () => {
  it("starts with a null lastSuccessAt and stamps it on the first winning response", async () => {
    const fetcher = vi.fn().mockResolvedValue("A")
    const { result } = renderHook(() => usePolledResource(fetcher))

    expect(result.current.lastSuccessAt).toBeNull()

    await waitFor(() => expect(result.current.data).toBe("A"))
    expect(result.current.lastSuccessAt).toBeInstanceOf(Date)
  })

  it("does NOT stamp lastSuccessAt when the caller's onData claims the response", async () => {
    // onData returning `true` means "I took over — do not store" (the empty-page
    // clamp case). markFresh must treat that as a non-success and leave the
    // timestamp null, so a StaleDataBanner keeps reporting the last REAL refresh.
    const fetcher = vi.fn().mockResolvedValue("A")
    const onData = vi.fn().mockReturnValue(true)
    const { result } = renderHook(() => usePolledResource(fetcher, { onData }))

    await waitFor(() => expect(onData).toHaveBeenCalledWith("A"))
    // Give any (incorrect) state update a chance to flush.
    await act(async () => {
      await Promise.resolve()
    })
    expect(result.current.lastSuccessAt).toBeNull()
    expect(result.current.data).toBeNull() // claimed -> not stored
  })

  it("stamps lastSuccessAt and stores when the caller's onData returns void/false", async () => {
    const fetcher = vi.fn().mockResolvedValue("A")
    const onData = vi.fn().mockReturnValue(undefined)
    const { result } = renderHook(() => usePolledResource(fetcher, { onData }))

    await waitFor(() => expect(result.current.data).toBe("A"))
    expect(onData).toHaveBeenCalledWith("A")
    expect(result.current.lastSuccessAt).toBeInstanceOf(Date)
  })

  it("forwards intervalMs as the background poll cadence and re-stamps on each poll", async () => {
    vi.useFakeTimers()
    try {
      const fetcher = vi.fn().mockResolvedValue("A")
      const { result } = renderHook(() => usePolledResource(fetcher, { intervalMs: 1000 }))

      expect(fetcher).toHaveBeenCalledTimes(1) // mount load
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0)
      })
      const first = result.current.lastSuccessAt
      expect(first).toBeInstanceOf(Date)

      // Advance the clock past two poll ticks; each winning poll re-stamps.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(2000)
      })
      expect(fetcher).toHaveBeenCalledTimes(3) // + 2 background polls
      expect(result.current.lastSuccessAt).toBeInstanceOf(Date)
      // A poll that lands later stamps a timestamp >= the first one.
      expect(result.current.lastSuccessAt!.getTime()).toBeGreaterThanOrEqual(first!.getTime())
    } finally {
      vi.useRealTimers()
    }
  })
})
