import { describe, it, expect, vi, afterEach } from "vitest"
import { act, renderHook, waitFor } from "@testing-library/react"
import { useResource } from "@/lib/useResource"

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe("useResource", () => {
  it("fetches on mount and resolves the loading flag", async () => {
    const fetcher = vi.fn().mockResolvedValue("A")
    const { result } = renderHook(() => useResource(fetcher))

    expect(result.current.loading).toBe(true)
    expect(result.current.data).toBeNull()

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.data).toBe("A")
    expect(result.current.error).toBeNull()
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it("skips the mount fetch when immediate is false", async () => {
    const fetcher = vi.fn().mockResolvedValue("A")
    const { result } = renderHook(() => useResource(fetcher, { immediate: false }))

    expect(result.current.loading).toBe(false)
    expect(fetcher).not.toHaveBeenCalled()

    await act(async () => {
      await result.current.refresh()
    })
    expect(result.current.data).toBe("A")
  })

  it("discards a slower earlier response when a newer refresh wins the race", async () => {
    const slow = deferred<string>()
    const fast = deferred<string>()
    const fetcher = vi
      .fn()
      .mockReturnValueOnce(Promise.resolve("initial"))
      .mockReturnValueOnce(slow.promise) // first refresh, resolves last
      .mockReturnValueOnce(fast.promise) // second refresh, resolves first

    const { result } = renderHook(() => useResource(fetcher))
    await waitFor(() => expect(result.current.data).toBe("initial"))

    act(() => {
      void result.current.refresh({ background: true })
      void result.current.refresh({ background: true })
    })

    await act(async () => {
      fast.resolve("newer")
      await fast.promise
    })
    expect(result.current.data).toBe("newer")

    // The earlier, slower request now resolves and must be ignored.
    await act(async () => {
      slow.resolve("stale")
      await slow.promise
    })
    expect(result.current.data).toBe("newer")
  })

  it("keeps prior data and avoids the spinner on a failed background refresh", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce("A")
      .mockRejectedValueOnce(new Error("boom"))

    const { result } = renderHook(() => useResource(fetcher))
    await waitFor(() => expect(result.current.data).toBe("A"))

    await act(async () => {
      await result.current.refresh({ background: true })
    })

    expect(result.current.data).toBe("A")
    expect(result.current.error).toBe("boom")
    expect(result.current.loading).toBe(false)
  })

  it("clears loading when a background refresh supersedes an in-flight foreground load", async () => {
    // Models the real Dashboard/Discover race: the user clicks Refresh
    // (foreground, raises the spinner) and the 30s poll (background) fires while
    // that request is still in flight. The poll bumps the request id, so the
    // foreground resolves stale; the winning background response must still clear
    // the spinner the foreground raised, or the Refresh button stays disabled and
    // spinning forever.
    const fg = deferred<string>()
    const bg = deferred<string>()
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce("initial") // mount
      .mockReturnValueOnce(fg.promise) // foreground refresh (resolves last, stale)
      .mockReturnValueOnce(bg.promise) // background poll (wins)

    const { result } = renderHook(() => useResource(fetcher))
    await waitFor(() => expect(result.current.data).toBe("initial"))
    expect(result.current.loading).toBe(false)

    act(() => {
      void result.current.refresh() // foreground: takes the spinner
    })
    expect(result.current.loading).toBe(true)

    act(() => {
      void result.current.refresh({ background: true }) // poll supersedes it
    })

    await act(async () => {
      bg.resolve("polled")
      await bg.promise
    })
    expect(result.current.data).toBe("polled")
    expect(result.current.loading).toBe(false)

    // The stale foreground resolves and is ignored; loading stays cleared.
    await act(async () => {
      fg.resolve("stale")
      await fg.promise
    })
    expect(result.current.data).toBe("polled")
    expect(result.current.loading).toBe(false)
  })

  it("clears loading when a FAILING background refresh supersedes an in-flight foreground load", async () => {
    // The catch-tail twin of the case above: the user clicks Refresh
    // (foreground, raises the spinner), the 30s poll (background) fires while
    // that request is still in flight AND the poll fails. The poll bumped the
    // request id, so the foreground resolves stale and can never clear the
    // spinner it raised — only the winning background's catch tail can. If that
    // catch were gated on `!background` the Refresh button would stay disabled
    // and spinning forever even though an error is already on screen.
    const fg = deferred<string>()
    const bg = deferred<string>()
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce("initial") // mount
      .mockReturnValueOnce(fg.promise) // foreground refresh (resolves last, stale)
      .mockReturnValueOnce(bg.promise) // background poll (wins, then rejects)

    const { result } = renderHook(() => useResource(fetcher))
    await waitFor(() => expect(result.current.data).toBe("initial"))

    act(() => {
      void result.current.refresh() // foreground: takes the spinner
    })
    expect(result.current.loading).toBe(true)

    act(() => {
      void result.current.refresh({ background: true }) // poll supersedes it
    })

    await act(async () => {
      bg.reject(new Error("poll boom"))
      await bg.promise.catch(() => {})
    })
    expect(result.current.error).toBe("poll boom")
    expect(result.current.loading).toBe(false)

    // The stale foreground resolves and is ignored; loading stays cleared.
    await act(async () => {
      fg.resolve("stale")
      await fg.promise
    })
    expect(result.current.loading).toBe(false)
    expect(result.current.error).toBe("poll boom")
  })

  it("keeps the spinner up when a stale background poll resolves before the winning foreground load", async () => {
    // Mirror of the supersede case: the poll fires first (background, no spinner)
    // then the user clicks Refresh (foreground, later id, wins, raises spinner).
    // The stale background resolving first must NOT store its value nor touch the
    // foreground's loading flag — the request-id guard drops it before any state
    // write — and the foreground then clears the spinner on its own win.
    const bg = deferred<string>()
    const fg = deferred<string>()
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce("initial") // mount
      .mockReturnValueOnce(bg.promise) // background poll (starts first, stale)
      .mockReturnValueOnce(fg.promise) // foreground refresh (starts later, wins)

    const { result } = renderHook(() => useResource(fetcher))
    await waitFor(() => expect(result.current.data).toBe("initial"))

    act(() => {
      void result.current.refresh({ background: true }) // poll: no spinner
    })
    expect(result.current.loading).toBe(false)

    act(() => {
      void result.current.refresh() // foreground starts later: raises spinner, wins
    })
    expect(result.current.loading).toBe(true)

    await act(async () => {
      bg.resolve("stale-poll")
      await bg.promise
    })
    expect(result.current.data).toBe("initial")
    expect(result.current.loading).toBe(true)

    await act(async () => {
      fg.resolve("fresh")
      await fg.promise
    })
    expect(result.current.data).toBe("fresh")
    expect(result.current.loading).toBe(false)
  })

  it("formats thrown values through mapError", async () => {
    const fetcher = vi.fn().mockRejectedValue(new Error("raw message"))
    const { result } = renderHook(() =>
      useResource(fetcher, { mapError: () => "friendly message" }),
    )

    await waitFor(() => expect(result.current.error).toBe("friendly message"))
    expect(result.current.loading).toBe(false)
  })

  it("setData installs a value, clears loading, and discards an in-flight fetch", async () => {
    const inflight = deferred<string>()
    const fetcher = vi.fn().mockReturnValue(inflight.promise)

    const { result } = renderHook(() => useResource(fetcher))
    expect(result.current.loading).toBe(true)

    act(() => {
      result.current.setData("optimistic")
    })
    expect(result.current.data).toBe("optimistic")
    expect(result.current.loading).toBe(false)

    // The fetch that was in flight when setData ran must not clobber the value.
    await act(async () => {
      inflight.resolve("stale")
      await inflight.promise
    })
    expect(result.current.data).toBe("optimistic")
  })

  it("lets onData take over a response by returning true (keeps loading, skips store)", async () => {
    const fetcher = vi.fn().mockResolvedValue("A")
    const onData = vi.fn().mockReturnValue(true)

    const { result } = renderHook(() => useResource(fetcher, { onData }))
    await waitFor(() => expect(onData).toHaveBeenCalledWith("A"))

    expect(result.current.data).toBeNull()
    expect(result.current.loading).toBe(true)
  })

  it("stores the data when onData returns nothing", async () => {
    const fetcher = vi.fn().mockResolvedValue("A")
    const onData = vi.fn() // returns undefined

    const { result } = renderHook(() => useResource(fetcher, { onData }))
    await waitFor(() => expect(result.current.data).toBe("A"))
    expect(onData).toHaveBeenCalledWith("A")
    expect(result.current.loading).toBe(false)
  })

  it("ignores a response that resolves after unmount", async () => {
    const inflight = deferred<string>()
    const onData = vi.fn()
    const fetcher = vi.fn().mockReturnValue(inflight.promise)

    const { result, unmount } = renderHook(() => useResource(fetcher, { onData }))
    unmount()

    await act(async () => {
      inflight.resolve("late")
      await inflight.promise
    })

    expect(onData).not.toHaveBeenCalled()
    expect(result.current.data).toBeNull()
  })

  it("refetches when the fetcher identity changes", async () => {
    const fa = vi.fn().mockResolvedValue("A")
    const fb = vi.fn().mockResolvedValue("B")
    const { result, rerender } = renderHook(({ f }) => useResource(f), {
      initialProps: { f: fa },
    })

    await waitFor(() => expect(result.current.data).toBe("A"))

    rerender({ f: fb })
    await waitFor(() => expect(result.current.data).toBe("B"))

    expect(fa).toHaveBeenCalledTimes(1)
    expect(fb).toHaveBeenCalledTimes(1)
  })

  it("polls in the background on cadence and stops on unmount", async () => {
    vi.useFakeTimers()
    try {
      const fetcher = vi.fn().mockResolvedValue("A")
      const { unmount } = renderHook(() => useResource(fetcher, { pollMs: 1000 }))

      expect(fetcher).toHaveBeenCalledTimes(1) // mount load

      await act(async () => {
        await vi.advanceTimersByTimeAsync(3000)
      })
      expect(fetcher).toHaveBeenCalledTimes(4) // + 3 polls

      unmount()
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5000)
      })
      expect(fetcher).toHaveBeenCalledTimes(4) // no further polls
    } finally {
      vi.useRealTimers()
    }
  })
})
