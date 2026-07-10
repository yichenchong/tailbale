import { useEffect, useRef, useState, type KeyboardEvent } from "react"
import { useNavigate } from "react-router-dom"
import { api, type ConnectionTestResult, type SetupProgress } from "@/lib/api"
import { errorMessage } from "@/lib/utils"
import { isBaseDomain, isEmailLike, isPassword, isUsername } from "@/lib/validation"

export const STEPS = [
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

/**
 * Data-fetch + mutation + multi-step state machine for the Setup wizard (AR10).
 * Extracted from Setup.tsx so the page is primarily presentational; behavior is
 * unchanged, including the synchronous double-submit ref guard and every
 * client-side validation gate.
 */
export function useSetupWizard(onSetupComplete?: () => void) {
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
      // Steps 1 (base domain) and 3 (ACME email) are the completed steps with a
      // client-side FORMAT validator, so if the user actively re-edits either to
      // a non-empty malformed value we still block Next rather than fire an
      // obviously-doomed PUT (keeps the no-doomed-submit invariant on re-edit).
      // Other steps are presence-only, so their backend rules surface via save().
      if (step === 1) return baseDomain.trim().length === 0 || isBaseDomain(baseDomain)
      if (step === 3) return acmeEmail.trim().length === 0 || isEmailLike(acmeEmail)
      return true
    }
    if (step === 0) {
      return (
        isUsername(adminUsername) &&
        isPassword(adminPassword) &&
        adminPassword === adminPasswordConfirm
      )
    }
    if (step === 1) return isBaseDomain(baseDomain)
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

  const goBack = () => {
    setStep(step - 1)
    setTestResult(null)
    setError("")
  }

  return {
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
  }
}
