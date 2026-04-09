import { useEffect, useState } from "react"
import { useNavigate } from "react-router-dom"
import { api, type ConnectionTestResult, type LoginResponse } from "@/lib/api"
import { cn } from "@/lib/utils"
import { Loader2, CheckCircle, XCircle, ArrowRight, ArrowLeft } from "lucide-react"

const STEPS = [
  { key: "account", label: "Account" },
  { key: "domain", label: "Domain" },
  { key: "cloudflare", label: "Cloudflare" },
  { key: "acme", label: "ACME Email" },
  { key: "tailscale", label: "Tailscale" },
  { key: "docker", label: "Docker" },
]

interface SetupProgress {
  user_exists: boolean
  base_domain_set: boolean
  cloudflare_configured: boolean
  acme_email_set: boolean
  tailscale_configured: boolean
  docker_configured: boolean
}

/** Return the first incomplete step index based on backend progress. */
function firstIncompleteStep(progress: SetupProgress): number {
  if (!progress.user_exists) return 0
  if (!progress.base_domain_set) return 1
  if (!progress.cloudflare_configured) return 2
  if (!progress.acme_email_set) return 3
  if (!progress.tailscale_configured) return 4
  if (!progress.docker_configured) return 5
  return 5 // all done, show last step for "Complete Setup"
}

export default function Setup() {
  const navigate = useNavigate()
  const [step, setStep] = useState(0)
  const [initializing, setInitializing] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")
  const [testResult, setTestResult] = useState<ConnectionTestResult | null>(null)
  const [completedSteps, setCompletedSteps] = useState<Set<number>>(new Set())

  // Account fields
  const [adminUsername, setAdminUsername] = useState("")
  const [adminPassword, setAdminPassword] = useState("")
  const [adminPasswordConfirm, setAdminPasswordConfirm] = useState("")

  // Settings fields
  const [baseDomain, setBaseDomain] = useState("")
  const [cfZoneId, setCfZoneId] = useState("")
  const [cfToken, setCfToken] = useState("")
  const [acmeEmail, setAcmeEmail] = useState("")
  const [tsAuthKey, setTsAuthKey] = useState("")
  const [dockerSocket, setDockerSocket] = useState("unix:///var/run/docker.sock")

  // On mount, check which steps are already completed and skip ahead
  useEffect(() => {
    api
      .get<SetupProgress>("/auth/setup-progress")
      .then((progress) => {
        const done = new Set<number>()
        if (progress.user_exists) done.add(0)
        if (progress.base_domain_set) done.add(1)
        if (progress.cloudflare_configured) done.add(2)
        if (progress.acme_email_set) done.add(3)
        if (progress.tailscale_configured) done.add(4)
        if (progress.docker_configured) done.add(5)
        setCompletedSteps(done)
        setStep(firstIncompleteStep(progress))
      })
      .catch(() => {
        // If the endpoint isn't available, start from step 0
      })
      .finally(() => setInitializing(false))
  }, [])

  const saveAndNext = async () => {
    setSaving(true)
    setError("")
    setTestResult(null)
    try {
      // Skip API calls for already-completed steps when user hasn't entered new data
      const alreadyDone = completedSteps.has(step)

      if (step === 0) {
        if (alreadyDone) {
          // User already exists — skip account creation
        } else {
          await api.post<LoginResponse>("/auth/setup-user", {
            username: adminUsername,
            password: adminPassword,
          })
        }
      } else if (step === 1) {
        await api.put("/settings/general", { base_domain: baseDomain })
      } else if (step === 2) {
        await api.put("/settings/cloudflare", {
          zone_id: cfZoneId,
          ...(cfToken ? { token: cfToken } : {}),
        })
        if (cfToken && cfZoneId) {
          const result = await api.post<ConnectionTestResult>("/settings/test/cloudflare")
          setTestResult(result)
          if (!result.success) {
            setSaving(false)
            return
          }
        }
      } else if (step === 3) {
        await api.put("/settings/general", { acme_email: acmeEmail })
      } else if (step === 4) {
        await api.put("/settings/tailscale", {
          ...(tsAuthKey ? { auth_key: tsAuthKey } : {}),
        })
        if (tsAuthKey) {
          const result = await api.post<ConnectionTestResult>("/settings/test/tailscale")
          setTestResult(result)
          if (!result.success) {
            setSaving(false)
            return
          }
        }
      } else if (step === 5) {
        await api.put("/settings/docker", { socket_path: dockerSocket })
        const result = await api.post<ConnectionTestResult>("/settings/test/docker")
        setTestResult(result)
        if (!result.success) {
          setSaving(false)
          return
        }
      }

      // Mark this step as completed
      setCompletedSteps((prev) => new Set(prev).add(step))

      if (step < STEPS.length - 1) {
        setStep(step + 1)
        setTestResult(null)
      } else {
        await api.put("/settings/setup-complete", {})
        navigate("/")
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  const canProceed = () => {
    // Already-completed steps can always be skipped
    if (completedSteps.has(step)) return true
    if (step === 0)
      return (
        adminUsername.length > 0 &&
        adminPassword.length >= 8 &&
        adminPassword === adminPasswordConfirm
      )
    if (step === 1) return baseDomain.length > 0
    if (step === 2) return cfZoneId.length > 0
    if (step === 3) return acmeEmail.includes("@")
    if (step === 4) return tsAuthKey.length > 0
    return true
  }

  if (initializing) {
    return (
      <div className="mx-auto max-w-lg py-12 px-4 flex items-center gap-2 text-zinc-500">
        <Loader2 className="h-5 w-5 animate-spin" /> Loading setup progress...
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-lg py-12 px-4">
      <h1 className="text-2xl font-bold">Welcome to tailBale</h1>
      <p className="mt-1 text-zinc-500">Let's configure your orchestrator.</p>

      {/* Step indicator */}
      <div className="mt-6 flex gap-1">
        {STEPS.map((s, i) => (
          <div
            key={s.key}
            className={cn(
              "h-1.5 flex-1 rounded-full",
              i <= step ? "bg-zinc-900" : "bg-zinc-200"
            )}
          />
        ))}
      </div>
      <p className="mt-2 text-xs text-zinc-500">
        Step {step + 1} of {STEPS.length}: {STEPS[step].label}
      </p>

      {/* Already-completed banner */}
      {completedSteps.has(step) && (
        <div className="mt-6 flex items-center gap-2 rounded-md bg-green-50 px-4 py-3 text-sm text-green-800">
          <CheckCircle className="h-4 w-4 shrink-0" />
          This step was already completed. Click Next to continue, or update the values below.
        </div>
      )}

      {/* Step content */}
      <div className="mt-6 space-y-4">
        {step === 0 && (
          <>
            <label className="block">
              <span className="text-sm font-medium text-zinc-700">Username</span>
              <p className="text-xs text-zinc-400 mt-0.5">Choose a username for the admin account.</p>
              <input
                type="text" value={adminUsername}
                onChange={(e) => setAdminUsername(e.target.value)}
                placeholder="admin"
                autoComplete="username"
                className="mt-1 block w-full rounded-md border border-zinc-300 px-3 py-2 text-sm focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
              />
            </label>
            <label className="block">
              <span className="text-sm font-medium text-zinc-700">Password</span>
              <p className="text-xs text-zinc-400 mt-0.5">Minimum 8 characters.</p>
              <input
                type="password" value={adminPassword}
                onChange={(e) => setAdminPassword(e.target.value)}
                placeholder="Password"
                autoComplete="new-password"
                className="mt-1 block w-full rounded-md border border-zinc-300 px-3 py-2 text-sm focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
              />
            </label>
            <label className="block">
              <span className="text-sm font-medium text-zinc-700">Confirm Password</span>
              <input
                type="password" value={adminPasswordConfirm}
                onChange={(e) => setAdminPasswordConfirm(e.target.value)}
                placeholder="Confirm password"
                autoComplete="new-password"
                className="mt-1 block w-full rounded-md border border-zinc-300 px-3 py-2 text-sm focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
              />
              {adminPasswordConfirm.length > 0 && adminPassword !== adminPasswordConfirm && (
                <p className="mt-1 text-xs text-red-600">Passwords do not match.</p>
              )}
            </label>
          </>
        )}

        {step === 1 && (
          <label className="block">
            <span className="text-sm font-medium text-zinc-700">Base Domain</span>
            <p className="text-xs text-zinc-400 mt-0.5">The root domain managed in Cloudflare (e.g. mydomain.com)</p>
            <input
              type="text" value={baseDomain}
              onChange={(e) => setBaseDomain(e.target.value)}
              placeholder="mydomain.com"
              className="mt-1 block w-full rounded-md border border-zinc-300 px-3 py-2 text-sm focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
            />
          </label>
        )}

        {step === 2 && (
          <>
            <label className="block">
              <span className="text-sm font-medium text-zinc-700">Cloudflare Zone ID</span>
              <p className="text-xs text-zinc-400 mt-0.5">Found in Cloudflare dashboard under your domain's overview.</p>
              <input
                type="text" value={cfZoneId}
                onChange={(e) => setCfZoneId(e.target.value)}
                placeholder="abc123..."
                className="mt-1 block w-full rounded-md border border-zinc-300 px-3 py-2 text-sm font-mono focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
              />
            </label>
            <label className="block">
              <span className="text-sm font-medium text-zinc-700">Cloudflare API Token</span>
              <p className="text-xs text-zinc-400 mt-0.5">Needs DNS:Edit permissions for your zone.</p>
              <input
                type="password" value={cfToken}
                onChange={(e) => setCfToken(e.target.value)}
                placeholder="API token..."
                className="mt-1 block w-full rounded-md border border-zinc-300 px-3 py-2 text-sm font-mono focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
              />
            </label>
          </>
        )}

        {step === 3 && (
          <label className="block">
            <span className="text-sm font-medium text-zinc-700">ACME Email</span>
            <p className="text-xs text-zinc-400 mt-0.5">Used for Let's Encrypt certificate registration.</p>
            <input
              type="email" value={acmeEmail}
              onChange={(e) => setAcmeEmail(e.target.value)}
              placeholder="you@example.com"
              className="mt-1 block w-full rounded-md border border-zinc-300 px-3 py-2 text-sm focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
            />
          </label>
        )}

        {step === 4 && (
          <label className="block">
            <span className="text-sm font-medium text-zinc-700">Tailscale Auth Key</span>
            <p className="text-xs text-zinc-400 mt-0.5">Reusable auth key from Tailscale admin console. Used to authenticate edge containers.</p>
            <input
              type="password" value={tsAuthKey}
              onChange={(e) => setTsAuthKey(e.target.value)}
              placeholder="tskey-auth-..."
              className="mt-1 block w-full rounded-md border border-zinc-300 px-3 py-2 text-sm font-mono focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
            />
          </label>
        )}

        {step === 5 && (
          <label className="block">
            <span className="text-sm font-medium text-zinc-700">Docker Socket Path</span>
            <p className="text-xs text-zinc-400 mt-0.5">Usually unix:///var/run/docker.sock on Linux/Unraid.</p>
            <input
              type="text" value={dockerSocket}
              onChange={(e) => setDockerSocket(e.target.value)}
              className="mt-1 block w-full rounded-md border border-zinc-300 px-3 py-2 text-sm font-mono focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
            />
          </label>
        )}
      </div>

      {/* Test result */}
      {testResult && (
        <div className={cn(
          "mt-4 flex items-start gap-2 rounded-md px-4 py-3 text-sm",
          testResult.success ? "bg-green-50 text-green-800" : "bg-red-50 text-red-800"
        )}>
          {testResult.success ? <CheckCircle className="mt-0.5 h-4 w-4 shrink-0" /> : <XCircle className="mt-0.5 h-4 w-4 shrink-0" />}
          {testResult.message}
        </div>
      )}

      {error && (
        <div className="mt-4 rounded-md bg-red-50 px-4 py-3 text-sm text-red-800">{error}</div>
      )}

      {/* Navigation */}
      <div className="mt-6 flex items-center justify-between">
        <button
          onClick={() => { setStep(step - 1); setTestResult(null); setError("") }}
          disabled={step === 0}
          className="inline-flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-700 disabled:opacity-30"
        >
          <ArrowLeft className="h-4 w-4" /> Back
        </button>
        <button
          onClick={saveAndNext}
          disabled={saving || !canProceed()}
          className="inline-flex items-center gap-1.5 rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800 disabled:opacity-50"
        >
          {saving ? (
            <><Loader2 className="h-4 w-4 animate-spin" /> Saving...</>
          ) : step < STEPS.length - 1 ? (
            <>Next <ArrowRight className="h-4 w-4" /></>
          ) : (
            <>Complete Setup <CheckCircle className="h-4 w-4" /></>
          )}
        </button>
      </div>
    </div>
  )
}
