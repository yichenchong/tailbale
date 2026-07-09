import Setup from "@/pages/Setup"
import { fireEvent, screen, waitFor } from "@testing-library/react"
import { mockApi, renderRoute } from "./testkit"

export const FRESH_PROGRESS = {
  user_exists: false,
  base_domain_set: false,
  cloudflare_configured: false,
  acme_email_set: false,
  tailscale_configured: false,
  docker_configured: false,
}

export const ALL_DONE_PROGRESS = {
  user_exists: true,
  base_domain_set: true,
  cloudflare_configured: true,
  acme_email_set: true,
  tailscale_configured: true,
  docker_configured: true,
}

/** Mock fetch that returns setup-progress first, then all subsequent calls return data. */
export function mockFetchWithProgress(
  progress: Record<string, boolean>,
  data: unknown = { user: { id: "usr_1", username: "admin", display_name: null, role: "admin" }, success: true, message: "OK" },
) {
  return mockApi([
    { url: "/auth/setup-progress", json: progress },
    { json: data },
  ])
}

/** Fetch mock for a resumed setup where every step (including Docker) is already configured. */
export function mockResumeAllDone() {
  return mockApi([
    { url: "/auth/setup-progress", json: ALL_DONE_PROGRESS },
    { url: "/settings/test/docker", json: { success: true, message: "Docker connected" } },
    { json: {} },
  ])
}

export async function renderSetup() {
  return renderRoute(<Setup />)
}

export async function fillAccountStep(username = "testuser", password = "password123") {
  await waitFor(() => {
    expect(screen.getByPlaceholderText("admin")).toBeInTheDocument()
  })
  fireEvent.change(screen.getByPlaceholderText("admin"), { target: { value: username } })
  fireEvent.change(screen.getByPlaceholderText("Password"), { target: { value: password } })
  fireEvent.change(screen.getByPlaceholderText("Confirm password"), { target: { value: password } })
}

export async function advanceAccountStep(username = "testuser", password = "password123") {
  await fillAccountStep(username, password)
  fireEvent.click(screen.getByText("Next").closest("button")!)
  await waitFor(() => {
    expect(screen.getByText("Step 2 of 6: Domain")).toBeInTheDocument()
  })
}
