import { describe, expect, it } from "vitest"

import {
  STATUS_FALLBACK_STYLE,
  phaseStyle,
  jobStatusStyle,
  eventLevelStyle,
} from "@/lib/statusStyles"

/**
 * COVERAGE for the domain-state -> badge-class maps. Each helper must return a
 * specific style for every known state and the shared neutral fallback for any
 * unknown one (including transient/inactive backend phases like
 * "checking_health"/"disabled" the maps deliberately don't enumerate).
 */

describe("phaseStyle (service reconciliation phase)", () => {
  it("maps each known phase to its pill style", () => {
    expect(phaseStyle("healthy")).toBe("bg-green-100 text-green-700")
    expect(phaseStyle("pending")).toBe("bg-yellow-100 text-yellow-700")
    expect(phaseStyle("warning")).toBe("bg-yellow-100 text-yellow-700")
    expect(phaseStyle("error")).toBe("bg-red-100 text-red-700")
    expect(phaseStyle("failed")).toBe("bg-red-100 text-red-700")
  })

  it("falls back to neutral for transient/inactive or unknown phases", () => {
    // Backend also persists "checking_health" (transient) and "disabled"
    // (inactive); both legitimately render with the shared neutral pill.
    expect(phaseStyle("checking_health")).toBe(STATUS_FALLBACK_STYLE)
    expect(phaseStyle("disabled")).toBe(STATUS_FALLBACK_STYLE)
    expect(phaseStyle("")).toBe(STATUS_FALLBACK_STYLE)
    expect(phaseStyle("bogus")).toBe(STATUS_FALLBACK_STYLE)
  })
})

describe("jobStatusStyle (orphan-DNS job status)", () => {
  it("maps each known status to its pill style", () => {
    expect(jobStatusStyle("pending")).toBe("bg-yellow-100 text-yellow-800")
    expect(jobStatusStyle("running")).toBe("bg-blue-100 text-blue-700")
    expect(jobStatusStyle("failed")).toBe("bg-red-100 text-red-700")
    // A succeeded job deletes its row server-side, so "completed" is never sent,
    // but the style stays defined as a harmless safety net.
    expect(jobStatusStyle("completed")).toBe("bg-green-100 text-green-700")
  })

  it("falls back to neutral for unknown statuses", () => {
    expect(jobStatusStyle("queued")).toBe(STATUS_FALLBACK_STYLE)
    expect(jobStatusStyle("")).toBe(STATUS_FALLBACK_STYLE)
  })
})

describe("eventLevelStyle (event severity level)", () => {
  it("maps each known level to its pill style", () => {
    expect(eventLevelStyle("info")).toBe("bg-blue-100 text-blue-700")
    expect(eventLevelStyle("warning")).toBe("bg-yellow-100 text-yellow-800")
    expect(eventLevelStyle("error")).toBe("bg-red-100 text-red-700")
  })

  it("falls back to neutral for unknown levels", () => {
    expect(eventLevelStyle("debug")).toBe(STATUS_FALLBACK_STYLE)
    expect(eventLevelStyle("")).toBe(STATUS_FALLBACK_STYLE)
  })
})
