/**
 * Single source of truth for domain-state -> Tailwind badge classes. Three
 * sibling vocabularies (service phase, job status, event level) that all map a
 * string state onto a `bg-*-100 text-*-{600,700,800}` pill style, with one
 * shared fallback for any state the map doesn't know about. Mirrors the
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

/** Badge classes for a service reconciliation phase (Services, ServiceDetail). */
export function phaseStyle(phase: string): string {
  return PHASE_STYLES[phase] || STATUS_FALLBACK_STYLE
}

/** Badge classes for a job status (OrphanDns). */
export function jobStatusStyle(status: string): string {
  return JOB_STATUS_STYLES[status] || STATUS_FALLBACK_STYLE
}

/** Badge classes for an event severity level (Events). */
export function eventLevelStyle(level: string): string {
  return EVENT_LEVEL_STYLES[level] || STATUS_FALLBACK_STYLE
}
