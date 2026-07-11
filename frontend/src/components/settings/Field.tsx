import { useId } from "react"
import { cn } from "@/lib/utils"

export function Field({
  label,
  value,
  onChange,
  type = "text",
  placeholder,
  hint,
  error,
  autoComplete,
  describedById,
  min,
  max,
  step,
}: {
  label: string
  value: string
  onChange: (v: string) => void
  type?: string
  placeholder?: string
  hint?: string
  error?: string
  autoComplete?: string
  /** Extra element id(s) to append to `aria-describedby` (e.g. a SecretStatus). */
  describedById?: string
  /** Native numeric bounds for `type="number"` inputs (mirrors the service forms). */
  min?: number
  max?: number
  step?: number
}) {
  const id = useId()
  const inputId = `${id}-input`
  const noteId = `${id}-note`
  const note = error ?? hint
  const describedBy =
    [note ? noteId : undefined, describedById].filter(Boolean).join(" ") || undefined
  return (
    <div>
      <label htmlFor={inputId} className="block text-sm font-medium text-zinc-700">
        {label}
      </label>
      <input
        id={inputId}
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        autoComplete={autoComplete}
        min={min}
        max={max}
        step={step}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        className={cn(
          "mt-1 block w-full rounded-md border px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-1",
          error
            ? "border-red-400 focus:border-red-500 focus:ring-red-500"
            : "border-zinc-300 focus:border-zinc-500 focus:ring-zinc-500"
        )}
      />
      {error ? (
        <p id={noteId} className="mt-1 text-xs text-red-600">{error}</p>
      ) : (
        hint && <p id={noteId} className="mt-1 text-xs text-zinc-400">{hint}</p>
      )}
    </div>
  )
}
