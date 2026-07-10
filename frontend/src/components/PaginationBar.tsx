import { Pagination } from "@/components/Pagination"
import type { UsePaginatedResourceResult } from "@/lib/usePaginatedResource"

export interface PaginationBarProps<TData, TItem> {
  /** The whole {@link usePaginatedResource} result; only its pagination slice is read. */
  resource: UsePaginatedResourceResult<TData, TItem>
}

/**
 * Adapts a {@link usePaginatedResource} result onto the shared {@link Pagination}
 * controls, so a list page needs one `<PaginationBar resource={...} />` instead
 * of re-spreading the nine offset/limit/page/pageCount/prev/next/goToPage props
 * at every call site. Renders nothing when everything fits on one page (that
 * guard lives in {@link Pagination}).
 */
export function PaginationBar<TData, TItem>({ resource }: PaginationBarProps<TData, TItem>) {
  return (
    <Pagination
      offset={resource.offset}
      limit={resource.limit}
      total={resource.total}
      page={resource.page}
      pageCount={resource.pageCount}
      onPrev={resource.prev}
      onNext={resource.next}
      onGoToPage={resource.goToPage}
    />
  )
}
