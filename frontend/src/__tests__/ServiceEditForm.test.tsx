import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { ServiceEditForm } from "@/components/service/ServiceEditForm"
import { type ServiceEditState } from "@/lib/serviceTypes"
import { makeService } from "./factories"
import type { ServiceItem } from "@/lib/api"

function makeEditState(overrides: Partial<ServiceEditState> = {}): ServiceEditState {
  const name = overrides.name ?? "cloud"
  return {
    editing: true,
    setEditing: vi.fn(),
    name,
    setName: vi.fn(),
    port: "443",
    setPort: vi.fn(),
    scheme: "http",
    setScheme: vi.fn(),
    healthcheck: "",
    setHealthcheck: vi.fn(),
    preserveHost: true,
    setPreserveHost: vi.fn(),
    snippet: "",
    setSnippet: vi.fn(),
    additionalNetworks: [],
    setAdditionalNetworks: vi.fn(),
    normalizedName: name.trim(),
    nameValid: true,
    portValid: true,
    additionalNetworksValid: true,
    reset: vi.fn(),
    ...overrides,
  }
}

function renderForm(edit: ServiceEditState) {
  return render(
    <ServiceEditForm
      service={makeService() as unknown as ServiceItem}
      id="svc_abc123"
      edit={edit}
      applyServiceUpdate={vi.fn()}
      setError={vi.fn()}
    />,
  )
}

describe("ServiceEditForm name-field a11y", () => {
  it("associates the length error with the Name input via aria-invalid + aria-describedby", () => {
    const longName = "a".repeat(129)
    renderForm(makeEditState({ name: longName, normalizedName: longName, nameValid: false }))

    const name = screen.getByLabelText("Name")
    expect(name).toHaveAttribute("aria-invalid", "true")
    const describedBy = name.getAttribute("aria-describedby")
    expect(describedBy).toBeTruthy()
    // The referenced node must be the visible error message so a screen reader
    // announces WHY the field is invalid (WCAG 3.3.1 / 4.1.3).
    const errorNode = document.getElementById(describedBy!)
    expect(errorNode).toHaveTextContent("Service name must be 128 characters or fewer")
  })

  it("marks a valid Name input as neither invalid nor described-by an error", () => {
    renderForm(makeEditState({ name: "cloud", nameValid: true }))
    const name = screen.getByLabelText("Name")
    expect(name).not.toHaveAttribute("aria-invalid")
    expect(name).not.toHaveAttribute("aria-describedby")
  })
})
