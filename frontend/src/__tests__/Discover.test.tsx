import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor, fireEvent } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"

/** Build a fetch mock that returns discovery data for /discovery/ and services data for /services. */
function mockFetchResponses(
  containers: unknown[] = [],
  services: unknown[] = [],
) {
  return vi.fn().mockImplementation((url: string) => {
    if (String(url).includes("/services")) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ services, total: services.length }),
      })
    }
    // Default: discovery endpoint
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ containers, total: containers.length }),
    })
  })
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("Discover page", () => {
  it("shows loading state initially", async () => {
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})))
    const { default: Discover } = await import("@/pages/Discover")
    render(
      <MemoryRouter>
        <Discover />
      </MemoryRouter>
    )
    expect(screen.getByText("Loading containers...")).toBeInTheDocument()
  })

  it("renders empty state when no containers", async () => {
    vi.stubGlobal("fetch", mockFetchResponses([], []))
    const { default: Discover } = await import("@/pages/Discover")
    render(
      <MemoryRouter>
        <Discover />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText(/No containers found/)).toBeInTheDocument()
    })
  })

  it("renders container list with data", async () => {
    vi.stubGlobal("fetch", mockFetchResponses(
      [{
        id: "c1",
        name: "nextcloud",
        image: "nextcloud:28",
        status: "running",
        state: "running",
        ports: [{ container_port: "80", host_port: "9080", protocol: "tcp" }],
        networks: ["bridge"],
        labels: {},
      }],
      [],
    ))
    const { default: Discover } = await import("@/pages/Discover")
    render(
      <MemoryRouter>
        <Discover />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("nextcloud")).toBeInTheDocument()
    })
    expect(screen.getByText("nextcloud:28")).toBeInTheDocument()
    expect(screen.getByText("running")).toBeInTheDocument()
    expect(screen.getByText("80/tcp")).toBeInTheDocument()
    expect(screen.getByText("Expose")).toBeInTheDocument()
  })

  it("shows exposure count badge for already-exposed container", async () => {
    vi.stubGlobal("fetch", mockFetchResponses(
      [{
        id: "c1",
        name: "nextcloud",
        image: "nextcloud:28",
        status: "running",
        state: "running",
        ports: [{ container_port: "80", host_port: "9080", protocol: "tcp" }],
        networks: ["bridge"],
        labels: {},
      }],
      [
        { upstream_container_id: "c1", name: "Nextcloud Web", hostname: "nc.example.com", status: { phase: "healthy" } },
      ],
    ))
    const { default: Discover } = await import("@/pages/Discover")
    render(
      <MemoryRouter>
        <Discover />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("1 svc")).toBeInTheDocument()
    })
  })

  it("shows search input", async () => {
    vi.stubGlobal("fetch", mockFetchResponses([], []))
    const { default: Discover } = await import("@/pages/Discover")
    render(
      <MemoryRouter>
        <Discover />
      </MemoryRouter>
    )
    expect(screen.getByPlaceholderText("Search by name or image...")).toBeInTheDocument()
  })

  it("has running only checkbox checked by default", async () => {
    vi.stubGlobal("fetch", mockFetchResponses([], []))
    const { default: Discover } = await import("@/pages/Discover")
    render(
      <MemoryRouter>
        <Discover />
      </MemoryRouter>
    )
    const checkbox = screen.getByLabelText("Running only")
    expect(checkbox).toBeChecked()
  })

  it("shows Refresh button", async () => {
    vi.stubGlobal("fetch", mockFetchResponses([], []))
    const { default: Discover } = await import("@/pages/Discover")
    render(
      <MemoryRouter>
        <Discover />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Refresh")).toBeInTheDocument()
    })
  })

  it("shows last refresh timestamp after load", async () => {
    vi.stubGlobal("fetch", mockFetchResponses([], []))
    const { default: Discover } = await import("@/pages/Discover")
    render(
      <MemoryRouter>
        <Discover />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText(/Updated/)).toBeInTheDocument()
    })
  })

  it("reloads data when Refresh button clicked", async () => {
    const fetchMock = mockFetchResponses([], [])
    vi.stubGlobal("fetch", fetchMock)
    const { default: Discover } = await import("@/pages/Discover")
    render(
      <MemoryRouter>
        <Discover />
      </MemoryRouter>
    )
    await waitFor(() => {
      expect(screen.getByText("Refresh")).toBeInTheDocument()
    })
    const initialCallCount = fetchMock.mock.calls.length

    fireEvent.click(screen.getByText("Refresh"))

    await waitFor(() => {
      expect(fetchMock.mock.calls.length).toBeGreaterThan(initialCallCount)
    })
  })
})
