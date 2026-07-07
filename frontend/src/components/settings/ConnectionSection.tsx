import { type ConnectionTestResult } from "@/lib/api"
import { SaveButton } from "./SaveButton"
import { TestButton } from "./TestButton"
import { TestResultBanner } from "./TestResultBanner"

/**
 * The Save + Test button row and its result banner shared verbatim by the
 * Cloudflare/Tailscale/Docker settings tabs. Extracted so a tab shrinks to its
 * field list plus one `<ConnectionSection>`; the buttons' a11y (accessible
 * names, `aria-busy`) and the `role="status"` banner live in one place.
 */
export function ConnectionSection({
  saving,
  onSave,
  saveDisabled,
  testing,
  onTest,
  testLabel,
  testResult,
}: {
  saving: boolean
  onSave: () => void | Promise<void>
  saveDisabled?: boolean
  testing: boolean
  onTest: () => void
  testLabel: string
  testResult: ConnectionTestResult | null
}) {
  return (
    <>
      <div className="flex gap-2">
        <SaveButton saving={saving} onClick={onSave} disabled={saveDisabled} />
        <TestButton testing={testing} onClick={onTest} label={testLabel} />
      </div>
      {testResult && <TestResultBanner result={testResult} />}
    </>
  )
}
