import { describe, it, expect, vi, beforeEach } from "vitest"
import { fireEvent, screen, waitFor } from "@testing-library/react"
import { mockService, renderWithRoute } from "./serviceDetailTestUtils"

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("ServiceDetail page - edit", () => {
  it("prevents edit saves that violate backend constraints", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockService),
    }))
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => {
      expect(screen.getByText("Edit")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Edit"))
    const save = screen.getByText("Save")
    const port = screen.getByLabelText("Upstream Port")
    const name = screen.getByLabelText("Name")

    fireEvent.change(port, { target: { value: "70000" } })
    expect(save).toBeDisabled()

    fireEvent.change(port, { target: { value: "443" } })
    expect(save).toBeEnabled()

    fireEvent.change(name, { target: { value: "   " } })
    expect(save).toBeDisabled()
  })

  it("sends trimmed valid edit values with numeric port", async () => {
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (init?.method === "PUT") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ ...mockService, name: "Cloud", upstream_port: 443 }),
        })
      }
      if (url.endsWith("/edge-version")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ orchestrator_version: "1.0.0", edge_version: "1.0.0", up_to_date: true }),
        })
      }
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve(mockService),
      })
    })
    vi.stubGlobal("fetch", fetchMock)
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => {
      expect(screen.getByText("Edit")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Edit"))
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "  Cloud  " } })
    fireEvent.change(screen.getByLabelText("Upstream Port"), { target: { value: "443" } })
    fireEvent.click(screen.getByText("Save"))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/api/services/svc_abc123", expect.objectContaining({ method: "PUT" }))
    })
    const putCall = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT")
    expect(JSON.parse(String(putCall?.[1]?.body))).toMatchObject({
      name: "Cloud",
      upstream_port: 443,
    })
  })

  it("disables Save and shows inline feedback when the edited name exceeds 128 chars", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(mockService),
    }))
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => expect(screen.getByText("Edit")).toBeInTheDocument())

    fireEvent.click(screen.getByText("Edit"))
    const save = screen.getByText("Save")
    expect(save).toBeEnabled()

    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "a".repeat(129) } })
    expect(save).toBeDisabled()
    expect(screen.getByText("Service name must be 128 characters or fewer.")).toBeInTheDocument()
  })

  it("allows saving a name valid by code points but >128 UTF-16 units (emoji), and sends it", async () => {
    // Regression (post lib/validation code-point migration): handleSave and the
    // inline hint must delegate to the shared code-point isServiceName, not a
    // raw String.length (UTF-16) check. A 65-emoji name is 65 code points
    // (backend-accepted) but 130 UTF-16 units — the old `.length > 128` guard
    // would block the PUT and flash a false "too long" error, disagreeing with
    // nameValid (which enables Save).
    const emojiName = "\u{1F600}".repeat(65) // 65 code points, 130 UTF-16 units
    expect(emojiName.length).toBeGreaterThan(128) // UTF-16 units
    expect([...emojiName].length).toBe(65) // code points
    const fetchMock = vi.fn((url: string, init?: RequestInit) => {
      if (String(url).endsWith("/edge-version")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ orchestrator_version: "1.0.0", edge_version: "1.0.0", up_to_date: true }) })
      }
      if (init?.method === "PUT") {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ ...mockService, name: emojiName }) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(mockService) })
    })
    vi.stubGlobal("fetch", fetchMock)
    await renderWithRoute("/services/svc_abc123")
    await waitFor(() => expect(screen.getByText("Edit")).toBeInTheDocument())

    fireEvent.click(screen.getByText("Edit"))
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: emojiName } })

    // No false "too long" hint, and Save stays enabled.
    expect(screen.queryByText("Service name must be 128 characters or fewer.")).not.toBeInTheDocument()
    expect(screen.getByText("Save")).toBeEnabled()

    fireEvent.click(screen.getByText("Save"))
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(true)
    )
    const putCall = fetchMock.mock.calls.find(([, init]) => init?.method === "PUT")
    expect(JSON.parse(String(putCall?.[1]?.body))).toMatchObject({ name: emojiName })
  })
})
