import { useCallback, useEffect, useRef, useState } from "react"

export interface UseResourceOptions<T> {
  /**
   * Poll interval in ms. When set, a *background* refresh fires on this cadence
   * (no spinner, prior data stays visible). Cleared on unmount.
   */
  pollMs?: number
  /** Run the initial fetch on mount. Default `true`. */
  immediate?: boolean
  /**
   * Post-process a winning response (one that passed the staleness guard and is
   * still mounted). Runs for both foreground and background loads.
   *
   * Return `true` to take over: the hook will NOT store the data nor clear
   * `loading`, leaving the caller to drive what happens next (e.g. clamp the
   * pagination offset and let the resulting re-fetch render). Returning nothing
   * (or `false`) lets the hook store the data and clear `loading` as usual.
   */
  onData?: (data: T) => boolean | void
  /** Map a thrown value to the `error` string. Default: `Error.message` else `String`. */
  mapError?: (err: unknown) => string
}

export interface UseResourceResult<T> {
  data: T | null
  loading: boolean
  error: string | null
  /**
   * Re-run the fetcher. `background: true` skips the loading flag (and so the
   * full-page spinner) and keeps the current data in place while it runs.
   */
  refresh: (opts?: { background?: boolean }) => Promise<void>
  /**
   * Optimistically install an authoritative value (e.g. the body returned by a
   * mutating write). Bumps the request-id guard so any fetch still in flight is
   * discarded instead of clobbering this value, clears `loading`, and clears any
   * prior `error` — a successful write is a winning response, so it resolves the
   * resource to a clean state exactly as `run`'s success path does. Without this
   * a stale error banner from an earlier failure would linger over the freshly
   * installed value (e.g. ServiceDetail's inline error after a failed-then-retried
   * enable/disable toggle).
   */
  setData: (value: T) => void
  /** Set the error string directly (e.g. for action/validation errors). */
  setError: (value: string | null) => void
}

const defaultMapError = (err: unknown): string =>
  err instanceof Error ? err.message : String(err)

/**
 * Shared fetch / loading / error / race-guard / poll machine.
 *
 * Replaces the per-page copies that each re-derived a monotonically-increasing
 * request id to discard stale responses (see the original
 * `Services.tsx` / `ServiceDetail.tsx` race-guard comments). Every fetch
 * captures the id it started with; only the response whose id still equals
 * `requestId.current` may write state. That single invariant covers all three
 * stale cases: a slower earlier request finishing last, a response landing
 * after unmount, and a response superseded by an optimistic `setData`.
 *
 * Callers memoize `fetcher` over its inputs (filters, offset, route id); a new
 * fetcher identity re-runs the load, mirroring the old `useEffect(load, [load])`
 * pattern.
 */
export function useResource<T>(
  fetcher: () => Promise<T>,
  opts: UseResourceOptions<T> = {},
): UseResourceResult<T> {
  const { pollMs, immediate = true } = opts

  const [data, setDataState] = useState<T | null>(null)
  const [loading, setLoading] = useState(immediate)
  const [error, setError] = useState<string | null>(null)

  const requestId = useRef(0)

  // Latest option callbacks held in refs so `run` stays referentially stable
  // (it depends only on `fetcher`) without ever reading a stale closure.
  const onDataRef = useRef(opts.onData)
  onDataRef.current = opts.onData
  const mapErrorRef = useRef(opts.mapError)
  mapErrorRef.current = opts.mapError

  const run = useCallback(
    async ({ background = false }: { background?: boolean } = {}): Promise<void> => {
      const id = ++requestId.current
      // A foreground load owns the spinner and clears the prior error up front;
      // a background refresh leaves both alone so the current view stays put.
      if (!background) {
        setLoading(true)
        setError(null)
      }
      try {
        const result = await fetcher()
        if (id !== requestId.current) return
        if (onDataRef.current?.(result) === true) return
        setDataState(result)
        setError(null)
        // Clear the spinner whenever the *winning* (current-id) response lands —
        // even a background one. If a foreground load was in flight it has now
        // been superseded by this poll, so nothing else would ever reset the
        // `loading` it raised (leaving the Refresh button stuck disabled). A pure
        // background poll with no pending foreground sees `loading` already false,
        // so this is a harmless no-op there.
        setLoading(false)
      } catch (err) {
        if (id !== requestId.current) return
        setError((mapErrorRef.current ?? defaultMapError)(err))
        setLoading(false)
      }
    },
    [fetcher],
  )

  const setData = useCallback((value: T) => {
    requestId.current += 1
    setDataState(value)
    setError(null)
    setLoading(false)
  }, [])

  // Initial load + reload whenever the fetcher identity changes. The cleanup
  // bumps the guard so a response still in flight at unmount / fetcher-change
  // can never write state.
  useEffect(() => {
    if (immediate) void run()
    return () => {
      requestId.current += 1
    }
  }, [run, immediate])

  // Optional background polling, torn down on unmount / interval change.
  useEffect(() => {
    if (!pollMs) return
    const timer = setInterval(() => {
      void run({ background: true })
    }, pollMs)
    return () => clearInterval(timer)
  }, [run, pollMs])

  return { data, loading, error, refresh: run, setData, setError }
}
