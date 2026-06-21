import { formatDate, parseBackendDate } from "@/lib/useTimezone"

/**
 * Format a certificate expiry timestamp into display text plus a Tailwind color
 * class reflecting urgency (expired = red, within 14 days = yellow, else gray).
 *
 * Backend timestamps are naive UTC, so `parseBackendDate` normalizes them before
 * computing the day delta (a raw `new Date()` would misread them as local time).
 */
export function formatCertExpiry(
  iso: string | null | undefined,
  tz: string,
): { text: string; style: string } {
  if (!iso) return { text: "—", style: "text-zinc-400" }
  const expiry = parseBackendDate(iso)
  const now = new Date()
  const daysLeft = Math.ceil((expiry.getTime() - now.getTime()) / (1000 * 60 * 60 * 24))
  const text = formatDate(iso, tz)
  if (daysLeft < 0) return { text, style: "text-red-600 font-medium" }
  if (daysLeft <= 14) return { text, style: "text-yellow-600 font-medium" }
  return { text, style: "text-zinc-500" }
}
