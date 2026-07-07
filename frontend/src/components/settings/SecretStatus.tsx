import { CheckCircle } from "lucide-react"
import { cn } from "@/lib/utils"

export function SecretStatus({ configured, id }: { configured: boolean; id?: string }) {
  return (
    <span
      id={id}
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
        configured ? "bg-green-100 text-green-700" : "bg-zinc-100 text-zinc-500"
      )}
    >
      {configured ? (
        <><CheckCircle className="h-3 w-3" /> Configured</>
      ) : (
        "Not set"
      )}
    </span>
  )
}
