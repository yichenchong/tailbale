import type { ReactNode } from "react"
import { Loader2 } from "lucide-react"
import { cn } from "@/lib/utils"

const DEFAULT_LOADING_CLASS = "flex items-center gap-2 p-8 text-zinc-500"
const DEFAULT_LOADING_ICON_CLASS = "h-4 w-4 animate-spin"
const DEFAULT_ERROR_CLASS = "rounded-md bg-red-50 px-4 py-3 text-sm text-red-800"

interface PageStateProps {
  children: ReactNode
  className?: string
}

interface PageLoadingProps extends PageStateProps {
  iconClassName?: string
}

export function PageLoading({ children, className = DEFAULT_LOADING_CLASS, iconClassName = DEFAULT_LOADING_ICON_CLASS }: PageLoadingProps) {
  return (
    <div role="status" className={cn(className)}>
      <Loader2 className={cn(iconClassName)} />
      {children}
    </div>
  )
}

export function PageError({ children, className = DEFAULT_ERROR_CLASS }: PageStateProps) {
  return (
    <div role="alert" className={cn(className)}>
      {children}
    </div>
  )
}
