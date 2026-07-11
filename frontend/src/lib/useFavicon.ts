import { useEffect } from "react"
import { api, UnauthorizedError, type DashboardSummary } from "@/lib/api"

/**
 * Sets the document favicon to `href`, creating the <link rel="icon"> if needed.
 * @internal exported for tests
 */
export function setFavicon(href: string) {
  let link = document.querySelector<HTMLLinkElement>("link[rel='icon']")
  if (!link) {
    link = document.createElement("link")
    link.rel = "icon"
    link.type = "image/svg+xml"
    document.head.appendChild(link)
  }
  // Compare the attribute (relative) rather than `link.href` (which the DOM
  // resolves to an absolute URL), otherwise the guard never matches and the
  // favicon is needlessly rewritten — re-fetched — on every poll.
  if (link.getAttribute("href") !== href) {
    link.setAttribute("href", href)
  }
}

/**
 * Polls the dashboard summary and updates the favicon to reflect overall health.
 * Green monitor = no errors, red monitor = at least one error (warnings stay green).
 *
 * Self-guarding: if the request returns 401 (unauthenticated), polling stops
 * immediately so it can never cause a redirect loop.
 *
 * Deliberately NOT built on `useResource` (proposal F1). It fetches via
 * `api.getSafe` — the non-redirecting variant of the shared client — precisely
 * so a 401 does NOT redirect to /login; instead `getSafe` throws
 * `UnauthorizedError`, and this poller then stops the interval *permanently*.
 * Each effect owns its own stopped flag, which plays the same role as
 * useResource's monotonic request-id guard: it discards any response (and its
 * favicon write) that resolves after cleanup or a 401.
 */
export function useDynamicFavicon(intervalMs = 30_000) {
  useEffect(() => {
    let stopped = false
    let timer: number | null = null

    function stop() {
      stopped = true
      if (timer !== null) {
        window.clearInterval(timer)
        timer = null
      }
    }

    function update() {
      if (stopped) return
      api
        .getSafe<DashboardSummary>("/dashboard/summary")
        .then((data) => {
          if (stopped || !data) return
          const hasError = data.services.error > 0
          setFavicon(hasError ? "/favicon-error.svg" : "/favicon-healthy.svg")
        })
        .catch((err) => {
          // A 401 means we're unauthenticated — stop polling permanently so we
          // can never cause a redirect loop. Any other error (network blip,
          // non-2xx, parse failure) is transient: swallow it and keep polling.
          // The stopped flag is per-effect, so stale responses from StrictMode's
          // dev-only setup/cleanup/setup cycle (or an interval prop change) can
          // neither write the favicon nor stop the current poller.
          if (err instanceof UnauthorizedError && !stopped) stop()
        })
    }

    update()
    timer = window.setInterval(update, intervalMs)

    return () => stop()
  }, [intervalMs])
}

/**
 * Sets a static favicon — use on the login page where there's no health to poll.
 */
export function useStaticFavicon(href = "/favicon-healthy.svg") {
  useEffect(() => {
    setFavicon(href)
  }, [href])
}
