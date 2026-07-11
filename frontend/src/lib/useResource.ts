import { useCallback, useEffect, useRef, useState } from "react"
import { errorMessage } from "@/lib/utils"

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

const defaultMapError = (err: unknown): string => errorMessage(err)

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
  // Synced via an effect (not assigned during render) so refs are only ever
  // written during the commit phase; `run` only reads them from later
  // effects/callbacks/intervals, never synchronously in the same render.
  const onDataRef = useRef(opts.onData)
  const mapErrorRef = useRef(opts.mapError)
  useEffect(() => {
    onDataRef.current = opts.onData
    mapErrorRef.current = opts.mapError
  })

  // Starts a fetch immediately and synchronously -- callable directly from an
  // effect's top level (unlike `run` below) because it never touches state,
  // so a mount-triggered load actually starts in the same tick React commits
  // the effect, which several callers (and tests asserting on network
  // activity right after mount) depend on. A synchronous throw from
  // `fetcher` is caught here and turned into a rejection rather than left to
  // propagate synchronously.
  const begin = useCallback((): { id: number; pending: Promise<T> } => {
    const id = ++requestId.current
    let pending: Promise<T>
    try {
      pending = Promise.resolve(fetcher())
    } catch (syncErr) {
      pending = Promise.reject(syncErr as unknown)
    }
    return { id, pending }
  }, [fetcher])

  // Applies the outcome of a fetch started by `begin`. Every state update
  // lives here, so this (unlike `begin`) must never be called directly from
  // an effect's top level -- callers defer it a microtask (see the mount
  // effect below) or call it from a non-effect context (event handler, poll
  // interval callback).
  const commit = useCallback(
    async (id: number, pending: Promise<T>, background: boolean): Promise<void> => {
      // A foreground load owns the spinner and clears the prior error up
      // front; a background refresh leaves both alone so the current view
      // stays put.
      if (!background) {
        setLoading(true)
        setError(null)
      }
      try {
        const result = await pending
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
    [],
  )

  // Public refresh entry point (manual refresh, poll interval): starts and
  // commits a fetch in one call. Never invoked directly from an effect's top
  // level, so `commit`'s setState calls are not subject to the same
  // deferral `begin`/`commit` need when driven from the mount effect below.
  const run = useCallback(
    async ({ background = false }: { background?: boolean } = {}): Promise<void> => {
      const { id, pending } = begin()
      await commit(id, pending, background)
    },
    [begin, commit],
  )

  const setData = useCallback((value: T) => {
    requestId.current += 1
    setDataState(value)
    setError(null)
    setLoading(false)
  }, [])

  // Initial load + reload whenever the fetcher identity changes. The cleanup
  // bumps the guard so a response still in flight at unmount / fetcher-change
  // can never write state. `begin()` is called directly (synchronously
  // starting the fetch in this tick); `commit` -- which does all the state
  // updates -- is deferred one microtask so this effect never directly calls
  // a function that itself calls setState. That microtask still resolves
  // well before the next paint, so this is imperceptible to the user and to
  // `act()`-wrapped tests (which already drain the microtask queue).
  useEffect(() => {
    if (!immediate) return
    const { id, pending } = begin()
    void Promise.resolve().then(() => commit(id, pending, false))
    return () => {
      requestId.current += 1
    }
  }, [begin, commit, immediate])

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
