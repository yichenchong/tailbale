import { get } from "./core"

export interface AppProfile {
  name: string
  recommended_port: number
  healthcheck_path: string | null
  preserve_host_header: boolean
  post_setup_reminder: string | null
  image_patterns: string[]
}

export interface ProfileDetectResponse {
  detected_profile: string | null
  profile: AppProfile | null
}

export const profilesApi = {
  detect: (image: string) =>
    get<ProfileDetectResponse>(`/profiles/detect?image=${encodeURIComponent(image)}`),
}
