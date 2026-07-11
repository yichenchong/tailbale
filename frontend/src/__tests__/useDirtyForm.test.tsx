import { describe, it, expect, vi } from "vitest"
import { act, renderHook } from "@testing-library/react"
import { useDirtyForm } from "@/lib/useDirtyForm"

/**
 * CONTRACT TESTS for the per-field dirty-tracking form machine extracted from
 * the SettingsPage tabs. Exercises the three behaviors the hook exists to own:
 *   - an `edited` set that gates prop-sync so a background refresh never clobbers
 *     a field mid-edit while still adopting untouched server changes,
 *   - `save(run)` clearing the marks BEFORE awaiting so the save's response
 *     (server-normalized values) is freely adopted, and
 *   - restore-on-throw so a failed save keeps the user's input for a retry.
 *
 * The `settings` object identity is what triggers a fresh sync (the page mints a
 * new object on each load/save), so tests drive it via `rerender`.
 */

interface Settings {
  domain: string
  interval: number
}

// extract mirrors GeneralTab: numeric field surfaced as a String for a text input.
const extract = (s: Settings) => ({ domain: s.domain, interval: String(s.interval) })

function setup(initial: Settings) {
  return renderHook(({ settings }) => useDirtyForm(settings, extract), {
    initialProps: { settings: initial },
  })
}

describe("useDirtyForm", () => {
  it("seeds values from extract(settings)", () => {
    const { result } = setup({ domain: "a.com", interval: 60 })
    expect(result.current.values).toEqual({ domain: "a.com", interval: "60" })
  })

  it("set records the value and bind produces a working onChange handler", () => {
    const { result } = setup({ domain: "a.com", interval: 60 })

    act(() => result.current.set("domain", "b.com"))
    expect(result.current.values.domain).toBe("b.com")

    act(() => result.current.bind("interval")("90"))
    expect(result.current.values.interval).toBe("90")
  })

  it("adopts an incoming server change for a field the user has NOT edited", () => {
    const { result, rerender } = setup({ domain: "a.com", interval: 60 })
    // A background refresh delivers a new settings object with a changed field.
    rerender({ settings: { domain: "changed.com", interval: 60 } })
    expect(result.current.values.domain).toBe("changed.com")
  })

  it("does NOT clobber a field the user is mid-editing when settings refresh", () => {
    const { result, rerender } = setup({ domain: "a.com", interval: 60 })
    act(() => result.current.set("domain", "user-typed.com"))

    // Background refresh brings a server value for the SAME field — must be ignored
    // for the edited field while still adopting the untouched `interval` change.
    rerender({ settings: { domain: "server.com", interval: 120 } })
    expect(result.current.values.domain).toBe("user-typed.com")
    expect(result.current.values.interval).toBe("120")
  })

  it("marks a field edited even when re-set to its current value (guards a later sync)", () => {
    // set() adds to the edited set unconditionally; re-typing the same value still
    // pins the field so a subsequent server change for it is not adopted. Without
    // this, a user who re-typed the identical value would see it silently replaced.
    const { result, rerender } = setup({ domain: "a.com", interval: 60 })
    act(() => result.current.set("domain", "a.com")) // same value, still an edit
    rerender({ settings: { domain: "server.com", interval: 60 } })
    expect(result.current.values.domain).toBe("a.com")
  })

  it("save clears the edited marks BEFORE the response, so the save's server value is adopted", async () => {
    const { result, rerender } = setup({ domain: "a.com", interval: 60 })
    act(() => result.current.set("domain", "user.com"))

    // Simulate the real page: run() PUTs, then the page mints a new settings
    // object from the (normalized) response. save() must have cleared the marks
    // first so this post-save sync adopts the server-normalized value.
    await act(async () => {
      await result.current.save(async () => {
        rerender({ settings: { domain: "normalized.com", interval: 60 } })
      })
    })
    expect(result.current.values.domain).toBe("normalized.com")
  })

  it("restores the edited marks and rethrows when the save throws, keeping the user's input for retry", async () => {
    const { result, rerender } = setup({ domain: "a.com", interval: 60 })
    act(() => result.current.set("domain", "user.com"))

    const boom = new Error("save failed")
    await expect(
      act(async () => {
        await result.current.save(async () => {
          throw boom
        })
      }),
    ).rejects.toBe(boom)

    // The mark was restored on throw: a later background refresh must still not
    // clobber the field the user is about to retry.
    rerender({ settings: { domain: "server.com", interval: 60 } })
    expect(result.current.values.domain).toBe("user.com")
  })

  it("a successful save re-arms sync: a later server change to a now-unedited field is adopted", async () => {
    const { result, rerender } = setup({ domain: "a.com", interval: 60 })
    act(() => result.current.set("domain", "user.com"))

    await act(async () => {
      await result.current.save(async () => {
        rerender({ settings: { domain: "user.com", interval: 60 } })
      })
    })
    // Marks cleared by the successful save → a subsequent refresh is now free to
    // adopt a server change to `domain` (the field is no longer considered edited).
    rerender({ settings: { domain: "admin-changed.com", interval: 60 } })
    expect(result.current.values.domain).toBe("admin-changed.com")
  })

  it("save invokes run exactly once and resolves when it succeeds", async () => {
    const { result } = setup({ domain: "a.com", interval: 60 })
    const run = vi.fn().mockResolvedValue(undefined)
    await act(async () => {
      await result.current.save(run)
    })
    expect(run).toHaveBeenCalledTimes(1)
  })
})
