import { describe, expect, it } from "vitest"

import { certStatus, CERT_SOON_DAYS, formatCertExpiry } from "@/lib/certStatus"

// Build a naive-UTC ISO string (no "Z"), matching how the backend serializes
// timestamps, offset from now by the given number of days.
function naiveUtcInDays(days: number): string {
  return new Date(Date.now() + days * 24 * 60 * 60 * 1000).toISOString().replace("Z", "")
}

describe("formatCertExpiry", () => {
  const tz = "UTC"

  it("returns the em-dash sentinel for a missing timestamp", () => {
    expect(formatCertExpiry(null, tz)).toEqual({ text: "—", style: "text-zinc-400" })
    expect(formatCertExpiry(undefined, tz)).toEqual({ text: "—", style: "text-zinc-400" })
  })

  it("returns the em-dash sentinel for an unparseable timestamp (NaN guard)", () => {
    // Pre-fix this fell through NaN comparisons to a gray "valid" style with empty text.
    expect(formatCertExpiry("not-a-date", tz)).toEqual({ text: "—", style: "text-zinc-400" })
  })

  it("flags an already-expired cert red", () => {
    expect(formatCertExpiry(naiveUtcInDays(-3), tz).style).toBe("text-red-600 font-medium")
  })

  it("flags a cert that expired less than a day ago red, not yellow (Math.ceil edge)", () => {
    // diffMs is negative but Math.ceil collapses daysLeft to 0, so a `daysLeft < 0`
    // gate would mislabel this freshly-expired cert "expiring soon" (yellow). The
    // red classification must key off the raw sign of diffMs, which this pins.
    expect(formatCertExpiry(naiveUtcInDays(-0.5), tz).style).toBe("text-red-600 font-medium")
  })

  it("flags a cert expiring within 14 days yellow", () => {
    expect(formatCertExpiry(naiveUtcInDays(5), tz).style).toBe("text-yellow-600 font-medium")
  })

  it("flags a far-future cert gray", () => {
    expect(formatCertExpiry(naiveUtcInDays(60), tz).style).toBe("text-zinc-500")
  })
})

describe("certStatus (shared cert urgency helper)", () => {
  it("returns the missing sentinel for absent or unparseable timestamps", () => {
    for (const v of [null, undefined, "not-a-date"]) {
      expect(certStatus(v)).toEqual({
        daysLeft: null,
        expired: false,
        urgency: "none",
        color: "text-zinc-400",
        label: "—",
      })
    }
  })

  it("classifies an expired cert with the 'Expired' label and red color", () => {
    const s = certStatus(naiveUtcInDays(-3))
    expect(s.expired).toBe(true)
    expect(s.urgency).toBe("expired")
    expect(s.color).toBe("text-red-600")
    expect(s.label).toBe("Expired")
  })

  it("labels a cert that lapsed within the last 24h Expired, not '0d left'", () => {
    const s = certStatus(naiveUtcInDays(-0.5))
    expect(s.expired).toBe(true)
    expect(s.label).toBe("Expired")
  })

  it("buckets a cert within the unified 14-day threshold as 'soon' (yellow)", () => {
    expect(CERT_SOON_DAYS).toBe(14)
    const s = certStatus(naiveUtcInDays(5))
    expect(s.urgency).toBe("soon")
    expect(s.color).toBe("text-yellow-600")
    expect(s.label).toBe("5d left")
  })

  it("includes the 14-day boundary in the yellow 'soon' bucket", () => {
    // ceil(13.5d) === 14 -> still within the threshold.
    expect(certStatus(naiveUtcInDays(13.5)).urgency).toBe("soon")
    // ceil(14.5d) === 15 -> just past the threshold, back to gray.
    expect(certStatus(naiveUtcInDays(14.5)).urgency).toBe("ok")
  })

  it("classifies a far-future cert as 'ok' (gray)", () => {
    const s = certStatus(naiveUtcInDays(60))
    expect(s.urgency).toBe("ok")
    expect(s.color).toBe("text-zinc-500")
    expect(s.label).toMatch(/^\d+d left$/)
  })
})
