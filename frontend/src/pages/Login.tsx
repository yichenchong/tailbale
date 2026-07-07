import { useRef, useState } from "react"
import { useNavigate } from "react-router-dom"
import { api } from "@/lib/api"
import { useStaticFavicon } from "@/lib/useFavicon"
import { Loader2, LogIn } from "lucide-react"

export default function Login({ onLogin }: { onLogin?: () => void } = {}) {
  useStaticFavicon("/favicon-healthy.svg")
  const navigate = useNavigate()
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)
  // Synchronous in-flight guard for the login POST. A ref (not the `loading`
  // state) because two submit events can fire within one React batch (a
  // same-batch double-Enter / programmatic double submit) before the
  // `loading=true` re-render commits; both would then close over the stale
  // `loading=false` and slip past a state check, POSTing twice — on wrong
  // credentials that burns two of the rate limiter's attempts per user action.
  // The ref flips immediately, so the second handler bails. `loading` still
  // drives the button/label UI.
  const submittingRef = useRef(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (submittingRef.current) return
    submittingRef.current = true
    setLoading(true)
    setError("")
    try {
      await api.auth.login({ username, password })
      onLogin?.()
      navigate("/")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed")
    } finally {
      submittingRef.current = false
      setLoading(false)
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-zinc-50 px-4">
      <div className="w-full max-w-sm">
        <h1 className="text-2xl font-bold text-center">tailBale</h1>
        <p className="mt-1 text-center text-sm text-zinc-500">
          Sign in to continue.
        </p>

        <form onSubmit={handleSubmit} className="mt-8 space-y-4">
          <label className="block">
            <span className="text-sm font-medium text-zinc-700">Username</span>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="Username"
              autoComplete="username"
              className="mt-1 block w-full rounded-md border border-zinc-300 px-3 py-2 text-sm focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
            />
          </label>

          <label className="block">
            <span className="text-sm font-medium text-zinc-700">Password</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Password"
              autoComplete="current-password"
              className="mt-1 block w-full rounded-md border border-zinc-300 px-3 py-2 text-sm focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
            />
          </label>

          {error && (
            <div role="alert" className="rounded-md bg-red-50 px-4 py-3 text-sm text-red-800">
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={loading || !username || !password}
            className="flex w-full items-center justify-center gap-2 rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800 disabled:opacity-50"
          >
            {loading ? (
              <><Loader2 className="h-4 w-4 animate-spin" /> Signing in...</>
            ) : (
              <><LogIn className="h-4 w-4" /> Sign In</>
            )}
          </button>
        </form>
      </div>
    </main>
  )
}
