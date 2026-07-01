import { describe, it, expect, vi } from "vitest"
import { act, render, renderHook, screen, fireEvent } from "@testing-library/react"
import { usePagination } from "@/lib/usePagination"
import { Pagination } from "@/components/Pagination"

describe("usePagination", () => {
  it("starts on page 1 at offset 0 with a single empty page", () => {
    const { result } = renderHook(() => usePagination())
    expect(result.current.offset).toBe(0)
    expect(result.current.limit).toBe(50)
    expect(result.current.page).toBe(1)
    expect(result.current.pageCount).toBe(1)
  })

  it("derives page/pageCount from the synced total", () => {
    const { result } = renderHook(() => usePagination())
    act(() => result.current.setTotal(120))
    expect(result.current.pageCount).toBe(3) // ceil(120 / 50)
    act(() => result.current.next())
    expect(result.current.offset).toBe(50)
    expect(result.current.page).toBe(2)
    act(() => result.current.prev())
    expect(result.current.offset).toBe(0)
    expect(result.current.page).toBe(1)
  })

  it("next clamps at the last page and never overshoots the final record", () => {
    // Regression: an unbounded `next` (o => o + limit) lets a programmatic call
    // — or a double-click landing before the disabled Next button re-renders —
    // push offset past the last record, rendering an out-of-range page over an
    // empty result. Symmetric with `prev`'s floor at 0.
    const { result } = renderHook(() => usePagination())
    act(() => result.current.setTotal(120)) // 3 pages, last offset 100
    act(() => result.current.next())
    expect(result.current.offset).toBe(50)
    act(() => result.current.next())
    expect(result.current.offset).toBe(100) // on the last page now
    act(() => result.current.next()) // would overshoot to 150 unbounded
    expect(result.current.offset).toBe(100)
    expect(result.current.page).toBe(3)
    expect(result.current.page).toBeLessThanOrEqual(result.current.pageCount)
  })

  it("next is a no-op while total is still zero (offset stays at 0)", () => {
    const { result } = renderHook(() => usePagination())
    act(() => result.current.next())
    expect(result.current.offset).toBe(0)
  })

  it("honors a custom limit", () => {
    const { result } = renderHook(() => usePagination({ limit: 20 }))
    act(() => result.current.setTotal(45))
    expect(result.current.limit).toBe(20)
    expect(result.current.pageCount).toBe(3) // ceil(45 / 20)
  })

  it("goToPage clamps out-of-range input into [1, pageCount]", () => {
    const { result } = renderHook(() => usePagination())
    act(() => result.current.setTotal(120)) // pageCount 3

    act(() => result.current.goToPage(2))
    expect(result.current.offset).toBe(50)
    expect(result.current.page).toBe(2)

    // Past the end clamps to the last page.
    act(() => result.current.goToPage(999))
    expect(result.current.offset).toBe(100)
    expect(result.current.page).toBe(3)

    // Below the start clamps to page 1.
    act(() => result.current.goToPage(0))
    expect(result.current.offset).toBe(0)
    act(() => result.current.goToPage(-5))
    expect(result.current.offset).toBe(0)

    // Fractional input floors to a whole page.
    act(() => result.current.goToPage(2.9))
    expect(result.current.offset).toBe(50)

    // Non-finite input is ignored (stays put).
    act(() => result.current.goToPage(NaN))
    expect(result.current.offset).toBe(50)
  })

  it("clampToContent corrects an offset that fell off the end after a shrink", () => {
    const { result } = renderHook(() => usePagination())
    act(() => result.current.setTotal(51))
    act(() => result.current.setOffset(50)) // page 2

    // Page 2 came back empty and total shrank to 50 -> clamp back to offset 0.
    expect(result.current.clampToContent(50, 0)).toBe(0)
    // Page still has rows -> no clamp.
    expect(result.current.clampToContent(51, 1)).toBeNull()
    // total is 0 -> nothing to clamp to.
    expect(result.current.clampToContent(0, 0)).toBeNull()
  })

  it("clampToContent does not clamp from the first page", () => {
    const { result } = renderHook(() => usePagination())
    act(() => result.current.setTotal(0))
    // Empty first page (offset 0) is the legitimate empty state, not a shrink.
    expect(result.current.clampToContent(0, 0)).toBeNull()
  })
})

describe("Pagination component", () => {
  const defaults = {
    offset: 0,
    limit: 50,
    total: 120,
    page: 1,
    pageCount: 3,
    onPrev: () => {},
    onNext: () => {},
    onGoToPage: () => {},
  }

  it("renders nothing when everything fits on one page", () => {
    const { container } = render(<Pagination {...defaults} total={3} />)
    expect(container).toBeEmptyDOMElement()
  })

  it("renders the Prev / range / Next controls", () => {
    render(<Pagination {...defaults} />)
    expect(screen.getByText("Previous")).toBeDisabled() // first page
    expect(screen.getByText("Next")).not.toBeDisabled()
    expect(screen.getByText("1–50 of 120")).toBeInTheDocument()
  })

  it("disables Next on the last page", () => {
    render(<Pagination {...defaults} offset={100} page={3} />)
    expect(screen.getByText("Next")).toBeDisabled()
    expect(screen.getByText("Previous")).not.toBeDisabled()
    expect(screen.getByText("101–120 of 120")).toBeInTheDocument()
  })

  it("jumps to the typed page on Enter", () => {
    const onGoToPage = vi.fn()
    render(<Pagination {...defaults} onGoToPage={onGoToPage} />)
    const input = screen.getByLabelText("Go to page")
    fireEvent.change(input, { target: { value: "2" } })
    fireEvent.keyDown(input, { key: "Enter" })
    expect(onGoToPage).toHaveBeenCalledWith(2)
  })

  it("jumps to the typed page on blur", () => {
    const onGoToPage = vi.fn()
    render(<Pagination {...defaults} onGoToPage={onGoToPage} />)
    const input = screen.getByLabelText("Go to page")
    fireEvent.change(input, { target: { value: "3" } })
    fireEvent.blur(input)
    expect(onGoToPage).toHaveBeenCalledWith(3)
  })

  it("clamps an out-of-range jump and shows the clamped page", () => {
    const onGoToPage = vi.fn()
    render(<Pagination {...defaults} onGoToPage={onGoToPage} />)
    const input = screen.getByLabelText("Go to page")
    fireEvent.change(input, { target: { value: "999" } })
    fireEvent.keyDown(input, { key: "Enter" })
    expect(onGoToPage).toHaveBeenCalledWith(3) // clamped to pageCount
    expect(input).toHaveValue("3")
  })

  it("ignores a non-numeric jump and reverts to the current page", () => {
    const onGoToPage = vi.fn()
    render(<Pagination {...defaults} page={2} offset={50} onGoToPage={onGoToPage} />)
    const input = screen.getByLabelText("Go to page")
    expect(input).toHaveValue("2")
    fireEvent.change(input, { target: { value: "abc" } })
    fireEvent.keyDown(input, { key: "Enter" })
    expect(onGoToPage).not.toHaveBeenCalled()
    expect(input).toHaveValue("2")
  })

  it("mirrors the live page when navigation changes it", () => {
    const { rerender } = render(<Pagination {...defaults} page={1} offset={0} />)
    expect(screen.getByLabelText("Go to page")).toHaveValue("1")
    rerender(<Pagination {...defaults} page={2} offset={50} />)
    expect(screen.getByLabelText("Go to page")).toHaveValue("2")
  })
})
