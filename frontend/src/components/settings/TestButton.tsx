import { Loader2 } from "lucide-react"

export function TestButton({ testing, onClick, label }: { testing: boolean; onClick: () => void; label: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={testing}
      aria-busy={testing}
      className="inline-flex items-center gap-1.5 rounded-md border border-zinc-300 px-4 py-2 text-sm font-medium text-zinc-700 hover:bg-zinc-50 disabled:opacity-50"
    >
      {testing ? <><Loader2 className="h-4 w-4 animate-spin" /> Testing...</> : label}
    </button>
  )
}
