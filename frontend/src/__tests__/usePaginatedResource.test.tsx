import { describe, it, expect, vi, afterEach } from "vitest"
import { act, renderHook, waitFor } from "@testing-library/react"
import { usePaginatedResource } from "@/lib/usePaginatedResource"

afterEach(() => {
  vi.restoreAllMocks()
})

interface Page {
  items: string[]
  total: number
}

/**
 * Behavior contract for `usePaginatedResource` — wires `usePagination` into a
 * `useResource` fetcher. The intricate part is the `onData` bridge: it feeds the
 * response `total` back to pagination and, when the current offset has fallen
 * off the end (a shrink leaves the current page empty), CLAMPS to the last page
 * and CLAIMS the response (returns `true`) so `useResource` skips storing the
 * empty page and the offset change retriggers the load — instead of flashing an
 * empty state over records that are still reachable.
 */
describe("usePaginatedResource", () => {
  it("loads the first page and exposes items, total, and derived page metadata", async () => {
    const load = vi.fn().mockResolvedValue({ items: ["a", "b"], total: 120 } satisfies Page)
    const { result } = renderHook(() =>
      usePaginatedResource<Page, string>({
        load,
        getItems: (d) => d.items,
        limit: 50,
      }),
    )

    await waitFor(() => expect(result.current.items).toEqual(["a", "b"]))
    expect(load).toHaveBeenCalledWith({ limit: 50, offset: 0 })
    expect(result.current.total).toBe(120)
    expect(result.current.page).toBe(1)
    expect(result.current.pageCount).toBe(3) // ceil(120 / 50)
  })

  it("advances to the next page via next() and refetches at the new offset", async () => {
    const load = vi
      .fn()
      .mockResolvedValueOnce({ items: ["a", "b"], total: 120 } satisfies Page)
      .mockResolvedValueOnce({ items: ["c", "d"], total: 120 } satisfies Page)
    const { result } = renderHook(() =>
      usePaginatedResource<Page, string>({
        load,
        getItems: (d) => d.items,
        limit: 50,
      }),
    )

    await waitFor(() => expect(result.current.items).toEqual(["a", "b"]))

    act(() => result.current.next())

    await waitFor(() => expect(result.current.items).toEqual(["c", "d"]))
    expect(load).toHaveBeenLastCalledWith({ limit: 50, offset: 50 })
    expect(result.current.page).toBe(2)
  })

  it("clamps a page that fell off the end after a shrink and reloads the last page (no empty flash)", async () => {
    // Start on page 2 (offset 50). The record set shrinks to 3 (one page), so
    // the offset-50 reload returns an empty page. The clamp must detect this,
    // jump to offset 0, and reload — surfacing the real last page rather than
    // an empty list.
    const load = vi
      .fn()
      // initial page-1 load
      .mockResolvedValueOnce({ items: ["a", "b"], total: 120 } satisfies Page)
      // page-2 load after next()
      .mockResolvedValueOnce({ items: ["c", "d"], total: 120 } satisfies Page)
      // page-2 reload after the shrink: EMPTY page over a now-tiny total
      .mockResolvedValueOnce({ items: [], total: 3 } satisfies Page)
      // clamped reload at offset 0
      .mockResolvedValueOnce({ items: ["x", "y", "z"], total: 3 } satisfies Page)

    const { result } = renderHook(() =>
      usePaginatedResource<Page, string>({
        load,
        getItems: (d) => d.items,
        limit: 50,
      }),
    )

    await waitFor(() => expect(result.current.items).toEqual(["a", "b"]))

    act(() => result.current.next())
    await waitFor(() => expect(result.current.items).toEqual(["c", "d"]))
    expect(result.current.offset).toBe(50)

    // Force a reload; the mocked response is now the empty, shrunk page.
    await act(async () => {
      await result.current.refresh()
    })

    // The clamp must have kicked in: offset back to 0 and the real last page shown.
    await waitFor(() => expect(result.current.items).toEqual(["x", "y", "z"]))
    expect(result.current.offset).toBe(0)
    expect(result.current.total).toBe(3)
    // The empty page was never surfaced as the stored items.
    expect(result.current.items).not.toEqual([])
    expect(load).toHaveBeenLastCalledWith({ limit: 50, offset: 0 })
  })

  it("maps a thrown load through mapError", async () => {
    const load = vi.fn().mockRejectedValue(new Error("boom"))
    const { result } = renderHook(() =>
      usePaginatedResource<Page, string>({
        load,
        getItems: (d) => d.items,
        mapError: (e) => `mapped:${(e as Error).message}`,
      }),
    )

    await waitFor(() => expect(result.current.error).toBe("mapped:boom"))
    expect(result.current.items).toEqual([]) // no data -> empty items
  })
})
