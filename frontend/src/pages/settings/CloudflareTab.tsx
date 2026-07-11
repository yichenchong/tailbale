import { useId } from "react"
import { type AllSettings, type ConnectionTestResult } from "@/lib/api"
import { isNonBlank } from "@/lib/validation"
import { useDirtyForm } from "@/lib/useDirtyForm"
import { useSecretField, clearOnSuccess } from "@/lib/useSecretField"
import { Field } from "@/components/settings/Field"
import { SecretStatus } from "@/components/settings/SecretStatus"
import { ConnectionSection } from "@/components/settings/ConnectionSection"
import { type SaveHandler } from "./useSettings"

export function CloudflareTab({
  settings,
  onSave,
  onTest,
  saving,
  testing,
  testResult,
}: {
  settings: AllSettings["cloudflare"]
  onSave: SaveHandler
  onTest: () => void
  saving: boolean
  testing: boolean
  testResult: ConnectionTestResult | null
}) {
  const { values, bind, save } = useDirtyForm(settings, (s) => ({ zone_id: s.zone_id }))
  const token = useSecretField()
  const tokenStatusId = useId()

  const handleSave = () =>
    save(
      clearOnSuccess(
        () => onSave({ zone_id: values.zone_id, token: token.payload }),
        token,
      ),
    )

  const zoneValid = isNonBlank(values.zone_id)

  return (
    <div className="space-y-4">
      <Field label="Zone ID" value={values.zone_id} onChange={bind("zone_id")} placeholder="Cloudflare zone ID" error={zoneValid ? undefined : "Required — cannot be blank"} />
      <div>
        <Field
          label="API Token"
          value={token.value}
          onChange={token.set}
          type="password"
          placeholder="Enter new token to update"
          hint="Write-only — current value is never shown"
          autoComplete="off"
          describedById={tokenStatusId}
        />
        <div className="mt-1">
          <SecretStatus configured={settings.token_configured} id={tokenStatusId} />
        </div>
      </div>
      <ConnectionSection
        saving={saving}
        onSave={handleSave}
        saveDisabled={!zoneValid}
        testing={testing}
        onTest={onTest}
        testLabel="Test Connection"
        testResult={testResult}
      />
    </div>
  )
}
