import { type AllSettings, type ConnectionTestResult } from "@/lib/api"
import { isNonBlank } from "@/lib/validation"
import { useDirtyForm } from "@/lib/useDirtyForm"
import { Field } from "@/components/settings/Field"
import { ConnectionSection } from "@/components/settings/ConnectionSection"
import { type SaveHandler } from "./useSettings"

export function DockerTab({
  settings,
  onSave,
  onTest,
  saving,
  testing,
  testResult,
}: {
  settings: AllSettings["docker"]
  onSave: SaveHandler
  onTest: () => void
  saving: boolean
  testing: boolean
  testResult: ConnectionTestResult | null
}) {
  const { values, bind, save } = useDirtyForm(settings, (s) => ({ socket_path: s.socket_path }))

  const handleSave = () => save(() => onSave({ socket_path: values.socket_path }))

  const socketPathValid = isNonBlank(values.socket_path)

  return (
    <div className="space-y-4">
      <Field
        label="Docker Socket Path"
        value={values.socket_path}
        onChange={bind("socket_path")}
        placeholder="unix:///var/run/docker.sock"
        error={socketPathValid ? undefined : "Required — cannot be blank"}
      />
      <ConnectionSection
        saving={saving}
        onSave={handleSave}
        saveDisabled={!socketPathValid}
        testing={testing}
        onTest={onTest}
        testLabel="Test Connection"
        testResult={testResult}
      />
    </div>
  )
}
