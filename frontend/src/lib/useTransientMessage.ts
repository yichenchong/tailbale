import { useCallback, useEffect, useRef, useState } from "react"

export interface TransientMessage {
  /** The currently-visible message, or null when nothing is shown. */
  message: string | null
  /** Show `msg` and (re)arm the auto-clear timer. Replaces any pending timer. */
  show: (msg: string) => void
  /**
   * Soft-hide the visible message WITHOUT cancelling the pending timer, so a
   * newer message's timer stays the sole authority over when it disappears.
   */
  clear: () => void
}

/**
 * Auto-clearing transient status message (action toasts). Standardizes the
 * hand-rolled timer that Services and ServiceDetail each carried, using the
 * repo timer convention (`window.setTimeout` -> `number` id, not the
 * `@types/node` `Timeout`) and clearing the pending timer on unmount (AR3).
 */
export function useTransientMessage(durationMs: number): TransientMessage {
  const [message, setMessage] = useState<string | null>(null)
  const timerRef = useRef<number | null>(null)

  const cancelTimer = useCallback(() => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const show = useCallback(
    (msg: string) => {
      cancelTimer()
      setMessage(msg)
      timerRef.current = window.setTimeout(() => {
        setMessage(null)
        timerRef.current = null
      }, durationMs)
    },
    [cancelTimer, durationMs],
  )

  const clear = useCallback(() => setMessage(null), [])

  useEffect(() => cancelTimer, [cancelTimer])

  return { message, show, clear }
}
