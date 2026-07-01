import { useCallback, useState } from "react"

export interface UsePaginationOptions {
  /** Page size. Mirrors the backend's `limit` query param. Default `50`. */
  limit?: number
}

export interface UsePaginationResult {
  /** Zero-based offset of the first item on the current page (the fetcher dep). */
  offset: number
  /** Page size. */
  limit: number
  /** Last total reported by the server (kept in sync via {@link setTotal}). */
  total: number
  /** 1-based index of the current page. */
  page: number
  /** Number of pages, always `>= 1`. */
  pageCount: number
  /** Jump straight to an offset (e.g. reset to `0` on a filter/search change). */
  setOffset: (offset: number) => void
  /** Feed the freshly-fetched `total` so `page`/`pageCount`/`goToPage` stay correct. */
  setTotal: (total: number) => void
  /** Step back one page (no-op past the start). */
  prev: () => void
  /** Step forward one page. */
  next: () => void
  /** Jump to page `n`, CLAMPED to `[1, pageCount]`; non-numeric (NaN) `n` is ignored; +/-Infinity is clamped to `[1, pageCount]`. */
  goToPage: (n: number) => void
  /**
   * Detects the "current page fell off the end" case and returns the offset to
   * clamp back to, or `null` when no clamp is needed.
   *
   * Generalized from OrphanDns's original shrink-clamp: when a record is removed
   * from a later page and `total` shrinks, the current offset can point past the
   * last record, so the reload returns an *empty* page over still-reachable rows.
   * Wire it into `useResource`'s `onData` and, when it returns an offset, call
   * `setOffset(it)` and return `true` so the fetcher skips storing the empty page
   * (keeping the spinner up) and the offset change retriggers the load — instead
   * of flashing a misleading empty state over records that are still there.
   *
   * Takes the FRESH `total`/`currentPageLen` from the just-arrived response
   * because at `onData` time the hook's own `total` state is still the stale,
   * pre-shrink value.
   */
  clampToContent: (total: number, currentPageLen: number) => number | null
}

/**
 * Shared offset-pagination state machine. Replaces the byte-for-byte identical
 * hand-rolled pagination that OrphanDns and Events each carried (limit=50,
 * Prev/Next, "{offset+1}-{min} of {total}"). Owns `offset` so it composes with
 * the `useResource` fetcher pattern: callers list `offset` (and `limit`) as
 * fetcher deps, and feed the response `total` back through `setTotal`.
 */
export function usePagination(opts: UsePaginationOptions = {}): UsePaginationResult {
  const limit = opts.limit ?? 50
  const [offset, setOffset] = useState(0)
  const [total, setTotal] = useState(0)

  const pageCount = Math.max(1, Math.ceil(total / limit))
  const page = Math.floor(offset / limit) + 1

  const goToPage = useCallback(
    (n: number) => {
      const target = Math.min(pageCount, Math.max(1, Math.floor(n)))
      if (Number.isFinite(target)) setOffset((target - 1) * limit)
    },
    [pageCount, limit],
  )

  const prev = useCallback(() => {
    setOffset((o) => Math.max(0, o - limit))
  }, [limit])

  const next = useCallback(() => {
    // Clamp at the last page so a programmatic call (or a double-click landing
    // before the disabled Next button re-renders) can't push `offset` past the
    // final record — which would render an out-of-range "page N of M<N" and an
    // empty page over still-reachable rows. Symmetric with `prev`'s floor at 0.
    setOffset((o) => {
      const maxOffset = total > 0 ? Math.floor((total - 1) / limit) * limit : 0
      return Math.min(o + limit, maxOffset)
    })
  }, [limit, total])

  const clampToContent = useCallback(
    (freshTotal: number, currentPageLen: number): number | null => {
      if (currentPageLen === 0 && freshTotal > 0 && offset > 0) {
        const lastPageOffset = Math.max(0, Math.floor((freshTotal - 1) / limit) * limit)
        if (lastPageOffset !== offset) return lastPageOffset
      }
      return null
    },
    [offset, limit],
  )

  return { offset, limit, total, page, pageCount, setOffset, setTotal, prev, next, goToPage, clampToContent }
}
