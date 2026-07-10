/**
 * sibling vocabularies (service phase, job status, event level) that all map a
 * string state onto a `bg-*-100 text-*-{600,700,800}` pill style, with one
 * shared fallback for any state the map doesn't know about. Service phase has an
 * extra tier: the transient reconcile-step phases share one "in-progress" blue
 * pill. {@link phaseLabel} turns a raw phase into display text. Mirrors the
 * "classify once, expose a helper" pattern in {@link ./certStatus.ts}.
 */

/** Neutral pill style for any state not covered by a specific map. */
export const STATUS_FALLBACK_STYLE = "bg-zinc-100 text-zinc-600"

const PHASE_STYLES: Record<string, string> = {
  healthy: "bg-green-100 text-green-700",
  pending: "bg-yellow-100 text-yellow-700",
  warning: "bg-yellow-100 text-yellow-700",
  error: "bg-red-100 text-red-700",
  failed: "bg-red-100 text-red-700",
}

// Blue "in-progress" pill for the transient reconcile-step phases the reconciler
// persists between steps (backend/app/reconciler/steps/*, each committed and
// polled by the live UI). Shares job "running"'s blue so an actively-working
// service reads the same across surfaces, instead of the grey neutral fallback.
const IN_PROGRESS_PHASE_STYLE = "bg-blue-100 text-blue-700"

const IN_PROGRESS_PHASES: Record<string, true> = {
  validating: true,
  creating_network: true,
  ensuring_edge: true,
  detecting_ip: true,
  ensuring_dns: true,
  ensuring_cert: true,
  rendering_config: true,
  reloading_caddy: true,
  checking_health: true,
}

// Display labels for the phases whose Sentence-case fallback would mangle an
// acronym/product name (IP/DNS/Caddy) or otherwise read poorly. Every other
// phase (terminal states + the plainly-cased in-progress ones) falls through to
// the generic snake_case -> Sentence-case fallback in {@link phaseLabel}.
const PHASE_LABELS: Record<string, string> = {
  validating: "Validating",
  creating_network: "Creating network",
  ensuring_edge: "Ensuring edge",
  detecting_ip: "Detecting IP",
  ensuring_dns: "Ensuring DNS",
  ensuring_cert: "Ensuring certificate",
  rendering_config: "Rendering config",
  reloading_caddy: "Reloading Caddy",
  checking_health: "Checking health",
}

const JOB_STATUS_STYLES: Record<string, string> = {
  pending: "bg-yellow-100 text-yellow-800",
  running: "bg-blue-100 text-blue-700",
  failed: "bg-red-100 text-red-700",
  completed: "bg-green-100 text-green-700",
}

const EVENT_LEVEL_STYLES: Record<string, string> = {
  info: "bg-blue-100 text-blue-700",
  warning: "bg-yellow-100 text-yellow-800",
  error: "bg-red-100 text-red-700",
}

/**
 * Badge classes for a service reconciliation phase (Services, ServiceDetail).
 * Terminal phases (healthy/pending/warning/error/failed) get their explicit
 * pill; the transient reconcile-step phases share the blue in-progress pill;
 * anything else (e.g. "disabled") gets the neutral fallback.
 */
export function phaseStyle(phase: string): string {
  return (
    PHASE_STYLES[phase] ||
    (IN_PROGRESS_PHASES[phase] ? IN_PROGRESS_PHASE_STYLE : STATUS_FALLBACK_STYLE)
  )
}

/**
 * Human-readable label for a service phase. Uses the {@link PHASE_LABELS} map
 * where a naive fallback would mangle an acronym/name, else generic snake_case
 * -> Sentence-case ("reloading_caddy" -> "Reloading caddy", but that one is
 * mapped). An empty phase returns "" unchanged.
 */
export function phaseLabel(phase: string): string {
  if (PHASE_LABELS[phase]) return PHASE_LABELS[phase]
  if (!phase) return phase
  return phase.charAt(0).toUpperCase() + phase.slice(1).replace(/_/g, " ")
}

/** Badge classes for a job status (OrphanDns). */
export function jobStatusStyle(status: string): string {
  return JOB_STATUS_STYLES[status] || STATUS_FALLBACK_STYLE
}

/** Badge classes for an event severity level (Events). */
export function eventLevelStyle(level: string): string {
  return EVENT_LEVEL_STYLES[level] || STATUS_FALLBACK_STYLE
}
