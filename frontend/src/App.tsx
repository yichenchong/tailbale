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

  // Update favicon based on service health (polls every 30s)
  useDynamicFavicon(30_000)

  // Still loading
  if (setupComplete === null || authenticated === null) return null

  return (
    <BrowserRouter>
      <Routes>
        {/* Setup wizard — redirect to dashboard if already completed */}
        <Route
          path="setup"
          element={setupComplete ? <Navigate to="/" replace /> : <Setup />}
        />
        <Route path="login" element={<Login />} />

        <Route element={<Layout />}>
          <Route
            index
            element={
              !setupComplete ? (
                <Navigate to="/setup" replace />
              ) : !authenticated ? (
                <Navigate to="/login" replace />
              ) : (
                <Dashboard />
              )
            }
          />
          {!setupComplete ? (
            <Route path="*" element={<Navigate to="/setup" replace />} />
          ) : !authenticated ? (
            <Route path="*" element={<Navigate to="/login" replace />} />
          ) : (
            <>
              <Route path="services" element={<Services />} />
              <Route path="services/:id" element={<ServiceDetail />} />
              <Route path="discover" element={<Discover />} />
              <Route path="expose" element={<ExposeService />} />
              <Route path="events" element={<Events />} />
              <Route path="orphan-dns" element={<OrphanDns />} />
              <Route path="settings" element={<SettingsPage />} />
            </>
          )}
        </Route>
      </Routes>
    </BrowserRouter>
  )
}

export default App
