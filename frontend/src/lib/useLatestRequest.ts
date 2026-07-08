import { useCallback, useEffect, useMemo, useRef } from "react"

export interface LatestRequest {
  /** Start a request and return the token that represents it. */
  next: () => number
  /** True only for the latest request token that has not been invalidated. */
  isCurrent: (token: number) => boolean
  /** Invalidate any in-flight token without starting a new request. */
  invalidate: () => void
}

/**
 * Small shared monotonic guard for hooks that coordinate several pieces of state
 * and therefore do not fit useResource's single-resource state machine.
 */
export function useLatestRequest(): LatestRequest {
  const latest = useRef(0)

  const next = useCallback(() => {
    latest.current += 1
    return latest.current
  }, [])

  const isCurrent = useCallback((token: number) => token === latest.current, [])

  const invalidate = useCallback(() => {
    latest.current += 1
  }, [])

  useEffect(() => invalidate, [invalidate])

  return useMemo(() => ({ next, isCurrent, invalidate }), [next, isCurrent, invalidate])
}
