import { useCallback, useState } from "react"

/**
 * Write-only secret-input state for a settings connection form (API token,
 * auth key, …).
 *
 * A secret is NEVER seeded from the server: the settings API only reports
 * whether one is *configured*, never the value. The user types a replacement,
 * and the discipline is:
 *   - include it in the save body ONLY when non-blank (`payload`), so an
 *     untouched field leaves the stored secret alone rather than clearing it;
 *   - clear the input ONLY after the save resolves (`clear`, via
 *     {@link clearOnSuccess}). A thrown save must retain the entered secret so
 *     the user can retry without re-typing — a security/UX property proven by
 *     the SettingsPage "keeps the typed secret … when a … save rejects" tests.
 *
 * Extracted from the three settings tabs (Cloudflare/Tailscale/Docker) that
 * each hand-copied this `useState("")` + `value || undefined` + clear-on-success
 * dance, where a missed clear would leave a stale password in component state.
 */
export interface SecretField {
  /** Current input value; `""` when untouched or after a successful save. */
  value: string
  /** `onChange` handler for the bound `<Field>`. */
  set: (v: string) => void
  /** The value to send, or `undefined` when blank (omit rather than overwrite). */
  readonly payload: string | undefined
  /** Reset to `""`. Call ONLY inside a save-success continuation. */
  clear: () => void
}

export function useSecretField(): SecretField {
  const [value, set] = useState("")
  const clear = useCallback(() => set(""), [])
  return { value, set, payload: value || undefined, clear }
}

/**
 * Wrap a save body so the given secret fields are cleared ONLY after it
 * resolves. The `clear` calls stay inside the success continuation: a thrown
 * `run` short-circuits before them, so the entered secrets are retained for a
 * retry. This is the security discipline previously hand-wired into each tab's
 * `handleSave` — keep the clears here, never before the `await`.
 */
export function clearOnSuccess(
  run: () => Promise<void>,
  ...fields: SecretField[]
): () => Promise<void> {
  return async () => {
    await run()
    for (const field of fields) field.clear()
  }
}
