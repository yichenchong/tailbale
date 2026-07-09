/**
 * Single source of truth for the client-side validators that MIRROR backend
 * rules. These exist purely to give instant UI feedback / block doomed requests
 * before a server 422; the backend remains authoritative. Each helper documents
 * the exact backend constraint it shadows so drift is caught by validation.test.ts.
 */

/**
 * Slug used to derive edge container / network / ts-hostname names from a
 * SERVICE NAME. Mirrors the backend's `slugify` (backend/app/services/mapping.py):
 *
 *     slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
 *     return slug or "service"
 *
 * Keep in sync so the Expose Review preview matches what the server creates. (On
 * a name collision the backend appends -2/-3; the preview shows the un-suffixed
 * base, which is the common case.)
 */
export function slugify(name: string): string {
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "")
  return slug || "service"
}

/**
 * Prefills a hostname prefix from a container name as a valid DNS label.
 * Mirrors {@link slugify}'s charset/`-` handling (lowercase, replace invalid
 * chars with `-`, collapse repeats, strip leading/trailing hyphens) so e.g.
 * container "web." -> "web", not "web-". Unlike `slugify` there is no
 * "service" fallback: an empty prefix is a legitimate (invalid) prefill the
 * user then corrects, gated by ExposeService's RFC-1035 label regex.
 */
export function hostnamePrefix(containerName: string): string {
  return containerName
    .toLowerCase()
    .replace(/[^a-z0-9-]/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-+|-+$/g, "")
}

/**
 * Whole number >= 1. Mirrors the backend `Field(ge=1)` constraint on the
 * General tab's numeric settings (`reconcile_interval_seconds`,
 * `cert_renewal_window_days` in backend/app/schemas/settings.py), which are
 * typed `int` server-side. The API rejects a blank/zeroed field AND a fractional
 * value (e.g. 1.5 -> 422 "valid integer"), so accept only a whole number >= 1.
 * `<input type=number>` permits decimals, so the isInteger check is load-bearing.
 */
export function isPositiveInt(value: string): boolean {
  const n = Number(value)
  return value.trim() !== "" && Number.isInteger(n) && n >= 1
}

/**
 * Non-blank after trimming. Mirrors the backend `Field(min_length=1)` constraint
 * (paired with the server-side `.strip()`) on required text settings — Cloudflare
 * `zone_id`, Tailscale `control_url`/`default_ts_hostname_prefix`, Docker
 * `socket_path` (backend/app/schemas/settings.py). A blank/whitespace-only value
 * 422s, so the UI blocks the save before a doomed request fires.
 */
export function isNonBlank(value: string): boolean {
  return value.trim() !== ""
}

/**
 * Loose "looks like an email" shape check. Mirrors the backend's lenient
 * `acme_email` validator (backend/app/schemas/settings.py
 * `validate_acme_email`):
 *
 *     re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value)
 *
 * Deliberately permissive — exactly one `@`, a non-empty whitespace-free local
 * part, and a domain carrying at least one dot. It only catches obvious typos,
 * never rejects a real address. The leading `.trim()` mirrors the backend's
 * `strip_strings` (a `mode="before"` validator that strips every string field
 * before the regex runs), so a value with surrounding whitespace classifies the
 * same on both sides — without it the client would false-reject " a@b.co" that
 * the server happily accepts. A blank/whitespace-only value trims to `""` and
 * fails the `[^@\s]+` local part, so this also implies non-blank.
 */
export function isEmailLike(value: string): boolean {
  return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(value.trim())
}

/**
 * Base domain: lowercase DNS labels, total length <= 253, label length <= 63.
 * Mirrors `GeneralSettingsUpdate.normalize_base_domain`
 * (backend/app/schemas/settings.py): the backend strips strings, lowercases the
 * domain, applies the same label regex, then enforces the same DNS length
 * ceilings. Uppercase input is accepted here because the server normalizes it
 * before validation.
 */
export function isBaseDomain(value: string): boolean {
  const domain = value.trim().toLowerCase()
  if (!/^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$/.test(domain)) {
    return false
  }
  return domain.length <= 253 && domain.split(".").every((label) => label.length <= 63)
}

/**
 * Setup username: non-blank after trimming. The auth router strips before
 * storing/looking up usernames; block whitespace-only input client-side so the
 * setup wizard cannot create an effectively empty admin username.
 */
export function isUsername(value: string): boolean {
  return value.trim() !== ""
}

/** Password used for setup/new-password flows. Mirrors Field(min_length=8). */
export function isPassword(value: string): boolean {
  return value.length >= 8
}

/** Current/login password. Mirrors Field(min_length=1); do not trim passwords. */
export function isPresentPassword(value: string): boolean {
  return value.length > 0
}


/**
 * Upstream port: a whole number in the inclusive TCP range 1..65535. Mirrors the
 * backend `Field(..., ge=1, le=65535)` constraint on `upstream_port`
 * (backend/app/schemas/services.py:63 `ServiceCreate`, :98 `ServiceUpdate`),
 * typed `int` server-side. Accepts a `string` (from an `<input>`/`<select>`) or a
 * number; `Number("")`/`Number("abc")` collapse to 0/NaN and fail the range, and
 * the `isInteger` check rejects a fractional `<input type=number>` value that the
 * server would 422. Both service forms feed this the raw field value.
 */
export function isUpstreamPort(value: string | number): boolean {
  const n = Number(value)
  return Number.isInteger(n) && n >= 1 && n <= 65535
}

/**
 * Service name: non-blank and at most 128 chars after trimming. Mirrors the
 * backend `Field(..., min_length=1, max_length=128)` on `name` paired with the
 * `strip_name` (`mode="before"`) validator that trims before the length checks
 * (backend/app/schemas/services.py:59/71-75 `ServiceCreate`, :96/106-110
 * `ServiceUpdate`). The leading `.trim()` matches that server-side strip so a
 * whitespace-only name fails `min_length` on both sides, and a value over 128
 * chars gives instant feedback rather than a server 422 after submit.
 *
 * Length is measured in Unicode CODE POINTS (`[...trimmed].length`), NOT
 * `String.length` (UTF-16 code units), because the backend counts with Python
 * `len()` which is code-point-based. Using `.length` would over-count any name
 * containing astral characters (emoji, some CJK) — a 65-emoji name is 65 code
 * points (backend-accepted) but 130 UTF-16 units, so `.length > 128` would
 * false-reject a name the server happily takes (a doomed-block instead of a
 * doomed-request). Code-point counting keeps both sides in lockstep.
 */
export function isServiceName(value: string): boolean {
  const length = [...value.trim()].length
  return length >= 1 && length <= 128
}

/**
 * Shared, single-source validation messages for the service forms (ExposeService
 * + ServiceDetail), so the twin copies can't drift. Each pairs with the matching
 * {@link isServiceName}/{@link isUpstreamPort} guard above.
 */
export const SERVICE_NAME_REQUIRED_MESSAGE = "Service name is required"
export const SERVICE_NAME_LENGTH_MESSAGE = "Service name must be 128 characters or fewer"
export const UPSTREAM_PORT_MESSAGE = "Upstream port must be a whole number from 1 to 65535"
