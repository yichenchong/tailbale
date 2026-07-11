import { describe, expect, it } from "vitest"

import {
  STATUS_FALLBACK_STYLE,
  phaseStyle,
  phaseLabel,
  jobStatusStyle,
  eventLevelStyle,
} from "@/lib/statusStyles"

/**
 * COVERAGE for the domain-state -> badge-class maps. Each helper must return a
 * specific style for every known state and the shared neutral fallback for any
 * unknown one. Service phase has three tiers: terminal-state pills, the shared
 * blue "in-progress" pill for transient reconcile-step phases, and the neutral
 * fallback for inactive/unknown ("disabled", "").
 */

describe("phaseStyle (service reconciliation phase)", () => {
  it("maps each known phase to its pill style", () => {
    expect(phaseStyle("healthy")).toBe("bg-green-100 text-green-700")
    expect(phaseStyle("pending")).toBe("bg-yellow-100 text-yellow-700")
    expect(phaseStyle("warning")).toBe("bg-yellow-100 text-yellow-700")
    expect(phaseStyle("error")).toBe("bg-red-100 text-red-700")
    expect(phaseStyle("failed")).toBe("bg-red-100 text-red-700")
  })

  it("maps every transient reconcile-step phase to the shared blue in-progress pill", () => {
    // The reconciler commits these phases between steps (reconciler/steps/*) and
    // the live UI polls them; they must read as actively-working (blue), not the
    // grey neutral fallback. checking_health is one of them.
    const IN_PROGRESS = "bg-blue-100 text-blue-700"
    for (const phase of [
      "validating",
      "creating_network",
      "ensuring_edge",
      "detecting_ip",
      "ensuring_dns",
      "ensuring_cert",
      "rendering_config",
      "reloading_caddy",
      "checking_health",
    ]) {
      expect(phaseStyle(phase)).toBe(IN_PROGRESS)
    }
  })

  it("falls back to neutral for inactive or unknown phases", () => {
    // "disabled" is inactive (not in-progress) and stays neutral; so do the
    // empty string and any unknown phase.
    expect(phaseStyle("disabled")).toBe(STATUS_FALLBACK_STYLE)
    expect(phaseStyle("")).toBe(STATUS_FALLBACK_STYLE)
    expect(phaseStyle("bogus")).toBe(STATUS_FALLBACK_STYLE)
  })
})

describe("phaseLabel (service phase display text)", () => {
  it("uses the explicit label where the naive fallback would mangle an acronym/name", () => {
    expect(phaseLabel("detecting_ip")).toBe("Detecting IP")
    expect(phaseLabel("ensuring_dns")).toBe("Ensuring DNS")
    expect(phaseLabel("ensuring_cert")).toBe("Ensuring certificate")
    expect(phaseLabel("reloading_caddy")).toBe("Reloading Caddy")
    expect(phaseLabel("creating_network")).toBe("Creating network")
    expect(phaseLabel("checking_health")).toBe("Checking health")
  })

  it("Sentence-cases an unmapped snake_case phase via the generic fallback", () => {
    // Terminal phases carry no map entry and render through the fallback.
    expect(phaseLabel("pending")).toBe("Pending")
    expect(phaseLabel("healthy")).toBe("Healthy")
    expect(phaseLabel("disabled")).toBe("Disabled")
    expect(phaseLabel("some_new_phase")).toBe("Some new phase")
  })

  it("returns an empty string unchanged", () => {
    expect(phaseLabel("")).toBe("")
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
