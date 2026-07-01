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
  const [token, setToken] = useState("")

  const handleSave = () =>
    save(async () => {
      await onSave({ zone_id: values.zone_id, token: token || undefined })
      setToken("")
    })

  const zoneValid = isNonBlank(values.zone_id)

  return (
    <div className="space-y-4">
      <Field label="Zone ID" value={values.zone_id} onChange={bind("zone_id")} placeholder="Cloudflare zone ID" error={zoneValid ? undefined : "Required — cannot be blank"} />
      <div>
        <Field
          label="API Token"
          value={token}
          onChange={setToken}
          type="password"
          placeholder="Enter new token to update"
          hint="Write-only — current value is never shown"
          autoComplete="off"
        />
        <div className="mt-1">
          <SecretStatus configured={settings.token_configured} />
        </div>
      </div>
      <div className="flex gap-2">
        <SaveButton
          saving={saving}
          onClick={handleSave}
          disabled={!zoneValid}
        />
        <TestButton testing={testing} onClick={onTest} label="Test Connection" />
      </div>
      {testResult && <TestResultBanner result={testResult} />}
    </div>
  )
}
