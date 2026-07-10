import { useEffect, useState } from "react"

export interface PaginationProps {
  /** Zero-based offset of the first item on the current page. */
  offset: number
  /** Page size. */
  limit: number
  /** Total item count across all pages. */
  total: number
  /** 1-based current page. */
  page: number
  /** Number of pages, always `>= 1`. */
  pageCount: number
  /** Step back one page. */
  onPrev: () => void
  /** Step forward one page. */
  onNext: () => void
  /** Jump to a page; the handler is expected to clamp to `[1, pageCount]`. */
  onGoToPage: (n: number) => void
}

/**
 * Shared offset-pagination controls — Previous / "{from}-{to} of {total}" / Next,
 * plus a numeric page-jump input. The Prev/range/Next markup is byte-for-byte
 * the Tailwind that OrphanDns and Events each hand-rolled; the jump input is the
 * one new affordance. Renders nothing when everything fits on a single page
 * (`total <= limit`), matching the old per-page `total > limit` guard.
 */
export function Pagination({
  offset,
  limit,
  total,
  page,
  pageCount,
  onPrev,
  onNext,
  onGoToPage,
}: PaginationProps) {
  // Local draft so the user can type freely; mirrors the live page on every
  // navigation (Prev/Next, a jump, or a shrink-clamp) so it never drifts.
  const [draft, setDraft] = useState(String(page))
  useEffect(() => {
    setDraft(String(page))
  }, [page])

  // Controls are pointless when the whole list fits on one page.
  if (total <= limit) return null

  const commit = () => {
    const n = Number.parseInt(draft, 10)
    if (Number.isNaN(n)) {
      // Non-numeric input is ignored: snap the box back to the current page.
      setDraft(String(page))
      return
    }
    // Clamp out-of-range jumps into [1, pageCount]. Reflect the clamped value
    // immediately so the box is correct even when the page doesn't actually
    // change (e.g. typing 999 while already on the last page).
    const clamped = Math.min(Math.max(1, n), pageCount)
    setDraft(String(clamped))
    onGoToPage(clamped)
  }

  return (
    <div className="mt-3 flex gap-2 items-center text-sm">
      <button
        type="button"
        disabled={offset === 0}
        onClick={onPrev}
        className="px-3 py-1 border rounded disabled:opacity-50"
      >
        Previous
      </button>
      <span className="text-zinc-500">
        {offset + 1}–{Math.min(offset + limit, total)} of {total}
      </span>
      <button
        type="button"
        disabled={offset + limit >= total}
        onClick={onNext}
        className="px-3 py-1 border rounded disabled:opacity-50"
      >
        Next
      </button>
      <input
        type="text"
        inputMode="numeric"
        aria-label="Go to page"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault()
            commit()
          }
        }}
        onBlur={commit}
        className="w-12 px-2 py-1 border rounded text-center"
      />
    </div>
  )
}
