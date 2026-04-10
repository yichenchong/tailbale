import { useEffect, useRef } from "react"
import { api } from "./api"

interface DashboardSummary {
  services: { error: number }
}

/**
 * Polls the dashboard summary and updates the favicon to reflect overall health.
 * Green monitor = all healthy, red monitor = at least one error.
 *
 * Pass `null` to disable polling (e.g. when the user isn't authenticated).
 */
export function useDynamicFavicon(intervalMs: number | null = 30_000) {
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    if (intervalMs === null) return

    function update() {
      api
        .get<DashboardSummary>("/dashboard/summary")
        .then((data) => {
          const hasError = data.services.error > 0
          const href = hasError ? "/favicon-error.svg" : "/favicon-healthy.svg"
          let link = document.querySelector<HTMLLinkElement>("link[rel='icon']")
          if (!link) {
            link = document.createElement("link")
            link.rel = "icon"
            link.type = "image/svg+xml"
            document.head.appendChild(link)
          }
          if (link.href !== href) {
            link.href = href
          }
        })
        .catch(() => {
          // If we can't reach the API, show error favicon
          const link = document.querySelector<HTMLLinkElement>("link[rel='icon']")
          if (link) link.href = "/favicon-error.svg"
        })
    }

    update()
    timerRef.current = setInterval(update, intervalMs)

    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [intervalMs])
}
