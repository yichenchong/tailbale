import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom"
import { useEffect, useState } from "react"
import { Layout } from "@/components/Layout"
import Dashboard from "@/pages/Dashboard"
import Services from "@/pages/Services"
import ServiceDetail from "@/pages/ServiceDetail"
import Discover from "@/pages/Discover"
import ExposeService from "@/pages/ExposeService"
import Events from "@/pages/Events"
import OrphanDns from "@/pages/OrphanDns"
import SettingsPage from "@/pages/SettingsPage"
import Setup from "@/pages/Setup"
import Login from "@/pages/Login"
import { api, type AuthStatus } from "@/lib/api"
import { useDynamicFavicon } from "@/lib/useFavicon"

/** Wrapper that enables favicon polling only for authenticated routes. */
function AuthenticatedLayout() {
  useDynamicFavicon(30_000)
  return <Layout />
}

function App() {
  const [setupComplete, setSetupComplete] = useState<boolean | null>(null)
  const [authenticated, setAuthenticated] = useState<boolean | null>(null)

  useEffect(() => {
    api
      .get<AuthStatus>("/auth/status")
      .then((s) => {
        setSetupComplete(s.setup_complete)
        setAuthenticated(s.authenticated)
      })
      .catch(() => {
        setSetupComplete(true)
        setAuthenticated(false)
      })
  }, [])

  // Still loading
  if (setupComplete === null || authenticated === null) return null

  // Determine where unauthenticated users should go
  const redirect = !setupComplete ? "/setup" : !authenticated ? "/login" : null

  return (
    <BrowserRouter>
      <Routes>
        {/* Setup wizard — redirect to dashboard if already completed */}
        <Route
          path="setup"
          element={setupComplete ? <Navigate to="/" replace /> : <Setup />}
        />
        <Route
          path="login"
          element={authenticated ? <Navigate to="/" replace /> : <Login />}
        />

        {redirect ? (
          /* Not authenticated or not set up — redirect everything to login/setup.
             No Layout renders, so no Sidebar, no favicon polling. */
          <Route path="*" element={<Navigate to={redirect} replace />} />
        ) : (
          /* Authenticated — render full app with Layout + favicon polling */
          <Route element={<AuthenticatedLayout />}>
            <Route index element={<Dashboard />} />
            <Route path="services" element={<Services />} />
            <Route path="services/:id" element={<ServiceDetail />} />
            <Route path="discover" element={<Discover />} />
            <Route path="expose" element={<ExposeService />} />
            <Route path="events" element={<Events />} />
            <Route path="orphan-dns" element={<OrphanDns />} />
            <Route path="settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        )}
      </Routes>
    </BrowserRouter>
  )
}

export default App
