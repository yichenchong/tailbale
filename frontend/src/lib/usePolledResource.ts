import { useCallback, useEffect, useRef, useState } from "react"

import { useResource, type UseResourceOptions, type UseResourceResult } from "@/lib/useResource"

export interface UsePolledResourceOptions<T> extends Omit<UseResourceOptions<T>, "pollMs"> {
  /** Background poll cadence in ms. Omit for a one-shot (non-polling) fetch. */
  intervalMs?: number
}

export interface UsePolledResourceResult<T> extends UseResourceResult<T> {
  /** Timestamp of the most recent successful fetch, or `null` before the first one lands. */
  lastSuccessAt: Date | null
}

/**
 * `useResource` plus a "last good fetch" timestamp.
 *
 * Folds together the three-piece wiring the polling pages (Dashboard/Discover)
 * each repeated: `usePolledFreshness` (the `lastRefresh` / `markFresh` pair) +
 * `onData: markFresh` + `useResource({ pollMs })`. It drives the same background
 * poll and stamps `lastSuccessAt` on every *winning* response — the one that
 * passes `useResource`'s monotonic request-id guard and actually stores — so a
 * {@link StaleDataBanner} / `PolledRefreshControl` can report when the on-screen
 * data was last refreshed. Behavior is identical to the old `markFresh`, which
 * likewise ran only on winning responses (foreground or background).
 */
export function usePolledResource<T>(
  fetcher: () => Promise<T>,
  { intervalMs, onData, ...opts }: UsePolledResourceOptions<T> = {},
): UsePolledResourceResult<T> {
  const [lastSuccessAt, setLastSuccessAt] = useState<Date | null>(null)

  // Hold the caller's onData in a ref so `markFresh` stays referentially stable
  // (and so `useResource`'s `run` identity does) without capturing a stale one.
  // Synced via an effect (not during render) since `markFresh` only reads it
  // later, from useResource's own async fetch effects/poll interval.
  const onDataRef = useRef(onData)
  useEffect(() => {
    onDataRef.current = onData
  })

  const markFresh = useCallback((data: T): boolean | void => {
    // Preserve the caller's onData contract: if it claims the response (returns
    // `true` — e.g. a pagination clamp that deliberately skips storing an empty
    // page and retriggers the load), this was NOT a successful store, so it does
    // not count as fresh. Otherwise stamp the success time and store as usual.
    const claimed = onDataRef.current?.(data)
    if (claimed === true) return true
    setLastSuccessAt(new Date())
    return claimed
  }, [])

  const resource = useResource(fetcher, { ...opts, pollMs: intervalMs, onData: markFresh })
  return { ...resource, lastSuccessAt }
}
