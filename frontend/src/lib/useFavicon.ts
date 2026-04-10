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
 * Uses raw `fetch` instead of `api.get` to avoid the 401 interceptor
 * that triggers a hard redirect to /login (which would cause a reload loop
 * if the user isn't authenticated).
 */
export function useDynamicFavicon(intervalMs = 30_000) {
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    function update() {
      fetch("/api/dashboard/summary", { credentials: "same-origin" })
        .then((r) => (r.ok ? r.json() : null))
        .then((data: DashboardSummary | null) => {
          if (!data) return // 401 or error — leave favicon alone
          const hasError = data.services.error > 0
          setFavicon(hasError ? "/favicon-error.svg" : "/favicon-healthy.svg")
        })
        .catch(() => {})
    }

    update()
    timerRef.current = setInterval(update, intervalMs)

    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
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
