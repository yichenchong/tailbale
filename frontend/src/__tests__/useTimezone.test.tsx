import { describe, it, expect, vi, beforeEach, afterEach, beforeAll, afterAll } from "vitest"
import { render, screen, act } from "@testing-library/react"
import {
  useTimezone,
  setConfiguredTimezone,
  _resetTimezoneCache,
  formatDateTime,
  formatDateTimeOrDash,
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

    it("falls back to a renderable string instead of throwing on an invalid tz", () => {
      // Regression: toLocaleString throws a RangeError on a bogus IANA zone, so
      // an unguarded formatter white-screens every timestamp during render.
      expect(() => formatDateTime("2026-06-21T12:00:00Z", "Not/AZone")).not.toThrow()
      expect(formatDateTime("2026-06-21T12:00:00Z", "Not/AZone")).not.toBe("")
    })

    it("keeps the explicit timezone authoritative over a bad options.timeZone", () => {
      // Regression: the dedicated `timezone` arg must win over `options`, or a
      // caller-supplied `options.timeZone` silently overrides both the
      // configured zone AND the UTC safety net, forcing a raw-ISO fallback.
      const withBadOption = formatDateTime("2026-06-21T12:00:00Z", "UTC", {
        timeZone: "Not/AZone",
      })
      const plain = formatDateTime("2026-06-21T12:00:00Z", "UTC")
      expect(withBadOption).toBe(plain)
      // And it must be the formatted UTC string, not the raw ISO fallback.
      expect(withBadOption).not.toBe("2026-06-21T12:00:00.000Z")
    })
  })
})

describe("formatDateTimeOrDash", () => {
  it("returns the em-dash placeholder for a missing value", () => {
    expect(formatDateTimeOrDash(null, "UTC")).toBe("\u2014")
    expect(formatDateTimeOrDash(undefined, "UTC")).toBe("\u2014")
    expect(formatDateTimeOrDash("", "UTC")).toBe("\u2014")
  })

  it("formats a valid date identically to formatDateTime", () => {
    const iso = "2026-06-21T12:00:00Z"
    expect(formatDateTimeOrDash(iso, "America/New_York")).toBe(
      formatDateTime(iso, "America/New_York"),
    )
  })

  it("renders the em-dash sentinel for an unparseable value (FL-OBS1)", () => {
    // A non-null but invalid value is truthy, so it slips past the `!date`
    // guard; formatDateTime returns "" for it, and OrDash must map that to the
    // sentinel rather than a blank cell. Cover both an invalid string and an
    // invalid Date object so a future refactor can't regress one of them.
    expect(formatDateTimeOrDash("not a date", "UTC")).toBe("\u2014")
    expect(formatDateTimeOrDash(new Date("nonsense"), "UTC")).toBe("\u2014")
  })
})

describe("useTimezone settings fetch", () => {
  beforeEach(() => {
    _resetTimezoneCache()
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("does not let a late settings fetch clobber a newer explicit timezone", async () => {
    // Hold the /api/settings response open so we control exactly when it lands.
    let resolveFetch!: (value: unknown) => void
    const pending = new Promise((res) => {
      resolveFetch = res
    })
    vi.stubGlobal("fetch", vi.fn().mockReturnValue(pending))

    // Mounting kicks off the (now in-flight) settings fetch because the cache is empty.
    render(<TzProbe />)

    // Meanwhile a newer value is set explicitly (e.g. the user saves settings).
    act(() => {
      setConfiguredTimezone("Asia/Tokyo")
    })
    expect(screen.getByTestId("tz").textContent).toBe("Asia/Tokyo")

    // The stale fetch finally resolves with a DIFFERENT, older timezone. Pre-fix
    // this called setConfiguredTimezone("UTC") and clobbered the explicit value.
    await act(async () => {
      resolveFetch({
        ok: true,
        json: () => Promise.resolve({ general: { timezone: "UTC" } }),
      })
      await Promise.resolve()
      await Promise.resolve()
    })

    // The explicit value must survive; the late fetch must be ignored.
    expect(screen.getByTestId("tz").textContent).toBe("Asia/Tokyo")
  })
})
