import { describe, it, expect } from "vitest"
import { render, screen } from "@testing-library/react"
import { ResourceBoundary } from "@/components/ResourceBoundary"

const slots = {
  loadingSlot: <div>LOADING</div>,
  errorSlot: <div>ERROR</div>,
  emptySlot: <div>EMPTY</div>,
}

describe("ResourceBoundary", () => {
  it("loading wins over errored and empty", () => {
    render(
      <ResourceBoundary {...slots} loading errored empty>
        <div>ROWS</div>
      </ResourceBoundary>,
    )
    expect(screen.getByText("LOADING")).toBeInTheDocument()
    expect(screen.queryByText("ERROR")).not.toBeInTheDocument()
    expect(screen.queryByText("EMPTY")).not.toBeInTheDocument()
    expect(screen.queryByText("ROWS")).not.toBeInTheDocument()
  })

  it("errored wins over empty when not loading", () => {
    render(
      <ResourceBoundary {...slots} errored empty>
        <div>ROWS</div>
      </ResourceBoundary>,
    )
    expect(screen.getByText("ERROR")).toBeInTheDocument()
    expect(screen.queryByText("LOADING")).not.toBeInTheDocument()
    expect(screen.queryByText("EMPTY")).not.toBeInTheDocument()
    expect(screen.queryByText("ROWS")).not.toBeInTheDocument()
  })

  it("empty shows the empty slot when it is the only flag set", () => {
    render(
      <ResourceBoundary {...slots} empty>
        <div>ROWS</div>
      </ResourceBoundary>,
    )
    expect(screen.getByText("EMPTY")).toBeInTheDocument()
    expect(screen.queryByText("LOADING")).not.toBeInTheDocument()
    expect(screen.queryByText("ERROR")).not.toBeInTheDocument()
    expect(screen.queryByText("ROWS")).not.toBeInTheDocument()
  })

  it("renders children when no flag is set", () => {
    render(
      <ResourceBoundary {...slots}>
        <div>ROWS</div>
      </ResourceBoundary>,
    )
    expect(screen.getByText("ROWS")).toBeInTheDocument()
    expect(screen.queryByText("LOADING")).not.toBeInTheDocument()
    expect(screen.queryByText("ERROR")).not.toBeInTheDocument()
    expect(screen.queryByText("EMPTY")).not.toBeInTheDocument()
  })

  it("rows branch renders refreshErrorSlot above the children", () => {
    render(
      <ResourceBoundary refreshErrorSlot={<div>REFRESH</div>}>
        <div>ROWS</div>
      </ResourceBoundary>,
    )
    const refresh = screen.getByText("REFRESH")
    const rows = screen.getByText("ROWS")
    expect(refresh).toBeInTheDocument()
    expect(rows).toBeInTheDocument()
    // REFRESH precedes ROWS in document order.
    expect(
      refresh.compareDocumentPosition(rows) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
  })

  it("does not render refreshErrorSlot in the loading branch", () => {
    render(
      <ResourceBoundary loading loadingSlot={<div>LOADING</div>} refreshErrorSlot={<div>REFRESH</div>}>
        <div>ROWS</div>
      </ResourceBoundary>,
    )
    expect(screen.getByText("LOADING")).toBeInTheDocument()
    expect(screen.queryByText("REFRESH")).not.toBeInTheDocument()
    expect(screen.queryByText("ROWS")).not.toBeInTheDocument()
  })

  it("adds no wrapper DOM of its own", () => {
    render(
      <div data-testid="parent">
        <ResourceBoundary>
          <span data-testid="child" />
        </ResourceBoundary>
      </div>,
    )
    const parent = screen.getByTestId("parent")
    const child = screen.getByTestId("child")
    // The Fragment contributes no element, so the span is a direct child.
    expect(child.parentElement).toBe(parent)
  })
})
