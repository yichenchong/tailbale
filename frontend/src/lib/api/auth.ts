import { get, post } from "./core"

export interface AuthStatus {
  setup_complete: boolean
  authenticated: boolean
}

export interface AuthUser {
  id: string
  username: string
  display_name: string | null
  role: string
}

export interface LoginResponse {
  user: AuthUser
}

export interface SetupProgress {
  user_exists: boolean
  base_domain_set: boolean
  cloudflare_configured: boolean
  cloudflare_token_set?: boolean
  acme_email_set: boolean
  tailscale_configured: boolean
  docker_configured: boolean
}

export interface CredentialsRequest {
  username: string
  password: string
}

export interface ChangePasswordRequest {
  current_password: string
  new_password: string
}

export const authApi = {
  status: () => get<AuthStatus>("/auth/status"),
  setupProgress: () => get<SetupProgress>("/auth/setup-progress"),
  login: (body: CredentialsRequest) => post<LoginResponse>("/auth/login", body),
  setupUser: (body: CredentialsRequest) => post<LoginResponse>("/auth/setup-user", body),
  changePassword: (body: ChangePasswordRequest) => post<void>("/auth/change-password", body),
}
