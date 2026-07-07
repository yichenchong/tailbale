import { describe, expect, it } from "vitest"

import {
  slugify,
  hostnamePrefix,
  isPositiveInt,
  isNonBlank,
  isEmailLike,
  isUpstreamPort,
  isServiceName,
} from "@/lib/validation"

/**
 * CONTRACT TESTS. Each validator mirrors a backend rule; these assert the
 * client produces the output the backend rule requires, so any future drift in
 * either copy is caught here. Backend source is cited per block.
 */

describe("slugify (mirrors _slugify, backend/app/services/crud.py)", () => {
  // re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "service"
  it("lowercases and replaces runs of non-alphanumerics with a single dash", () => {
    expect(slugify("My Service")).toBe("my-service")
    expect(slugify("Foo___Bar")).toBe("foo-bar")
    expect(slugify("a  b   c")).toBe("a-b-c")
  })

  it("strips leading and trailing dashes", () => {
    expect(slugify("  web.  ")).toBe("web")
    expect(slugify("--edge--")).toBe("edge")
    expect(slugify("...Plex...")).toBe("plex")
  })

  it("falls back to 'service' when the slug would be empty", () => {
    expect(slugify("")).toBe("service")
    expect(slugify("---")).toBe("service")
    expect(slugify("!@#$")).toBe("service")
  })

  it("preserves embedded digits and single internal dashes", () => {
    expect(slugify("nginx-1")).toBe("nginx-1")
    expect(slugify("App2You")).toBe("app2you")
  })
})

describe("hostnamePrefix (DNS-label prefill, mirrors slugify charset handling)", () => {
  // ExposeService prefill: lowercase, invalid->'-', collapse repeats, strip ends.
  // No "service" fallback — an empty prefix is a legitimate (invalid) prefill.
  it("lowercases and turns invalid characters into single dashes", () => {
    expect(hostnamePrefix("My App")).toBe("my-app")
    expect(hostnamePrefix("Foo.Bar")).toBe("foo-bar")
  })

  it("collapses dash runs and strips leading/trailing dashes", () => {
    expect(hostnamePrefix("web.")).toBe("web")
    expect(hostnamePrefix("--a__b--")).toBe("a-b")
  })

  it("keeps existing hyphens (unlike slugify, hyphens are valid input)", () => {
    expect(hostnamePrefix("my-edge")).toBe("my-edge")
  })

  it("returns an empty string when nothing valid remains (no fallback)", () => {
    expect(hostnamePrefix("!!!")).toBe("")
    expect(hostnamePrefix("")).toBe("")
  })
})

describe("isPositiveInt (mirrors Field(ge=1), backend/app/schemas/settings.py:18-19)", () => {
  // reconcile_interval_seconds / cert_renewal_window_days: int, ge=1. A
  // blank/zero/negative/fractional value 422s server-side.
  it("accepts whole numbers >= 1", () => {
    expect(isPositiveInt("1")).toBe(true)
    expect(isPositiveInt("60")).toBe(true)
    expect(isPositiveInt(" 30 ")).toBe(true)
  })

  it("rejects blank, zero, and negative values", () => {
    expect(isPositiveInt("")).toBe(false)
    expect(isPositiveInt("   ")).toBe(false)
    expect(isPositiveInt("0")).toBe(false)
    expect(isPositiveInt("-5")).toBe(false)
  })

  it("rejects fractional values (backend wants a valid integer)", () => {
    expect(isPositiveInt("1.5")).toBe(false)
    expect(isPositiveInt("2.0")).toBe(true) // Number("2.0") === 2, an integer
  })

  it("rejects non-numeric input", () => {
    expect(isPositiveInt("abc")).toBe(false)
    expect(isPositiveInt("1abc")).toBe(false)
  })
})

describe("isNonBlank (mirrors Field(min_length=1)+.strip(), backend/app/schemas/settings.py)", () => {
  // zone_id, control_url, default_ts_hostname_prefix, socket_path: min_length=1
  // paired with a server-side strip — a whitespace-only value 422s.
  it("accepts any value with non-whitespace content", () => {
    expect(isNonBlank("x")).toBe(true)
    expect(isNonBlank("  trimmed  ")).toBe(true)
  })

  it("rejects empty and whitespace-only values", () => {
    expect(isNonBlank("")).toBe(false)
    expect(isNonBlank("   ")).toBe(false)
    expect(isNonBlank("\t\n")).toBe(false)
  })
})

describe("isEmailLike (mirrors validate_acme_email, backend/app/schemas/settings.py:40-48)", () => {
  // re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value) after strip_strings trims.
  it("accepts a plausible address shape", () => {
    expect(isEmailLike("admin@example.com")).toBe(true)
    expect(isEmailLike("a.b+tag@sub.example.co.uk")).toBe(true)
  })

  it("trims surrounding whitespace before matching (mirrors backend strip_strings)", () => {
    // The backend strips before the regex, so a padded-but-valid address is
    // accepted server-side; the client must not false-reject it.
    expect(isEmailLike("  admin@example.com  ")).toBe(true)
  })

  it("rejects obvious mistakes the backend regex also rejects", () => {
    expect(isEmailLike("no-at-sign.com")).toBe(false) // missing '@'
    expect(isEmailLike("admin@nodot")).toBe(false) // domain has no dot
    expect(isEmailLike("admin@@example.com")).toBe(false) // local part holds '@'
    expect(isEmailLike("a b@example.com")).toBe(false) // internal whitespace
    expect(isEmailLike("@example.com")).toBe(false) // empty local part
    expect(isEmailLike("admin@.com")).toBe(false) // empty domain label before dot
  })

  it("rejects blank / whitespace-only input (implies non-blank)", () => {
    expect(isEmailLike("")).toBe(false)
    expect(isEmailLike("   ")).toBe(false)
    expect(isEmailLike("\t\n")).toBe(false)
  })
})

describe("isUpstreamPort (mirrors Field(ge=1, le=65535), backend/app/schemas/services.py:63/98)", () => {
  // upstream_port: int, 1..65535 inclusive. A blank/zero/out-of-range/fractional
  // value 422s server-side; the wizard's <input>/<select> feeds a string.
  it("accepts whole ports across the inclusive 1..65535 range", () => {
    expect(isUpstreamPort("1")).toBe(true)
    expect(isUpstreamPort("80")).toBe(true)
    expect(isUpstreamPort("65535")).toBe(true)
    expect(isUpstreamPort(443)).toBe(true) // numeric input also accepted
  })

  it("rejects blank, zero, negative, and above-range ports", () => {
    expect(isUpstreamPort("")).toBe(false)
    expect(isUpstreamPort("   ")).toBe(false)
    expect(isUpstreamPort("0")).toBe(false)
    expect(isUpstreamPort("-1")).toBe(false)
    expect(isUpstreamPort("65536")).toBe(false)
    expect(isUpstreamPort("70000")).toBe(false)
  })

  it("rejects fractional and non-numeric ports (backend wants a valid integer)", () => {
    expect(isUpstreamPort("80.5")).toBe(false)
    expect(isUpstreamPort("abc")).toBe(false)
    expect(isUpstreamPort("8080abc")).toBe(false)
  })
})

describe("isServiceName (mirrors Field(min_length=1, max_length=128)+strip, backend/app/schemas/services.py:59/71-75)", () => {
  // name: trimmed server-side (strip_name), then 1..128 chars. Whitespace-only
  // fails min_length; >128 fails max_length.
  it("accepts a non-blank name within 128 chars (after trimming)", () => {
    expect(isServiceName("Nextcloud")).toBe(true)
    expect(isServiceName("  padded name  ")).toBe(true)
    expect(isServiceName("a".repeat(128))).toBe(true)
  })

  it("rejects blank / whitespace-only names (trims to empty, fails min_length)", () => {
    expect(isServiceName("")).toBe(false)
    expect(isServiceName("   ")).toBe(false)
    expect(isServiceName("\t\n")).toBe(false)
  })

  it("rejects names longer than 128 chars after trimming", () => {
    expect(isServiceName("a".repeat(129))).toBe(false)
    // Surrounding whitespace is stripped first, so 128 real chars + padding pass.
    expect(isServiceName(`  ${"a".repeat(128)}  `)).toBe(true)
  })

  it("counts length in Unicode code points, not UTF-16 units, matching Python len()", () => {
    // Backend `Field(max_length=128)` counts with Python `len()` (code points).
    // An emoji is one code point but two UTF-16 units, so a `.length`-based
    // check would false-reject a name the server accepts. 65 emoji = 65 code
    // points (backend-accepted) but 130 UTF-16 units.
    expect("😀".repeat(65).length).toBe(130) // guards the UTF-16 assumption
    expect(isServiceName("😀".repeat(65))).toBe(true)
    expect(isServiceName("😀".repeat(128))).toBe(true) // exactly 128 code points
    expect(isServiceName("😀".repeat(129))).toBe(false) // 129 code points > max
  })
})
