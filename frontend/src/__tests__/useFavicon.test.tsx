import { describe, it, expect, beforeEach, afterEach, vi } from "vitest"
import { render, act } from "@testing-library/react"
import { setFavicon, useDynamicFavicon } from "@/lib/useFavicon"

function iconHref(): string | null {
  return document.querySelector("link[rel='icon']")?.getAttribute("href") ?? null
}

// Flush the (fetch -> json -> setFavicon) microtask chain.
async function flushMicrotasks() {
  await act(async () => {
    for (let i = 0; i < 6; i++) await Promise.resolve()
  })
}

describe("setFavicon", () => {
  beforeEach(() => {
    document.querySelectorAll("link[rel='icon']").forEach((el) => el.remove())
  })

  it("creates the icon link with the given (relative) href", () => {
    setFavicon("/favicon-healthy.svg")
    const link = document.querySelector("link[rel='icon']")
    expect(link).not.toBeNull()
    expect(link?.getAttribute("href")).toBe("/favicon-healthy.svg")
  })

  it("does not rewrite the href when it is unchanged", () => {
    setFavicon("/favicon-healthy.svg")
    const link = document.querySelector<HTMLLinkElement>("link[rel='icon']")!

    // Count href writes through BOTH the `link.href` property setter and
    // setAttribute, so this fails for the historical bug (which assigned
    // `link.href`) and any future regression. The guard compared the
    // DOM-resolved absolute `link.href` against the relative arg, so it never
    // matched and rewrote the favicon — re-fetching it — on every poll.
    let hrefWrites = 0
    let proto: object | null = HTMLLinkElement.prototype
    let desc: PropertyDescriptor | undefined
    while (proto && !(desc = Object.getOwnPropertyDescriptor(proto, "href"))) {
      proto = Object.getPrototypeOf(proto)
    }
    const get = desc?.get
    const set = desc?.set
    if (!get || !set) throw new Error("no href accessor on HTMLLinkElement")
    Object.defineProperty(link, "href", {
      configurable: true,
      get: () => get.call(link),
      set: (v) => {
        hrefWrites++
        set.call(link, v)
      },
    })
    const realSetAttribute = link.setAttribute.bind(link)
    vi.spyOn(link, "setAttribute").mockImplementation((name, value) => {
      if (name === "href") hrefWrites++
      realSetAttribute(name, value)
    })

    setFavicon("/favicon-healthy.svg")
    expect(hrefWrites).toBe(0)

    setFavicon("/favicon-error.svg")
    expect(hrefWrites).toBe(1)
    expect(iconHref()).toBe("/favicon-error.svg")
  })
})

describe("useDynamicFavicon", () => {
  function Probe({ interval = 1000 }: { interval?: number }) {
    useDynamicFavicon(interval)
    return null
  }

  beforeEach(() => {
    document.querySelectorAll("link[rel='icon']").forEach((el) => el.remove())
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it("reflects health from the summary endpoint", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ services: { error: 0 } }),
    }) as unknown as typeof fetch

    const { unmount } = render(<Probe />)
    await flushMicrotasks()
    expect(iconHref()).toBe("/favicon-healthy.svg")
    unmount()
  })

  it("switches to the error favicon when a service is in error", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ services: { error: 2 } }),
    }) as unknown as typeof fetch

    const { unmount } = render(<Probe />)
    await flushMicrotasks()
    expect(iconHref()).toBe("/favicon-error.svg")
    unmount()
  })

  it("stops polling after unmount (no leaked interval)", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ services: { error: 0 } }),
    })
    global.fetch = fetchMock as unknown as typeof fetch

    const { unmount } = render(<Probe interval={1000} />)
    await flushMicrotasks()
    const callsBeforeUnmount = fetchMock.mock.calls.length
    expect(callsBeforeUnmount).toBe(1)

    unmount()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })
    expect(fetchMock.mock.calls.length).toBe(callsBeforeUnmount)
  })

  it("clears interval id zero on unmount", async () => {
    const clearIntervalMock = vi.spyOn(globalThis, "clearInterval")
    vi.spyOn(globalThis, "setInterval").mockReturnValue(0 as unknown as ReturnType<typeof setInterval>)
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ services: { error: 0 } }),
    }) as unknown as typeof fetch

    const { unmount } = render(<Probe interval={1000} />)
    await flushMicrotasks()
    unmount()

    expect(clearIntervalMock).toHaveBeenCalledWith(0)
  })

  it("does not rewrite a static favicon from an in-flight request after unmount", async () => {
    let resolveFetch!: (value: Response) => void
    global.fetch = vi.fn().mockReturnValue(
      new Promise<Response>((resolve) => {
        resolveFetch = resolve
      })
    ) as unknown as typeof fetch

    const { unmount } = render(<Probe />)
    unmount()
    setFavicon("/favicon-healthy.svg")

    await act(async () => {
      resolveFetch({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ services: { error: 1 } }),
      } as Response)
    })
    await flushMicrotasks()

    expect(iconHref()).toBe("/favicon-healthy.svg")
  })

  it("stops polling permanently after a 401 response", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 401,
      json: () => Promise.resolve(null),
    })
    global.fetch = fetchMock as unknown as typeof fetch

    const { unmount } = render(<Probe interval={1000} />)
    await flushMicrotasks()
    const callsAfter401 = fetchMock.mock.calls.length
    expect(callsAfter401).toBe(1)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })
    expect(fetchMock.mock.calls.length).toBe(callsAfter401)
    unmount()
  })
})
