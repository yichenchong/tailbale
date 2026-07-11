import { get } from "./core"

export interface VersionResponse {
  version: string
}

export const metaApi = {
  version: () => get<VersionResponse>("/version"),
}
