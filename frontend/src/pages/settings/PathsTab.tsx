import { type AllSettings } from "@/lib/api"
import { useDirtyForm } from "@/lib/useDirtyForm"
import { Field } from "@/components/settings/Field"
import { SaveButton } from "@/components/settings/SaveButton"
import { type SaveHandler } from "./useSettings"

export function PathsTab({
  settings,
  onSave,
  saving,
}: {
  settings: AllSettings["paths"]
  onSave: SaveHandler
  saving: boolean
}) {
  const { values, bind, save } = useDirtyForm(settings, (s) => ({
    generated_root: s.generated_root,
    cert_root: s.cert_root,
    tailscale_state_root: s.tailscale_state_root,
  }))

  const handleSave = () =>
    save(() =>
      onSave({
        generated_root: values.generated_root,
        cert_root: values.cert_root,
        tailscale_state_root: values.tailscale_state_root,
      }),
    )

  return (
    <div className="space-y-4">
      <Field
        label="Generated Config Root"
        value={values.generated_root}
        onChange={bind("generated_root")}
        placeholder="Leave blank to use default (data/generated)"
        hint="Where generated Caddyfiles are stored"
      />
      <Field
        label="Certificate Root"
        value={values.cert_root}
        onChange={bind("cert_root")}
        placeholder="Leave blank to use default (data/certs)"
        hint="Where TLS certificates are stored"
      />
      <Field
        label="Tailscale State Root"
        value={values.tailscale_state_root}
        onChange={bind("tailscale_state_root")}
        placeholder="Leave blank to use default (data/tailscale)"
        hint="Where Tailscale state directories are stored"
      />
      <SaveButton saving={saving} onClick={handleSave} />
    </div>
  )
}
