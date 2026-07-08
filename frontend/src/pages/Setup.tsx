import { cn } from "@/lib/utils"
import { Loader2, CheckCircle, XCircle, ArrowRight, ArrowLeft } from "lucide-react"
import { STEPS, useSetupWizard } from "./useSetupWizard"

export default function Setup({ onSetupComplete }: { onSetupComplete?: () => void }) {
  const {
    step,
    initializing,
    saving,
    error,
    testResult,
    completedSteps,
    adminUsername,
    setAdminUsername,
    adminPassword,
    setAdminPassword,
    adminPasswordConfirm,
    setAdminPasswordConfirm,
    baseDomain,
    setBaseDomain,
    cfZoneId,
    setCfZoneId,
    cfToken,
    setCfToken,
    acmeEmail,
    setAcmeEmail,
    tsAuthKey,
    setTsAuthKey,
    tsApiKey,
    setTsApiKey,
    dockerSocket,
    setDockerSocket,
    setDockerSocketEdited,
    saveAndNext,
    canProceed,
    handleStepKeyDown,
    goBack,
  } = useSetupWizard(onSetupComplete)

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
            onClick={goBack}
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
