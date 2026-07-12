import { describe, it, expect, vi, beforeEach } from "vitest"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import ExposeService from "@/pages/ExposeService"
import { ServiceIdProbe, mockCreatedService, mockSettings, stubSettingsFetch } from "./exposeServiceTestUtils"

beforeEach(() => {
  vi.restoreAllMocks()
  stubSettingsFetch()
})

describe("ExposeService page - submit", () => {
  it("calls API and navigates after submit", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      // Settings GET
      if (String(url).includes("/settings")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve(mockSettings),
        })
      }
      // Profile detect
      if (String(url).includes("/profiles/detect")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ detected_profile: null, profile: null }),
        })
      }
      // Service POST (create)
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
      expect(screen.getByText("Create Service")).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText("Create Service"))

    // Verify the POST was made
    await waitFor(() => {
      const postCalls = fetchMock.mock.calls.filter(
        (c: unknown[]) => typeof c[1] === "object" && (c[1] as RequestInit).method === "POST"
      )
      expect(postCalls.length).toBeGreaterThanOrEqual(1)
    })
  })

  it("submits the service form when Enter is pressed in a field", async () => {
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
      expect(screen.getByText("Create Service")).toBeInTheDocument()
    })

    await userEvent.click(screen.getByRole("textbox", { name: "Service Name" }))
    await userEvent.keyboard("{Enter}")

    await waitFor(() => {
      expect(
        fetchMock.mock.calls.some(
          (c: unknown[]) =>
            String(c[0]).includes("/api/services") &&
            typeof c[1] === "object" &&
            (c[1] as RequestInit).method === "POST"
        )
      ).toBe(true)
    })
  })

  it("does not double-submit the service when the form is re-submitted while saving", async () => {
    let postCount = 0
    let resolvePost: (() => void) | null = null
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSettings) })
      }
      if (String(url).includes("/profiles/detect")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ detected_profile: null, profile: null }) })
      }
      if (opts?.method === "POST") {
        postCount += 1
        // Keep the first POST in flight (saving stays true) so the second
        // submit exercises the in-flight guard rather than a fresh create.
        return new Promise((resolve) => {
          resolvePost = () => resolve({ ok: true, json: () => Promise.resolve(mockCreatedService) })
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

    // Submit via the form (the keyboard/Enter path), not the button.
    const form = screen.getByRole("button", { name: "Create Service" }).closest("form")!
    fireEvent.submit(form)

    // The first submit flips the form into the saving state with the POST pending.
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /Creating/ })).toBeDisabled()
    })
    expect(screen.getByRole("button", { name: /Creating/ })).toHaveAttribute("aria-busy", "true")

    // A second submit while that POST is still in flight must be ignored.
    fireEvent.submit(form)
    await Promise.resolve()
    expect(postCount).toBe(1)

    // Settle the in-flight POST so no promise dangles past the test.
    await act(async () => {
      resolvePost?.()
    })
  })

  it("ignores a synchronous double-fire of the submit event (ref-based in-flight guard)", async () => {
    // A state-based `if (saving) return` guard only blocks a second submit once
    // React has committed the `saving=true` re-render. Two submit events
    // dispatched within a single batch (before that commit) both close over
    // `saving=false` and slip through -> two POSTs. A ref set synchronously at
    // the top of the handler closes that window. This dispatches both submits
    // inside ONE act() so no re-render lands between them.
    let postCount = 0
    let resolvePost: (() => void) | null = null
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSettings) })
      }
      if (String(url).includes("/profiles/detect")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ detected_profile: null, profile: null }) })
      }
      if (opts?.method === "POST") {
        postCount += 1
        return new Promise((resolve) => {
          resolvePost = () => resolve({ ok: true, json: () => Promise.resolve(mockCreatedService) })
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

    const form = screen.getByRole("button", { name: "Create Service" }).closest("form")!
    await act(async () => {
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }))
      form.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }))
    })

    expect(postCount).toBe(1)

    // Settle the in-flight POST so no promise dangles past the test.
    await act(async () => {
      resolvePost?.()
    })
  })

  it("allows a retry after a failed submit (in-flight guard resets on error)", async () => {
    let postCount = 0
    const fetchMock = vi.fn().mockImplementation((url: string, opts?: RequestInit) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSettings) })
      }
      if (String(url).includes("/profiles/detect")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ detected_profile: null, profile: null }) })
      }
      if (opts?.method === "POST") {
        postCount += 1
        // First attempt fails; second succeeds. The ref-based guard must clear
        // on the failure so the second submit is not permanently blocked.
        return postCount === 1
          ? Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({ detail: "boom" }) })
          : Promise.resolve({ ok: true, json: () => Promise.resolve(mockCreatedService) })
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

    const form = screen.getByRole("button", { name: "Create Service" }).closest("form")!
    fireEvent.submit(form)
    // The failed POST re-enables the button (saving reset) and clears the ref.
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Create Service" })).toBeEnabled()
    })
    expect(postCount).toBe(1)

    // Second submit must go through (a second POST fires).
    fireEvent.submit(form)
    await waitFor(() => {
      expect(postCount).toBe(2)
    })
  })

  it("encodes the created service id when navigating to its detail page", async () => {
    // Service ids are server-generated and currently URL-safe, but the navigate
    // target must still be encoded (as every other service route is) so a slash
    // or space in an id can't break route matching on the detail page.
    const fetchMock = vi.fn((url: string, opts?: RequestInit) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSettings) })
      }
      if (String(url).includes("/profiles/detect")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ detected_profile: null, profile: null }) })
      }
      if (opts?.method === "POST") {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ ...mockCreatedService, id: "svc/odd id" }) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    vi.stubGlobal("fetch", fetchMock)

    render(
      <MemoryRouter initialEntries={["/expose?container_id=c1&container_name=nginx&image=nginx:latest&ports=[]"]}>
        <Routes>
          <Route path="/expose" element={<ExposeService />} />
          <Route path="/services/:id" element={<ServiceIdProbe />} />
        </Routes>
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Create Service" })).toBeEnabled()
    })
    fireEvent.click(screen.getByText("Create Service"))

    // With the id encoded, the slash-containing id round-trips through the route
    // and lands on the detail page; an unencoded navigate would mis-match.
    await waitFor(() => {
      expect(screen.getByTestId("matched-id")).toHaveTextContent("svc/odd id")
    })
  })

  it("submits the detected app_profile and the edited optional fields", async () => {
    // The create POST must carry the auto-detected app_profile (so the backend
    // applies profile-specific behavior) plus every optional field the user
    // edits — scheme, healthcheck path, the enable toggle, and the custom Caddy
    // snippet. Prior submit tests only exercised the all-default path.
    const fetchMock = vi.fn((url: string, opts?: RequestInit) => {
      if (String(url).includes("/settings")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockSettings) })
      }
      if (String(url).includes("/profiles/detect")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({
            detected_profile: "nextcloud",
            profile: {
              name: "Nextcloud",
              recommended_port: 80,
              healthcheck_path: "/status.php",
              preserve_host_header: false,
              post_setup_reminder: null,
              image_patterns: ["nextcloud"],
            },
          }),
        })
      }
      if (opts?.method === "POST") {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(mockCreatedService) })
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve({}) })
    })
    vi.stubGlobal("fetch", fetchMock)

    render(
      <MemoryRouter initialEntries={["/expose?container_id=c1&container_name=nextcloud&image=nextcloud:28&ports=[]"]}>
        <ExposeService />
      </MemoryRouter>
    )

    // Wait for the profile to be detected (its defaults have been applied).
    await waitFor(() => {
      expect(screen.getByText(/Detected/)).toBeInTheDocument()
    })

    fireEvent.change(screen.getByRole("combobox", { name: "Upstream Scheme" }), { target: { value: "https" } })
    fireEvent.change(screen.getByRole("textbox", { name: "Healthcheck Path (optional)" }), { target: { value: "/custom-health" } })
    fireEvent.click(screen.getByRole("checkbox", { name: "Enable immediately" }))
    fireEvent.change(screen.getByPlaceholderText("Additional Caddy directives..."), { target: { value: "header X-Test 1" } })
    fireEvent.click(screen.getByRole("button", { name: "Add network" }))
    fireEvent.change(screen.getByRole("textbox", { name: "Additional Docker network 1" }), { target: { value: "opencloud_opencloud-net" } })
    fireEvent.change(screen.getByRole("textbox", { name: "Aliases for additional network 1" }), { target: { value: "cloud.example.com" } })

    fireEvent.click(screen.getByText("Create Service"))

    await waitFor(() => {
      const createCall = fetchMock.mock.calls.find(
        (c: unknown[]) =>
          String(c[0]).includes("/api/services") &&
          typeof c[1] === "object" &&
          (c[1] as RequestInit).method === "POST"
      )
      expect(createCall).toBeTruthy()
      expect(JSON.parse(String((createCall?.[1] as RequestInit).body))).toMatchObject({
        name: "nextcloud",
        app_profile: "nextcloud",
        upstream_scheme: "https",
        healthcheck_path: "/custom-health",
        enabled: false,
        custom_caddy_snippet: "header X-Test 1",
        preserve_host_header: false,
        additional_networks: [
          { name: "opencloud_opencloud-net", aliases: ["cloud.example.com"] },
        ],
      })
    })
  })
})
