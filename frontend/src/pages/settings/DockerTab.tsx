import { type AllSettings, type ConnectionTestResult } from "@/lib/api"
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

  const usingDockerEnv = values.socket_path.trim() === ""

  return (
    <div className="space-y-4">
      <Field
        label="Docker Socket Path"
        value={values.socket_path}
        onChange={bind("socket_path")}
        placeholder="Leave blank to use DOCKER_HOST / docker.from_env()"
        hint={usingDockerEnv ? "Blank: tailBale will use Docker environment variables." : undefined}
      />
      <ConnectionSection
        saving={saving}
        onSave={handleSave}
        testing={testing}
        onTest={onTest}
        testLabel="Test Connection"
        testResult={testResult}
      />
    </div>
  )
}
