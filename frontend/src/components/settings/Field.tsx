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
}: {
  label: string
  value: string
  onChange: (v: string) => void
  type?: string
  placeholder?: string
  hint?: string
  error?: string
  autoComplete?: string
}) {
  return (
    <label className="block">
      <span className="text-sm font-medium text-zinc-700">{label}</span>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        autoComplete={autoComplete}
        aria-invalid={error ? true : undefined}
        className={cn(
          "mt-1 block w-full rounded-md border px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-1",
          error
            ? "border-red-400 focus:border-red-500 focus:ring-red-500"
            : "border-zinc-300 focus:border-zinc-500 focus:ring-zinc-500"
        )}
      />
      {error ? (
        <p className="mt-1 text-xs text-red-600">{error}</p>
      ) : (
        hint && <p className="mt-1 text-xs text-zinc-400">{hint}</p>
      )}
    </label>
  )
}
