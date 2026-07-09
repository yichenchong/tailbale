/* eslint-disable react-refresh/only-export-components -- test utility exports fixtures plus JSX render helpers. */
import { vi } from "vitest"
import ExposeService from "@/pages/ExposeService"
import { renderRoute } from "./testkit"
import { makeSettings } from "./factories"
import { useParams } from "react-router-dom"

export const mockSettings = makeSettings()

export const mockCreatedService = {
  id: "svc_new123",
  name: "nginx",
  enabled: true,
  upstream_container_id: "c1",
  upstream_container_name: "nginx",
  upstream_scheme: "http",
  upstream_port: 80,
  hostname: "nginx.example.com",
  base_domain: "example.com",
  edge_container_name: "edge_nginx",
  network_name: "edge_net_nginx",
  ts_hostname: "edge-nginx",
  preserve_host_header: true,
  custom_caddy_snippet: null,
  app_profile: null,
  healthcheck_path: null,
  status: { phase: "pending", message: "Awaiting first reconciliation", tailscale_ip: null, edge_container_id: null, last_reconciled_at: null, health_checks: null, cert_expires_at: null },
  created_at: "2026-04-05T00:00:00",
  updated_at: "2026-04-05T00:00:00",
}

export function stubSettingsFetch() {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
    ok: true,
    json: () => Promise.resolve(mockSettings),
  }))
}

export async function renderExpose(entry = "/expose?container_id=c1&container_name=nginx&image=nginx:latest&ports=[]") {
  return renderRoute(<ExposeService />, { initialEntries: [entry] })
}

export function exposeFetchRouter({
  settings = mockSettings,
  profile = { detected_profile: null, profile: null },
  createdService = mockCreatedService,
}: {
  settings?: unknown
  profile?: unknown
  createdService?: unknown
} = {}) {
  return vi.fn((url: string, opts?: RequestInit) => {
    if (String(url).includes("/settings")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(settings) })
    }
    if (String(url).includes("/profiles/detect")) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(profile) })
    }
    if (opts?.method === "POST") {
      return Promise.resolve({ ok: true, json: () => Promise.resolve(createdService) })
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
  })
}

export function ServiceIdProbe() {
  const { id } = useParams<{ id: string }>()
  return <div data-testid="matched-id">{id}</div>
}
