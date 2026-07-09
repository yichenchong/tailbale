import { get } from "./core"

export interface ContainerPort {
  container_port: string
  host_port: string | null
  protocol: string
}

export interface DiscoveredContainer {
  id: string
  name: string
  image: string
  status: string
  state: string
  ports: ContainerPort[]
  networks: string[]
  labels: Record<string, string>
}

export interface DiscoveryResponse {
  containers: DiscoveredContainer[]
  total: number
}

export interface DiscoveryQuery {
  runningOnly: boolean
  search?: string
}

export const discoveryApi = {
  containers: (params: DiscoveryQuery) => {
    const qs = new URLSearchParams({
      running_only: String(params.runningOnly),
      hide_managed: "true",
      ...(params.search ? { search: params.search } : {}),
    })
    return get<DiscoveryResponse>(`/discovery/containers?${qs}`)
  },
}
