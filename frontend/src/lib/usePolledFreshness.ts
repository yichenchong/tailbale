import { useCallback, useState } from "react"

export interface PolledFreshnessState {
  lastRefresh: Date | null
  markFresh: () => void
}

export function usePolledFreshness(): PolledFreshnessState {
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null)
  const markFresh = useCallback(() => setLastRefresh(new Date()), [])
  return { lastRefresh, markFresh }
}
