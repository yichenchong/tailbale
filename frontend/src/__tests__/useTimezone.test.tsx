import { describe, it, expect, beforeEach, beforeAll, afterAll } from "vitest"
import { render, screen, act } from "@testing-library/react"
import {
  useTimezone,
  setConfiguredTimezone,
  _resetTimezoneCache,
  formatDateTime,
  parseBackendDate,
} from "@/lib/useTimezone"

function TzProbe() {
  const tz = useTimezone()
  return <span data-testid="tz">{tz}</span>
}

describe("useTimezone", () => {
  beforeEach(() => {
    _resetTimezoneCache()
  })

  it("re-renders mounted consumers when the timezone changes", () => {
    render(<TzProbe />)
    // Two independent consumers should both pick up the change reactively.
    render(<TzProbe />)

    act(() => {
      setConfiguredTimezone("America/New_York")
    })

    const probes = screen.getAllByTestId("tz")
    expect(probes).toHaveLength(2)
    for (const node of probes) {
      expect(node.textContent).toBe("America/New_York")
    }
  })

  it("initializes new consumers from the cached timezone", () => {
    act(() => {
      setConfiguredTimezone("Europe/Berlin")
    })
    render(<TzProbe />)
    expect(screen.getByTestId("tz").textContent).toBe("Europe/Berlin")
  })
})

// Force a non-UTC host timezone so the naive-vs-UTC parsing distinction is
// observable. On a UTC host the buggy local parse coincides with UTC and the
// regression silently passes. Node honors runtime TZ changes for Date parsing.
describe("backend timestamp parsing (non-UTC host)", () => {
  const originalTz = process.env.TZ
  beforeAll(() => {
    process.env.TZ = "America/New_York"
  })
  afterAll(() => {
    if (originalTz === undefined) delete process.env.TZ
    else process.env.TZ = originalTz
  })

  describe("parseBackendDate", () => {
    it("treats an offset-less backend timestamp as UTC", () => {
      // Backend func.now() fields serialize without a tz designator but mean UTC.
      expect(parseBackendDate("2026-06-21T12:00:00").toISOString()).toBe(
        "2026-06-21T12:00:00.000Z",
      )
    })

    it("leaves a 'Z'-suffixed timestamp unchanged", () => {
      expect(parseBackendDate("2026-06-21T12:00:00Z").toISOString()).toBe(
        "2026-06-21T12:00:00.000Z",
      )
    })

    it("honors an explicit offset instead of clobbering it with UTC", () => {
      // A naive-append-Z fix that ignored existing offsets would report 12:00Z.
      expect(parseBackendDate("2026-06-21T12:00:00+02:00").toISOString()).toBe(
        "2026-06-21T10:00:00.000Z",
      )
    })

    it("preserves sub-second precision on naive timestamps", () => {
      expect(parseBackendDate("2026-06-21T12:00:00.500").toISOString()).toBe(
        "2026-06-21T12:00:00.500Z",
      )
    })
  })

  describe("formatDateTime", () => {
    it("renders an offset-less timestamp identically to its 'Z' form", () => {
      // Regression: an offset-less string must not be parsed as local time and
      // then re-projected, which double-offsets it on non-UTC browsers.
      const tz = "America/New_York"
      expect(formatDateTime("2026-06-21T12:00:00", tz)).toBe(
        formatDateTime("2026-06-21T12:00:00Z", tz),
      )
    })

    it("projects a UTC instant into the configured zone deterministically", () => {
      // Date-object input is unaffected by string parsing, so it pins the
      // expected projection regardless of the host machine's local timezone.
      const utcInstant = new Date(Date.UTC(2026, 5, 21, 12, 0, 0))
      expect(formatDateTime("2026-06-21T12:00:00", "Asia/Tokyo")).toBe(
        formatDateTime(utcInstant, "Asia/Tokyo"),
      )
    })

    it("returns an empty string for unparseable input", () => {
      expect(formatDateTime("not-a-date", "UTC")).toBe("")
    })
  })
})
