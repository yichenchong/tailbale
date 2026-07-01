import { useState } from "react"
import { type AllSettings, type ConnectionTestResult } from "@/lib/api"
import { isNonBlank } from "@/lib/validation"
import { useDirtyForm } from "@/lib/useDirtyForm"
import { Field } from "@/components/settings/Field"
import { SaveButton } from "@/components/settings/SaveButton"
import { TestButton } from "@/components/settings/TestButton"
import { TestResultBanner } from "@/components/settings/TestResultBanner"
import { SecretStatus } from "@/components/settings/SecretStatus"
import { type SaveHandler } from "./useSettings"

export function TailscaleTab({
  settings,
  onSave,
  onTest,
  saving,
  testing,
  testResult,
}: {
  settings: AllSettings["tailscale"]
  onSave: SaveHandler
  onTest: () => void
  saving: boolean
  testing: boolean
  testResult: ConnectionTestResult | null
}) {
  const { values, bind, save } = useDirtyForm(settings, (s) => ({
    control_url: s.control_url,
    default_ts_hostname_prefix: s.default_ts_hostname_prefix,
  }))
  const [authKey, setAuthKey] = useState("")
  const [apiKey, setApiKey] = useState("")

  const handleSave = () =>
    save(async () => {
      await onSave({
        auth_key: authKey || undefined,
        api_key: apiKey || undefined,
        control_url: values.control_url,
        default_ts_hostname_prefix: values.default_ts_hostname_prefix,
      })
      setAuthKey("")
      setApiKey("")
    })

  const controlUrlValid = isNonBlank(values.control_url)
  const prefixValid = isNonBlank(values.default_ts_hostname_prefix)
  const requiredValid = controlUrlValid && prefixValid

  return (
    <div className="space-y-4">
      <div>
        <Field
          label="Auth Key"
          value={authKey}
          onChange={setAuthKey}
          type="password"
          placeholder="tskey-auth-..."
          hint="Write-only — used to register edge containers on your tailnet"
          autoComplete="off"
        />
        <div className="mt-1">
          <SecretStatus configured={settings.auth_key_configured} />
        </div>
      </div>
      <div>
        <Field
          label="API Key"
          value={apiKey}
          onChange={setApiKey}
          type="password"
          placeholder="tskey-api-..."
          hint="Write-only — used to remove devices from tailnet on service deletion"
          autoComplete="off"
        />
        <div className="mt-1">
          <SecretStatus configured={settings.api_key_configured} />
        </div>
      </div>
      <Field label="Control URL" value={values.control_url} onChange={bind("control_url")} placeholder="https://controlplane.tailscale.com" error={controlUrlValid ? undefined : "Required — cannot be blank"} />
      <Field
        label="Default TS Hostname Prefix"
        value={values.default_ts_hostname_prefix}
        onChange={bind("default_ts_hostname_prefix")}
        placeholder="edge"
        hint="Edge containers will be named <prefix>-<service-slug>"
        error={prefixValid ? undefined : "Required — cannot be blank"}
      />
      <div className="flex gap-2">
        <SaveButton
          saving={saving}
          onClick={handleSave}
          disabled={!requiredValid}
        />
        <TestButton testing={testing} onClick={onTest} label="Validate Key" />
      </div>
      {testResult && <TestResultBanner result={testResult} />}
    </div>
  )
}
