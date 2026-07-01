import { CheckCircle, XCircle } from "lucide-react"
import { cn } from "@/lib/utils"
import { type ConnectionTestResult } from "@/lib/api"

export function TestResultBanner({ result }: { result: ConnectionTestResult }) {
  return (
    <div
      className={cn(
        "flex items-center gap-2 rounded-md px-3 py-2 text-sm",
        result.success ? "bg-green-50 text-green-800" : "bg-red-50 text-red-800"
      )}
    >
      {result.success ? <CheckCircle className="h-4 w-4" /> : <XCircle className="h-4 w-4" />}
      {result.message}
    </div>
  )
}
