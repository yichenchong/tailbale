import { useEffect, useRef } from "react"

interface DashboardSummary {
  services: { error: number }
}

function setFavicon(href: string) {
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
}

/**
 * Polls the dashboard summary and updates the favicon to reflect overall health.
 * Green monitor = all healthy, red monitor = at least one error.
 *
 * Self-guarding: if the fetch returns 401 (unauthenticated), polling stops
 * immediately so it can never cause a redirect loop.
 */
export function useDynamicFavicon(intervalMs = 30_000) {
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const stoppedRef = useRef(false)

  useEffect(() => {
    stoppedRef.current = false

    function stop() {
      stoppedRef.current = true
      if (timerRef.current) {
        clearInterval(timerRef.current)
        timerRef.current = null
      }
    }

    function update() {
      if (stoppedRef.current) return
      fetch("/api/dashboard/summary", { credentials: "same-origin" })
        .then((r) => {
          if (r.status === 401) {
            // Not authenticated — stop polling permanently
            stop()
            return null
          }
          return r.ok ? r.json() : null
        })
        .then((data: DashboardSummary | null) => {
          if (!data) return
          const hasError = data.services.error > 0
          setFavicon(hasError ? "/favicon-error.svg" : "/favicon-healthy.svg")
        })
        .catch(() => {})
    }

    update()
    timerRef.current = setInterval(update, intervalMs)

    return () => stop()
  }, [intervalMs])
}

/**
 * Sets a static favicon — use on the login page where there's no health to poll.
 */
export function useStaticFavicon(href = "/favicon.svg") {
  useEffect(() => {
    setFavicon(href)
  }, [href])
}
