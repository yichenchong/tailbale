import { useEffect, useRef, useState, type KeyboardEvent } from "react"
import { useNavigate } from "react-router-dom"
import { api, type ConnectionTestResult, type SetupProgress } from "@/lib/api"
import { cn, errorMessage } from "@/lib/utils"
import { isEmailLike } from "@/lib/validation"
import { Loader2, CheckCircle, XCircle, ArrowRight, ArrowLeft } from "lucide-react"

const STEPS = [
  { key: "account", label: "Account" },
  { key: "domain", label: "Domain" },
  { key: "cloudflare", label: "Cloudflare" },
  { key: "acme", label: "ACME Email" },
  { key: "tailscale", label: "Tailscale" },
  { key: "docker", label: "Docker" },
]

function firstIncompleteStep(progress: SetupProgress): number {
  if (!progress.user_exists) return 0
  if (!progress.base_domain_set) return 1
  if (!progress.cloudflare_configured) return 2
  if (!progress.acme_email_set) return 3
  if (!progress.tailscale_configured) return 4
  if (!progress.docker_configured) return 5
  return 5
}

export default function Setup({ onSetupComplete }: { onSetupComplete?: () => void }) {
  const navigate = useNavigate()
  const [step, setStep] = useState(0)
  const [initializing, setInitializing] = useState(true)
  const [saving, setSaving] = useState(false)
  // Synchronous in-flight guard for the per-step save. A ref (not the `saving`
  // state) because two submit events can fire within one React batch (a
  // same-batch double-Enter on the last field, or assistive tech) before the
  // `saving=true` re-render commits; both would close over the stale
  // `saving=false` and slip past a state check, firing the step's POST/PUT
  // twice — e.g. a duplicate setup-user that 409s with a confusing "user
  // already exists" even though the account was just created. The ref flips
  // immediately so the second call bails; `saving` still drives the UI.
  const submittingRef = useRef(false)
  const [error, setError] = useState("")
  const [testResult, setTestResult] = useState<ConnectionTestResult | null>(null)
  const [completedSteps, setCompletedSteps] = useState<Set<number>>(new Set())
  const [cfTokenConfigured, setCfTokenConfigured] = useState(false)
  const [adminUsername, setAdminUsername] = useState("")
  const [adminPassword, setAdminPassword] = useState("")
  const [adminPasswordConfirm, setAdminPasswordConfirm] = useState("")

  const [baseDomain, setBaseDomain] = useState("")
  const [cfZoneId, setCfZoneId] = useState("")
  const [cfToken, setCfToken] = useState("")
  const [acmeEmail, setAcmeEmail] = useState("")
  const [tsAuthKey, setTsAuthKey] = useState("")
  const [tsApiKey, setTsApiKey] = useState("")
  const [dockerSocket, setDockerSocket] = useState("unix:///var/run/docker.sock")
  const [dockerSocketEdited, setDockerSocketEdited] = useState(false)

  useEffect(() => {
    api.auth
      .setupProgress()
      .then((progress) => {
        const done = new Set<number>()
        if (progress.user_exists) done.add(0)
        if (progress.base_domain_set) done.add(1)
        if (progress.cloudflare_configured) done.add(2)
        if (progress.acme_email_set) done.add(3)
        if (progress.tailscale_configured) done.add(4)
        if (progress.docker_configured) done.add(5)
        setCompletedSteps(done)
        setCfTokenConfigured(Boolean(progress.cloudflare_token_set || progress.cloudflare_configured))
        setStep(firstIncompleteStep(progress))
      })
      .catch((e) => {
        setError(errorMessage(e, "Failed to load setup progress"))
      })
      .finally(() => setInitializing(false))
  }, [])

  const saveAndNext = async () => {
    if (submittingRef.current) return
    submittingRef.current = true
    setSaving(true)
    setError("")
    setTestResult(null)
    try {
      const alreadyDone = completedSteps.has(step)

      if (step === 0) {
        if (!alreadyDone) {
          await api.auth.setupUser({
            username: adminUsername.trim(),
            password: adminPassword,
          })
        }
      } else if (step === 1) {
        if (!alreadyDone || baseDomain) {
          await api.settings.update("general", { base_domain: baseDomain })
        }
      } else if (step === 2) {
        if (!alreadyDone || cfZoneId || cfToken) {
          await api.settings.update("cloudflare", {
            ...(cfZoneId ? { zone_id: cfZoneId } : {}),
            ...(cfToken ? { token: cfToken } : {}),
          })
          if (cfToken && cfZoneId) {
            const result = await api.settings.test("cloudflare")
            setTestResult(result)
            if (!result.success) {
              setSaving(false)
              return
            }
          }
        }
      } else if (step === 3) {
        if (!alreadyDone || acmeEmail) {
          await api.settings.update("general", { acme_email: acmeEmail })
        }
      } else if (step === 4) {
        if (!alreadyDone || tsAuthKey || tsApiKey) {
          await api.settings.update("tailscale", {
            ...(tsAuthKey ? { auth_key: tsAuthKey } : {}),
            ...(tsApiKey ? { api_key: tsApiKey } : {}),
          })
          if (tsAuthKey && tsApiKey) {
            const result = await api.settings.test("tailscale")
            setTestResult(result)
            if (!result.success) {
              setSaving(false)
              return
            }
          }
        }
      } else if (step === 5) {
        if (!alreadyDone || dockerSocketEdited) {
          await api.settings.update("docker", { socket_path: dockerSocket })
        }
        const result = await api.settings.test("docker")
        setTestResult(result)
        if (!result.success) {
          setSaving(false)
          return
        }
      }

      setCompletedSteps((prev) => new Set(prev).add(step))

      if (step < STEPS.length - 1) {
        setStep(step + 1)
        setTestResult(null)
      } else {
        await api.settings.update("setup-complete", {})
        onSetupComplete?.()
        navigate("/", { replace: true })
      }
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      submittingRef.current = false
      setSaving(false)
    }
  }

  const canProceed = () => {
    if (completedSteps.has(step)) {
      // A resumed setup loads only progress flags (not the secret/value fields),
      // so a completed step normally has empty inputs -> "keep existing" -> allow.
      // Step 3 (ACME email) is the one completed step with a client-side FORMAT
      // validator, so if the user actively re-edits it to a non-empty malformed
      // value we still block Next rather than fire an obviously-doomed PUT (keeps
      // the no-doomed-submit invariant on re-edit). Other steps have no client
      // format check, so their backend-only rules surface gracefully via save().
      if (step === 3) return acmeEmail.trim().length === 0 || isEmailLike(acmeEmail)
      return true
    }
    if (step === 0) {
      return (
        adminUsername.trim().length > 0 &&
        adminPassword.length >= 8 &&
        adminPassword === adminPasswordConfirm
      )
    }
    if (step === 1) return baseDomain.trim().length > 0
    if (step === 2) return cfZoneId.trim().length > 0 && (cfToken.length > 0 || cfTokenConfigured)
    if (step === 3) return isEmailLike(acmeEmail)
    if (step === 4) return tsAuthKey.trim().length > 0 && tsApiKey.trim().length > 0
    return true
  }

  const handleStepKeyDown = (e: KeyboardEvent<HTMLFormElement>) => {
    if (e.key !== "Enter" || e.shiftKey || e.defaultPrevented) return

    const target = e.target
    if (!(target instanceof HTMLInputElement || target instanceof HTMLSelectElement)) return

    e.preventDefault()

    const fields = Array.from(
      e.currentTarget.querySelectorAll("input:not([type='hidden']):not([disabled]), select:not([disabled])")
    ) as Array<HTMLInputElement | HTMLSelectElement>

    const currentIndex = fields.indexOf(target)
    const nextField = currentIndex >= 0 ? fields[currentIndex + 1] : null
    if (nextField) {
      nextField.focus()
      if (nextField instanceof HTMLInputElement) {
        nextField.select()
      }
      return
    }

    if (!saving && canProceed()) {
      void saveAndNext()
    }
  }

  if (initializing) {
    return (
      <div className="mx-auto max-w-lg py-12 px-4 flex items-center gap-2 text-zinc-500">
        <Loader2 className="h-5 w-5 animate-spin" /> Loading setup progress...
      </div>
    )
  }

  return (
    <main className="mx-auto max-w-lg py-12 px-4">
      <h1 className="text-2xl font-bold">Welcome to tailBale</h1>
      <p className="mt-1 text-zinc-500">Let's configure your orchestrator.</p>

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

      {completedSteps.has(step) && (
        <div className="mt-6 flex items-center gap-2 rounded-md bg-green-50 px-4 py-3 text-sm text-green-800">
          <CheckCircle className="h-4 w-4 shrink-0" />
          This step was already completed. Click Next to continue, or update the values below.
        </div>
      )}

      <form
        className="mt-6 space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          if (!saving && canProceed()) {
            void saveAndNext()
          }
        }}
        onKeyDown={handleStepKeyDown}
      >
        {step === 0 && (
          <>
            <label className="block">
              <span className="text-sm font-medium text-zinc-700">Username</span>
              <p className="text-xs text-zinc-400 mt-0.5">Choose a username for the admin account.</p>
              <input
                type="text"
                value={adminUsername}
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
                type="password"
                value={adminPassword}
                onChange={(e) => setAdminPassword(e.target.value)}
                placeholder="Password"
                autoComplete="new-password"
                className="mt-1 block w-full rounded-md border border-zinc-300 px-3 py-2 text-sm focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
              />
            </label>
            <label className="block">
              <span className="text-sm font-medium text-zinc-700">Confirm Password</span>
              <input
                type="password"
                value={adminPasswordConfirm}
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
              type="text"
              value={baseDomain}
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
                type="text"
                value={cfZoneId}
                onChange={(e) => setCfZoneId(e.target.value)}
                placeholder="abc123..."
                className="mt-1 block w-full rounded-md border border-zinc-300 px-3 py-2 text-sm font-mono focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
              />
            </label>
            <label className="block">
              <span className="text-sm font-medium text-zinc-700">Cloudflare API Token</span>
              <p className="text-xs text-zinc-400 mt-0.5">Needs DNS:Edit permissions for your zone.</p>
              <input
                type="password"
                value={cfToken}
                onChange={(e) => setCfToken(e.target.value)}
                autoComplete="off"
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
              type="email"
              value={acmeEmail}
              onChange={(e) => setAcmeEmail(e.target.value)}
              placeholder="you@example.com"
              className="mt-1 block w-full rounded-md border border-zinc-300 px-3 py-2 text-sm focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
            />
          </label>
        )}

        {step === 4 && (
          <>
            <label className="block">
              <span className="text-sm font-medium text-zinc-700">Tailscale Auth Key</span>
              <p className="text-xs text-zinc-400 mt-0.5">Reusable auth key from the Tailscale admin console. Must start with tskey-auth-.</p>
              <input
                type="password"
                value={tsAuthKey}
                onChange={(e) => setTsAuthKey(e.target.value)}
                autoComplete="off"
                placeholder="tskey-auth-..."
                className="mt-1 block w-full rounded-md border border-zinc-300 px-3 py-2 text-sm font-mono focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
              />
            </label>
            <label className="block">
              <span className="text-sm font-medium text-zinc-700">Tailscale API Key</span>
              <p className="text-xs text-zinc-400 mt-0.5">Required for setup. Used to manage tailnet devices when services are recreated or removed. Must start with tskey-api-.</p>
              <input
                type="password"
                value={tsApiKey}
                onChange={(e) => setTsApiKey(e.target.value)}
                autoComplete="off"
                placeholder="tskey-api-..."
                className="mt-1 block w-full rounded-md border border-zinc-300 px-3 py-2 text-sm font-mono focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
              />
            </label>
          </>
        )}

        {step === 5 && (
          <label className="block">
            <span className="text-sm font-medium text-zinc-700">Docker Socket Path</span>
            <p className="text-xs text-zinc-400 mt-0.5">Usually unix:///var/run/docker.sock on Linux.</p>
            <input
              type="text"
              value={dockerSocket}
              onChange={(e) => { setDockerSocket(e.target.value); setDockerSocketEdited(true) }}
              className="mt-1 block w-full rounded-md border border-zinc-300 px-3 py-2 text-sm font-mono focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
            />
          </label>
        )}

        {testResult && (
          <div
            role="status"
            className={cn(
              "mt-4 flex items-start gap-2 rounded-md px-4 py-3 text-sm",
              testResult.success ? "bg-green-50 text-green-800" : "bg-red-50 text-red-800"
            )}
          >
            {testResult.success ? <CheckCircle className="mt-0.5 h-4 w-4 shrink-0" /> : <XCircle className="mt-0.5 h-4 w-4 shrink-0" />}
            {testResult.message}
          </div>
        )}

        {error && (
          <div role="alert" className="mt-4 rounded-md bg-red-50 px-4 py-3 text-sm text-red-800">{error}</div>
        )}

        <div className="mt-6 flex items-center justify-between">
          <button
            type="button"
            onClick={() => {
              setStep(step - 1)
              setTestResult(null)
              setError("")
            }}
            disabled={step === 0 || saving}
            className="inline-flex items-center gap-1 text-sm text-zinc-500 hover:text-zinc-700 disabled:opacity-30"
          >
            <ArrowLeft className="h-4 w-4" /> Back
          </button>
          <button
            type="submit"
            disabled={saving || !canProceed()}
            aria-busy={saving}
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
      </form>
    </main>
  )
}
