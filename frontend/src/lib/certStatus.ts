import { formatDate, parseBackendDate } from "@/lib/useTimezone"

/** Urgency bucket for a certificate expiry, in increasing severity. */
export type CertUrgency = "none" | "ok" | "soon" | "expired"

export interface CertStatus {
  /** Whole days until expiry (ceil); null when the timestamp is missing/unparseable. */
  daysLeft: number | null
  /** True once the expiry is in the past (keyed off the raw sign, see below). */
  expired: boolean
  urgency: CertUrgency
  /** Tailwind text-color class reflecting urgency (no font weight). */
  color: string
  /** Compact label: "Expired" / "Nd left" / em-dash sentinel for missing dates. */
  label: string
}

/** Yellow "expiring soon" threshold, in days, shared by every cert UI surface. */
export const CERT_SOON_DAYS = 14

const URGENCY_COLOR: Record<CertUrgency, string> = {
  none: "text-zinc-400",
  ok: "text-zinc-500",
  soon: "text-yellow-600",
  expired: "text-red-600",
}

/**
 * Single source of truth for certificate-expiry urgency: parses the backend
 * timestamp, computes the day delta, and classifies it (expired = red, within
 * {@link CERT_SOON_DAYS} = yellow, else gray; missing/unparseable = sentinel).
 *
 * Backend timestamps are naive UTC, so `parseBackendDate` normalizes them before
 * computing the day delta (a raw `new Date()` would misread them as local time).
 */
export function certStatus(iso: string | null | undefined): CertStatus {
  if (!iso) {
    return { daysLeft: null, expired: false, urgency: "none", color: URGENCY_COLOR.none, label: "—" }
  }
  const ms = parseBackendDate(iso).getTime()
  // An unparseable timestamp yields NaN; treat it as missing (sentinel) instead
  // of letting NaN comparisons fall through to a misleading gray "valid" state.
  if (Number.isNaN(ms)) {
    return { daysLeft: null, expired: false, urgency: "none", color: URGENCY_COLOR.none, label: "—" }
  }
  const diffMs = ms - Date.now()
  const daysLeft = Math.ceil(diffMs / (1000 * 60 * 60 * 24))
  // Gate "expired" on the raw sign: Math.ceil collapses the first 24h after
  // expiry to 0, which would otherwise render an already-expired cert as merely
  // "expiring soon" (yellow) instead of expired (red).
  const expired = diffMs < 0
  const urgency: CertUrgency = expired ? "expired" : daysLeft <= CERT_SOON_DAYS ? "soon" : "ok"
  return {
    daysLeft,
    expired,
    urgency,
    color: URGENCY_COLOR[urgency],
    label: expired ? "Expired" : `${daysLeft}d left`,
  }
}

/**
 * Format a certificate expiry timestamp into display text (the localized date)
 * plus a Tailwind color class reflecting urgency. Thin wrapper over
 * {@link certStatus} so the threshold and color mapping live in one place.
 */
export function formatCertExpiry(
  iso: string | null | undefined,
  tz: string,
): { text: string; style: string } {
  const status = certStatus(iso)
  // `certStatus` already collapses BOTH a missing and an unparseable `iso` to
  // urgency "none", so that check alone is the single, sufficient sentinel gate.
  if (status.urgency === "none") return { text: "—", style: status.color }
  // Urgent states (expired/soon) are emphasized; a comfortably-valid cert is not.
  const weight = status.urgency === "ok" ? "" : " font-medium"
  return { text: formatDate(iso, tz), style: `${status.color}${weight}` }
}
