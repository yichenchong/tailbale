import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { PaginationBar } from "@/components/PaginationBar"
import type { UsePaginatedResourceResult } from "@/lib/usePaginatedResource"

/**
 * A minimal paginated-resource stand-in. Carries the real pagination slice
 * (offset/limit/total/page/pageCount — the only fields PaginationBar reads) plus
 * vi.fn() navigators so the wiring can be asserted, and inert stubs for the rest
 * of the useResource/usePagination surface so the cast is honest rather than a
 * bare `{} as ...`. PaginationBar's job is purely to ADAPT this onto <Pagination>.
 */
function makeResource(slice: {
  offset: number
  limit: number
  total: number
  page: number
  pageCount: number
}): UsePaginatedResourceResult<unknown, unknown> {
  return {
    ...slice,
    prev: vi.fn(),
    next: vi.fn(),
    goToPage: vi.fn(),
    setOffset: vi.fn(),
    setTotal: vi.fn(),
    clampToContent: vi.fn(),
    data: null,
    loading: false,
    error: null,
    refresh: vi.fn(),
    setData: vi.fn(),
    setError: vi.fn(),
    items: [],
  } as unknown as UsePaginatedResourceResult<unknown, unknown>
}

describe("PaginationBar", () => {
  it("adapts a multi-page resource onto the Pagination controls", () => {
    const resource = makeResource({ offset: 0, limit: 50, total: 100, page: 1, pageCount: 2 })
    render(<PaginationBar resource={resource} />)
    expect(screen.getByText("Previous")).toBeInTheDocument()
    expect(screen.getByText("Next")).toBeInTheDocument()
    expect(screen.getByText("1–50 of 100")).toBeInTheDocument()
    expect(screen.getByText("Previous")).toBeDisabled() // offset 0 -> first page
  })

  it("renders nothing when the whole list fits on one page", () => {
    const resource = makeResource({ offset: 0, limit: 50, total: 3, page: 1, pageCount: 1 })
    render(<PaginationBar resource={resource} />)
    expect(screen.queryByText("Previous")).not.toBeInTheDocument()
    expect(screen.queryByText("Next")).not.toBeInTheDocument()
  })

  it("routes the Next button to the resource's next()", () => {
    const resource = makeResource({ offset: 0, limit: 50, total: 100, page: 1, pageCount: 2 })
    render(<PaginationBar resource={resource} />)
    fireEvent.click(screen.getByText("Next"))
    expect(resource.next).toHaveBeenCalledTimes(1)
  })

  it("routes a page jump to the resource's goToPage()", () => {
    const resource = makeResource({ offset: 0, limit: 50, total: 100, page: 1, pageCount: 2 })
    render(<PaginationBar resource={resource} />)
    const input = screen.getByLabelText("Go to page")
    fireEvent.change(input, { target: { value: "2" } })
    fireEvent.keyDown(input, { key: "Enter" })
    expect(resource.goToPage).toHaveBeenCalledWith(2)
  })
})
