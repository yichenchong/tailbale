import { get } from "./core"

export interface EventItem {
  id: string
  service_id: string | null
  kind: string
  level: string
  message: string
  details: Record<string, unknown> | null
  created_at: string | null
}

export interface EventsResponse {
  events: EventItem[]
  total: number
}

/** GET /events/kinds — the canonical registry the backend emits, sorted. */
export interface EventKindsResponse {
  kinds: string[]
}

export interface EventsQuery {
  search?: string
  level?: string
  kind?: string
  limit: number
  offset: number
}

export const eventsApi = {
  list: (params: EventsQuery) => {
    const qs = new URLSearchParams()
    if (params.search) qs.set("search", params.search)
    if (params.level) qs.set("level", params.level)
    if (params.kind) qs.set("kind", params.kind)
    qs.set("limit", String(params.limit))
    qs.set("offset", String(params.offset))
    const s = qs.toString()
    return get<EventsResponse>(`/events${s ? `?${s}` : ""}`)
  },
  kinds: () => get<EventKindsResponse>("/events/kinds"),
}
