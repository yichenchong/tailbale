/**
 * Shared test scaffolding for the frontend suite (AR11).
 *
 * Replaces the per-file inline `<MemoryRouter>` wrappers / local
 * `renderWithRoute` helpers (`renderRoute`) and the hand-built
 * `vi.stubGlobal("fetch", vi.fn()...)` routers (`mockApi` + the `json*`
 * response helpers). Behaviour is identical to the literals it supersedes —
 * `mockApi` returns exactly the `{ ok, status, json }` Response-ish shapes the
 * old per-file `mockFetch` closures produced.
 */
import { render } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { vi } from "vitest"
import type { ReactElement } from "react"
import type { RenderResult } from "@testing-library/react"

export interface RenderRouteOptions {
  /**
   * Route pattern to mount `ui` under (e.g. `/services/:id`). When set the tree
   * is wrapped in `<Routes><Route path={path} .../></Routes>` so the page can
   * read `useParams`. Omit for pages that don't depend on a path param.
   */
  path?: string
  /** MemoryRouter history entries; defaults to `["/"]` (or `[path]` when a path is given). */
  initialEntries?: string[]
}

/**
 * Render `ui` inside a `MemoryRouter`, optionally under a parameterized route.
 * Drop-in for the repeated `render(<MemoryRouter ...>...</MemoryRouter>)` blocks
 * and the `renderWithRoute` helpers that ServiceDetail/ExposeService inlined.
 */
export function renderRoute(ui: ReactElement, options: RenderRouteOptions = {}): RenderResult {
  const { path, initialEntries } = options
  const entries = initialEntries ?? (path ? [path] : ["/"])
  return render(
    <MemoryRouter initialEntries={entries}>
      {path ? (
        <Routes>
          <Route path={path} element={ui} />
        </Routes>
      ) : (
        ui
      )}
    </MemoryRouter>,
  )
}

/** A minimal `Response`-like object — the subset the app's `api.ts` reads. */
export interface FakeResponse {
  ok: boolean
  status: number
  json: () => Promise<unknown>
}

type JsonBody = unknown | ((url: string, init?: RequestInit) => unknown)

function resolveBody(body: JsonBody, url: string, init?: RequestInit): unknown {
  return typeof body === "function"
    ? (body as (url: string, init?: RequestInit) => unknown)(url, init)
    : body
}

/** A single route entry for {@link mockApi}. */
export interface MockRoute {
  /** Matched against the request URL (substring, or a RegExp test). Omit for a catch-all. */
  url?: string | RegExp
  /** Restrict to a single HTTP method (case-insensitive). GET matches when no method is set. */
  method?: string
  /** Response body, or a function of (url, init) returning it. */
  json?: JsonBody
  /** `Response.ok`; defaults to `true`. */
  ok?: boolean
  /** `Response.status`; defaults to `200` (or `500` when `ok` is `false`). */
  status?: number
}

function urlMatches(matcher: string | RegExp | undefined, url: string): boolean {
  if (matcher === undefined) return true
  return typeof matcher === "string" ? url.includes(matcher) : matcher.test(url)
}

function methodMatches(routeMethod: string | undefined, init?: RequestInit): boolean {
  if (routeMethod === undefined) return true
  const actual = (init?.method ?? "GET").toUpperCase()
  return actual === routeMethod.toUpperCase()
}

/**
 * Build a typed fetch router keyed by URL (and optionally method). Routes are
 * tried in order; the first whose `url`/`method` match wins. A route with no
 * `url` acts as the catch-all. Unmatched requests resolve to `{}` with `ok:true`.
 *
 * Install with `vi.stubGlobal("fetch", mockApi([...]))`.
 */
export function mockApi(routes: MockRoute[]) {
  return vi.fn((input: string | URL | Request, init?: RequestInit) => {
    const url = String(typeof input === "object" && "url" in input ? input.url : input)
    const route = routes.find((r) => urlMatches(r.url, url) && methodMatches(r.method, init))
    const ok = route?.ok ?? true
    const status = route?.status ?? (ok ? 200 : 500)
    const body = route ? resolveBody(route.json, url, init) : {}
    return Promise.resolve({ ok, status, json: () => Promise.resolve(body) } as FakeResponse)
  })
}

/** Fetch mock that always resolves the same JSON body (`ok:true`, status 200). */
export function jsonOk(body: JsonBody) {
  return vi.fn((input: string | URL | Request, init?: RequestInit) => {
    const url = String(typeof input === "object" && "url" in input ? input.url : input)
    return Promise.resolve({
      ok: true,
      status: 200,
      json: () => Promise.resolve(resolveBody(body, url, init)),
    } as FakeResponse)
  })
}

/** Fetch mock that resolves an error Response (`ok:false`) carrying `{ detail }`. */
export function jsonError(detail: string, status = 500) {
  return vi.fn(() =>
    Promise.resolve({
      ok: false,
      status,
      json: () => Promise.resolve({ detail }),
    } as FakeResponse),
  )
}

/** Fetch mock whose promise never settles — pins a component in its loading state. */
export function pendingFetch() {
  return vi.fn(() => new Promise<FakeResponse>(() => {}))
}
