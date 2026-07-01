import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen } from "@testing-library/react"
import { useParams, useLocation } from "react-router-dom"
import { renderRoute, mockApi, jsonOk, jsonError, pendingFetch } from "./testkit"
import { makeService, makeSettings, makeEvent, makeContainer, makeJob } from "./factories"

beforeEach(() => {
  vi.restoreAllMocks()
})

// Guards the shared scaffolding itself: if renderRoute/mockApi/factories drift,
// every migrated page test would fail confusingly — these pin the contract.
describe("testkit", () => {
  it("renderRoute mounts a plain element under a MemoryRouter", () => {
    renderRoute(<div>hello</div>)
    expect(screen.getByText("hello")).toBeInTheDocument()
  })

  it("renderRoute wires a path param and default initial entry", () => {
    function Probe() {
      const { id } = useParams<{ id: string }>()
      return <div data-testid="id">{id}</div>
    }
    renderRoute(<Probe />, { path: "/services/:id", initialEntries: ["/services/svc_9"] })
    expect(screen.getByTestId("id")).toHaveTextContent("svc_9")
  })

  it("renderRoute defaults initialEntries to [path]", () => {
    function Probe() {
      const loc = useLocation()
      return <div data-testid="loc">{loc.pathname}</div>
    }
    renderRoute(<Probe />, { path: "/only" })
    expect(screen.getByTestId("loc")).toHaveTextContent("/only")
  })

  it("mockApi routes by url and method, with a catch-all fallback", async () => {
    const fetchMock = mockApi([
      { url: "/settings", json: { setting: true } },
      { url: "/services", method: "POST", json: { created: true } },
      { json: { fallback: true } },
    ])
    const settings = await (await fetchMock("/api/settings")).json()
    expect(settings).toEqual({ setting: true })
    const post = await (await fetchMock("/api/services", { method: "POST" })).json()
    expect(post).toEqual({ created: true })
    // GET /services does not match the POST-only route → falls through to catch-all.
    const get = await (await fetchMock("/api/services")).json()
    expect(get).toEqual({ fallback: true })
  })

  it("mockApi honors ok/status and defaults ok:false to 500", async () => {
    const fetchMock = mockApi([{ url: "/boom", ok: false }])
    const res = await fetchMock("/api/boom")
    expect(res.ok).toBe(false)
    expect(res.status).toBe(500)
  })

  it("jsonOk resolves a constant body; jsonError carries a detail; pendingFetch never settles", async () => {
    const ok = await (await jsonOk({ a: 1 })("/x")).json()
    expect(ok).toEqual({ a: 1 })
    const errRes = await jsonError("nope", 404)("/x")
    expect(errRes.ok).toBe(false)
    expect(errRes.status).toBe(404)
    expect(await errRes.json()).toEqual({ detail: "nope" })
    let settled = false
    void pendingFetch()("/x").then(() => {
      settled = true
    })
    await Promise.resolve()
    expect(settled).toBe(false)
  })

  it("factories default to the shapes the page tests hand-write", () => {
    expect(makeService()).toMatchObject({ id: "svc_abc123", name: "Nextcloud" })
    expect(makeService().status?.phase).toBe("pending")
    expect(makeSettings().general.base_domain).toBe("example.com")
    expect(makeEvent().kind).toBe("cert_issued")
    expect(makeContainer().name).toBe("nextcloud")
    expect(makeJob().status).toBe("pending")
  })

  it("factory overrides replace defaults (including nested via explicit override)", () => {
    expect(makeService({ name: "Other" }).name).toBe("Other")
    expect(makeSettings({ setup_complete: true }).setup_complete).toBe(true)
    expect(makeJob({ status: "failed" }).status).toBe("failed")
  })

  it("mockApi installs as a global fetch stub for a real fetch call", async () => {
    vi.stubGlobal("fetch", mockApi([{ url: "/ping", json: { pong: true } }]))
    const body = await (await fetch("/api/ping")).json()
    expect(body).toEqual({ pong: true })
  })
})
