import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { mockSettings } from "./settingsTestUtils"

beforeEach(() => {
  vi.restoreAllMocks()
})

describe("SettingsPathsTab dirty-state guards", () => {
  const paths = mockSettings.paths

  it("guards edited Paths fields on refresh while untouched ones update (PathsTab)", async () => {
    const { PathsTab } = await import("@/pages/settings/PathsTab")
    const onSave = vi.fn().mockResolvedValue(undefined)
    const { rerender } = render(
      <PathsTab settings={paths} onSave={onSave} saving={false} />
    )

    fireEvent.change(screen.getByDisplayValue("data/generated"), { target: { value: "custom/generated" } })

    rerender(
      <PathsTab settings={{ ...paths, generated_root: "srv/generated", cert_root: "srv/certs" }} onSave={onSave} saving={false} />
    )

    expect(screen.getByDisplayValue("custom/generated")).toBeInTheDocument()
    expect(screen.getByDisplayValue("srv/certs")).toBeInTheDocument()
    expect(screen.queryByDisplayValue("srv/generated")).not.toBeInTheDocument()
  })
})
