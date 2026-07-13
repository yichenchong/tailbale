import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { AdditionalNetworksEditor } from "@/components/service/AdditionalNetworksEditor"

describe("AdditionalNetworksEditor duplicate feedback", () => {
  it("flags duplicate network names inline on every offending row", () => {
    render(
      <AdditionalNetworksEditor
        value={[
          { name: "net_a", aliases: ["a.example.com"] },
          { name: "net_a", aliases: ["b.example.com"] },
        ]}
        onChange={vi.fn()}
      />,
    )
    // Both rows carry the same name, so both must explain the disabled Save.
    expect(screen.getAllByText("This Docker network is already listed above.")).toHaveLength(2)
  })

  it("flags duplicate aliases within a network inline", () => {
    render(
      <AdditionalNetworksEditor
        value={[{ name: "net_a", aliases: ["a.example.com", "a.example.com"] }]}
        onChange={vi.fn()}
      />,
    )
    expect(screen.getByText("Aliases must be unique within a network.")).toBeInTheDocument()
  })

  it("shows no duplicate error for distinct rows and aliases", () => {
    render(
      <AdditionalNetworksEditor
        value={[
          { name: "net_a", aliases: ["a.example.com"] },
          { name: "net_b", aliases: ["b.example.com"] },
        ]}
        onChange={vi.fn()}
      />,
    )
    expect(screen.queryByText("This Docker network is already listed above.")).not.toBeInTheDocument()
    expect(screen.queryByText("Aliases must be unique within a network.")).not.toBeInTheDocument()
  })
})
