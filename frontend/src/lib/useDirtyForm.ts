import { useCallback, useEffect, useRef, useState } from "react"

/** A settings form field is either a text/select value or a checkbox flag. */
export type FieldValue = string | boolean

export interface DirtyForm<V extends Record<string, FieldValue>> {
  /** Current form values (seeded from the server, then user-editable). */
  values: V
  /** Mark `field` user-edited and set its value. */
  set: <K extends keyof V>(field: K, value: V[K]) => void
  /** `onChange` handler factory for `field` (records the edit, then sets). */
  bind: <K extends keyof V>(field: K) => (value: V[K]) => void
  /**
   * Run a save. Clears the dirty marks BEFORE awaiting `run` so the prop-sync
   * triggered by the save's response (server-normalized values) is free to
   * adopt them. On a thrown save nothing changed server-side, so the marks are
   * restored and the user keeps their input to retry. Rethrows so the caller
   * can surface the error.
   */
  save: (run: () => Promise<void>) => Promise<void>
}

/**
 * Per-field dirty-tracking form machine.
 *
 * Extracted from the five copy-pasted SettingsPage tab state machines, each of
 * which re-derived the same `edited` set ref + `syncField` prop-sync effect +
 * save-with-restore-on-throw. Owns:
 *   - an `edited` set of user-touched field keys,
 *   - a `values` object seeded from `extract(settings)`,
 *   - a prop-sync effect that adopts incoming server values ONLY for fields the
 *     user has not edited, so a background settings refresh never clobbers a
 *     field mid-edit while still updating untouched ones, and
 *   - `save(run)` with the clear-before-await / restore-on-throw discipline.
 *
 * `extract` maps the server settings section to the tracked field values (e.g.
 * `String(...)` for numeric text inputs). It is read through a ref so an inline
 * `extract` prop does not re-run the sync effect every render; the effect keys
 * off the `settings` object identity (which the page mints anew on each
 * load/save), exactly when a fresh sync is wanted.
 */
export function useDirtyForm<S, V extends Record<string, FieldValue>>(
  settings: S,
  extract: (settings: S) => V,
): DirtyForm<V> {
  const extractRef = useRef(extract)
  extractRef.current = extract
  const [values, setValues] = useState<V>(() => extract(settings))
  const edited = useRef<Set<string>>(new Set())

  useEffect(() => {
    const server = extractRef.current(settings)
    const e = edited.current
    setValues((prev) => {
      let next = prev
      for (const key of Object.keys(server) as (keyof V)[]) {
        if (!e.has(key as string) && !Object.is(prev[key], server[key])) {
          if (next === prev) next = { ...prev }
          next[key] = server[key]
        }
      }
      return next
    })
  }, [settings])

  const set = useCallback(<K extends keyof V>(field: K, value: V[K]) => {
    edited.current.add(field as string)
    setValues((prev) => (Object.is(prev[field], value) ? prev : ({ ...prev, [field]: value } as V)))
  }, [])

  const bind = useCallback(
    <K extends keyof V>(field: K) =>
      (value: V[K]) =>
        set(field, value),
    [set],
  )

  const save = useCallback(async (run: () => Promise<void>) => {
    const previouslyEdited = [...edited.current]
    edited.current.clear()
    try {
      await run()
    } catch (e) {
      previouslyEdited.forEach((field) => edited.current.add(field))
      throw e
    }
  }, [])

  return { values, set, bind, save }
}
