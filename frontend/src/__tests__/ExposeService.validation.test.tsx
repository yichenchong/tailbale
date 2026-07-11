import { describe, it, expect, vi, beforeEach } from "vitest"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import ExposeService from "@/pages/ExposeService"
import { mockCreatedService, mockSettings, stubSettingsFetch } from "./exposeServiceTestUtils"

beforeEach(() => {
  vi.restoreAllMocks()
  stubSettingsFetch()
})

describe("ExposeService page - validation", () => {
  it("requires a discovered container before creating a service", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockSettings),
        })
      }
      if (String(url).includes("/profiles/detect")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ detected_profile: null, profile: null }),
        })
      }
      if (opts?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockCreatedService),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    vi.stubGlobal("fetch", fetchMock)

    render(
      <MemoryRouter initialEntries={["/expose"]}>
        <ExposeService />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText("Create Service")).toBeInTheDocument()
    })
    fireEvent.change(screen.getByRole("textbox", { name: "Service Name" }), { target: { value: "manual" } })
    fireEvent.change(screen.getByText("Hostname Prefix").closest("label")!.querySelector("input")!, { target: { value: "manual" } })

    expect(screen.getByRole("button", { name: "Create Service" })).toBeDisabled()
    fireEvent.submit(screen.getByRole("button", { name: "Create Service" }).closest("form")!)

    expect(screen.getByText("Choose a discovered container before creating a service")).toBeInTheDocument()
    // Submit-validation error is injected asynchronously; it must announce to
    // assistive tech via a live region (role="alert").
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Choose a discovered container before creating a service"
    )
    expect(
      fetchMock.mock.calls.some(
        (call: unknown[]) =>
          String(call[0]).includes("/api/services") &&
          typeof call[1] === "object" &&
          (call[1] as RequestInit).method === "POST"
      )
    ).toBe(false)
  })

  it("keeps Create Service disabled until settings load", async () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})))
    render(
      <MemoryRouter initialEntries={["/expose?container_id=c1&container_name=nginx&image=nginx:latest&ports=[]"]}>
        <ExposeService />
      </MemoryRouter>
    )

    expect(screen.getByRole("button", { name: "Create Service" })).toBeDisabled()
  })

  it("blocks invalid hostname prefixes before creating the service", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockSettings),
        })
      }
      if (String(url).includes("/profiles/detect")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ detected_profile: null, profile: null }),
        })
      }
      if (opts?.method === "POST") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockCreatedService),
        })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    vi.stubGlobal("fetch", fetchMock)

    render(
      <MemoryRouter initialEntries={["/expose?container_id=c1&container_name=nginx&image=nginx:latest&ports=[]"]}>
        <ExposeService />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Create Service" })).toBeEnabled()
    })

    const hostnameInput = screen.getByText("Hostname Prefix").closest("label")!.querySelector("input")!
    fireEvent.change(hostnameInput, { target: { value: "-bad" } })

    expect(screen.getByRole("button", { name: "Create Service" })).toBeDisabled()
    expect(
      screen.getByText(/Must start and end with a lowercase letter or number/)
    ).toBeInTheDocument()
    expect(
      fetchMock.mock.calls.some(
        (c: unknown[]) =>
          String(c[0]).includes("/api/services") &&
          typeof c[1] === "object" &&
          (c[1] as RequestInit).method === "POST"
      )
    ).toBe(false)
  })

  it("blocks a service name longer than the backend's 128-char limit", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSettings) })
      }
      if (String(url).includes("/profiles/detect")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ detected_profile: null, profile: null }) })
      }
      if (opts?.method === "POST") {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockCreatedService) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    vi.stubGlobal("fetch", fetchMock)

    render(
      <MemoryRouter initialEntries={["/expose?container_id=c1&container_name=nginx&image=nginx:latest&ports=[]"]}>
        <ExposeService />
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Create Service" })).toBeEnabled()
    })

    // 129 chars: one over the backend ServiceCreate.name max_length of 128.
    fireEvent.change(screen.getByRole("textbox", { name: "Service Name" }), {
      target: { value: "a".repeat(129) },
    })

    expect(screen.getByRole("button", { name: "Create Service" })).toBeDisabled()
    // Submitting the form directly (bypassing the disabled button) surfaces the
    // validation message and still must not POST.
    fireEvent.submit(screen.getByRole("button", { name: "Create Service" }).closest("form")!)
    expect(screen.getByText("Service name must be 128 characters or fewer")).toBeInTheDocument()
    expect(
      fetchMock.mock.calls.some(
        (c: unknown[]) =>
          String(c[0]).includes("/api/services") &&
          typeof c[1] === "object" &&
          (c[1] as RequestInit).method === "POST"
      )
    ).toBe(false)
  })
})
