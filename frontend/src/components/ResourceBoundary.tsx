import type { ReactNode } from "react"

export interface ResourceBoundaryProps {
  /** Show the {@link loadingSlot} (highest precedence). */
  loading?: boolean
  /** Show the {@link errorSlot} — an initial-load error over an as-yet-empty view. */
  errored?: boolean
  /** Show the {@link emptySlot} — no rows and no blocking error. */
  empty?: boolean
  /** Rendered while `loading`. */
  loadingSlot?: ReactNode
  /** Rendered while `errored`. */
  errorSlot?: ReactNode
  /** Rendered while `empty`. */
  emptySlot?: ReactNode
  /**
   * Non-blocking banner rendered directly above {@link children} once rows are
   * on screen — e.g. a failed background refresh whose error must be announced
   * without blanking the still-good list.
   */
  refreshErrorSlot?: ReactNode
  /** The loaded rows. */
  children: ReactNode
}

/**
 * The loading → initial-error → empty → rows switch every list page re-derived
 * by hand. It renders exactly one slot in that fixed precedence and adds no DOM
 * of its own (a Fragment), so each page keeps its own markup verbatim while the
 * ordering invariant lives in one place. `refreshErrorSlot` layers a
 * non-blocking error banner above the rows for the "refresh failed but the list
 * is still up" case.
 */
export function ResourceBoundary({
  loading = false,
  errored = false,
  empty = false,
  loadingSlot,
  errorSlot,
  emptySlot,
  refreshErrorSlot,
  children,
}: ResourceBoundaryProps) {
  if (loading) return <>{loadingSlot}</>
  if (errored) return <>{errorSlot}</>
  if (empty) return <>{emptySlot}</>
  return (
    <>
      {refreshErrorSlot}
      {children}
    </>
  )
}
