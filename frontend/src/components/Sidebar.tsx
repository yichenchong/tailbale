import { useEffect, useState } from "react"
import { NavLink } from "react-router-dom"
import {
  LayoutDashboard,
  Server,
  Search,
  ScrollText,
  Settings,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { api } from "@/lib/api"

const navItems = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/services", label: "Services", icon: Server },
  { to: "/discover", label: "Discover", icon: Search },
  { to: "/events", label: "Events", icon: ScrollText },
  { to: "/settings", label: "Settings", icon: Settings },
]

export function Sidebar() {
  const [version, setVersion] = useState<string | null>(null)

  useEffect(() => {
    api.get<{ version: string }>("/version").then((r) => setVersion(r.version)).catch(() => {})
  }, [])

  return (
    <aside className="flex h-screen w-56 flex-col border-r border-zinc-200 bg-zinc-50">
      <div className="flex h-14 items-center gap-2 border-b border-zinc-200 px-4">
        <span className="text-lg font-bold tracking-tight">tailBale</span>
      </div>
      <nav className="flex-1 space-y-1 p-2">
        {navItems.map(({ to, label, icon: Icon }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                isActive
                  ? "bg-zinc-200 text-zinc-900"
                  : "text-zinc-600 hover:bg-zinc-100 hover:text-zinc-900"
              )
            }
          >
            <Icon className="h-4 w-4" />
            {label}
          </NavLink>
        ))}
      </nav>
      {version && (
        <div className="border-t border-zinc-200 px-4 py-2">
          <span className="text-xs text-zinc-400">v{version}</span>
        </div>
      )}
    </aside>
  )
}
