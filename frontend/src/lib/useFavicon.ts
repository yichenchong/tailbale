import { useEffect, useRef } from "react"
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
 * `UnauthorizedError`, and this poller then stops the interval *permanently* —
 * two behaviors the generic hook intentionally doesn't model (its 401s redirect
 * via `api`, and its polling is unconditional). The `stoppedRef` guard below
 * plays the same role as useResource's monotonic request-id guard: it discards
 * any response (and its favicon write) that resolves after unmount or a 401.
 */
export function useDynamicFavicon(intervalMs = 30_000) {
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const stoppedRef = useRef(false)

  useEffect(() => {
    stoppedRef.current = false

    function stop() {
      stoppedRef.current = true
      if (timerRef.current !== null) {
        clearInterval(timerRef.current)
        timerRef.current = null
      }
    }

    function update() {
      if (stoppedRef.current) return
      api
        .getSafe<DashboardSummary>("/dashboard/summary")
        .then((data) => {
          if (stoppedRef.current || !data) return
          const hasError = data.services.error > 0
          setFavicon(hasError ? "/favicon-error.svg" : "/favicon-healthy.svg")
        })
        .catch((err) => {
          // A 401 means we're unauthenticated — stop polling permanently so we
          // can never cause a redirect loop. Any other error (network blip,
          // non-2xx, parse failure) is transient: swallow it and keep polling.
          if (err instanceof UnauthorizedError) stop()
        })
    }

    update()
    timerRef.current = setInterval(update, intervalMs)

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
