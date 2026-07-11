import { vi } from "vitest"
import ServiceDetail from "@/pages/ServiceDetail"
import { renderRoute } from "./testkit"
import { makeService } from "./factories"

export const mockService = makeService()

export async function renderWithRoute(path: string) {
  return renderRoute(<ServiceDetail />, { path: "/services/:id", initialEntries: [path] })
}

export function okServiceFetch(service: typeof mockService = mockService) {
  return vi.fn().mockResolvedValue({
    ok: true,
    json: () => Promise.resolve(service),
  })
}

export function renewFetchMock(messages: { refused: string; forced: string }) {
  const edgeVer = { orchestrator_version: "1.0.0", edge_version: "1.0.0", up_to_date: true }
  return vi.fn((url: string, init?: RequestInit) => {
    if (String(url).endsWith("/edge-version")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(edgeVer) })
    }
    if (init?.method === "POST" && String(url).includes("/renew-cert")) {
      const forced = String(url).includes("force=true")
      return Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            success: true,
            performed: forced,
            needs_force: !forced,
            message: forced ? messages.forced : messages.refused,
            expires_at: forced ? "2026-09-01T00:00:00" : null,
            last_failure: null,
          }),
      })
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve(mockService) })
  })
}

export const renewPosts = (mock: ReturnType<typeof vi.fn>) =>
  mock.mock.calls.filter(
    ([url, init]) => String(url).includes("/renew-cert") && (init as RequestInit | undefined)?.method === "POST",
  )
