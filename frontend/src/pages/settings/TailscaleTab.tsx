import { useId } from "react"
import { type AllSettings, type ConnectionTestResult } from "@/lib/api"
import { isNonBlank } from "@/lib/validation"
import { useDirtyForm } from "@/lib/useDirtyForm"
import { useSecretField, clearOnSuccess } from "@/lib/useSecretField"
import { Field } from "@/components/settings/Field"
import { SecretStatus } from "@/components/settings/SecretStatus"
import { ConnectionSection } from "@/components/settings/ConnectionSection"
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
  const authKey = useSecretField()
  const apiKey = useSecretField()
  const authKeyStatusId = useId()
  const apiKeyStatusId = useId()

  const handleSave = () =>
    save(
      clearOnSuccess(
        () =>
          onSave({
            auth_key: authKey.payload,
            api_key: apiKey.payload,
            control_url: values.control_url,
            default_ts_hostname_prefix: values.default_ts_hostname_prefix,
          }),
        authKey,
        apiKey,
      ),
    )

  const controlUrlValid = isNonBlank(values.control_url)
  const prefixValid = isNonBlank(values.default_ts_hostname_prefix)
  const requiredValid = controlUrlValid && prefixValid

  return (
    <div className="space-y-4">
      <div>
        <Field
          label="Auth Key"
          value={authKey.value}
          onChange={authKey.set}
          type="password"
          placeholder="tskey-auth-..."
          hint="Write-only — used to register edge containers on your tailnet"
          autoComplete="off"
          describedById={authKeyStatusId}
        />
        <div className="mt-1">
          <SecretStatus configured={settings.auth_key_configured} id={authKeyStatusId} />
        </div>
      </div>
      <div>
        <Field
          label="API Key"
          value={apiKey.value}
          onChange={apiKey.set}
          type="password"
          placeholder="tskey-api-..."
          hint="Write-only — used to remove devices from tailnet on service deletion"
          autoComplete="off"
          describedById={apiKeyStatusId}
        />
        <div className="mt-1">
          <SecretStatus configured={settings.api_key_configured} id={apiKeyStatusId} />
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
      <ConnectionSection
        saving={saving}
        onSave={handleSave}
        saveDisabled={!requiredValid}
        testing={testing}
        onTest={onTest}
        testLabel="Validate Key"
        testResult={testResult}
      />
    </div>
  )
}
