import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom"
import { useEffect, useState, useCallback } from "react"
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
import { api } from "@/lib/api"
import { useDynamicFavicon } from "@/lib/useFavicon"
import { errorMessage } from "@/lib/utils"

/** Wrapper that enables favicon polling only for authenticated routes. */
function AuthenticatedLayout() {
  useDynamicFavicon(30_000)
  return <Layout />
}

function App() {
  const [setupComplete, setSetupComplete] = useState<boolean | null>(null)
  const [authenticated, setAuthenticated] = useState<boolean | null>(null)
  const [setupUserExists, setSetupUserExists] = useState<boolean | null>(null)
  const [bootError, setBootError] = useState<string | null>(null)
  useEffect(() => {
    api.auth
      .status()
      .then(async (s) => {
        setBootError(null)
        setSetupComplete(s.setup_complete)
        setAuthenticated(s.authenticated)
        if (!s.setup_complete) {
          try {
            const progress = await api.auth.setupProgress()
            setSetupUserExists(progress.user_exists)
          } catch (err) {
            setBootError(errorMessage(err, "Unable to load setup progress"))
            setSetupUserExists(true)
          }
        } else {
          setSetupUserExists(null)
        }
      })
      .catch((err) => {
        setBootError(errorMessage(err, "Unable to load app status"))
        setSetupComplete(false)
        setAuthenticated(false)
        setSetupUserExists(false)
      })
  }, [])

  const onLogin = useCallback(() => {
    setAuthenticated(true)
  }, [])

  const onSetupComplete = useCallback(() => {
    setSetupComplete(true)
    setAuthenticated(true)
  }, [])

  // Still loading
  if (
    setupComplete === null ||
    authenticated === null ||
    (setupComplete === false && setupUserExists === null)
  ) return null

  if (bootError) {
    return (
      <div role="alert" className="mx-auto max-w-lg p-6">
        <h1 className="text-xl font-semibold">Startup error</h1>
        <p className="mt-2 text-sm text-zinc-500">{bootError}</p>
        <button
          className="mt-4 rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white"
          onClick={() => window.location.reload()}
        >
          Retry
        </button>
      </div>
    )
  }

  // Determine where unauthenticated users should go. Once the admin account
  // exists, incomplete setup still needs a login path because settings writes
  // are authenticated.
  const redirect = !setupComplete
    ? authenticated || !setupUserExists
      ? "/setup"
      : "/login"
    : !authenticated
      ? "/login"
      : null

  return (
    <BrowserRouter>
      <Routes>
        {/* Setup wizard — redirect to dashboard if already completed */}
        <Route
          path="setup"
          element={
            setupComplete ? (
              <Navigate to="/" replace />
            ) : setupUserExists && !authenticated ? (
              <Navigate to="/login" replace />
            ) : (
              <Setup onSetupComplete={onSetupComplete} />
            )
          }
        />
        <Route
          path="login"
          element={
            !setupComplete && !setupUserExists ? (
              <Navigate to="/setup" replace />
            ) : authenticated ? (
              <Navigate to="/" replace />
            ) : (
              <Login onLogin={onLogin} />
            )
          }
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
