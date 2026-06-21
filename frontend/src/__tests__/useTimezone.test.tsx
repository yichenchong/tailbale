import { describe, it, expect, beforeEach } from "vitest"
import { render, screen, act } from "@testing-library/react"
import {
  useTimezone,
  setConfiguredTimezone,
  _resetTimezoneCache,
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
