import { Outlet } from "react-router-dom"
import { Sidebar } from "./Sidebar"
import { useDynamicFavicon } from "@/lib/useFavicon"

export function Layout() {
  useDynamicFavicon(30_000)

  return (
    <div className="flex h-screen">
      <Sidebar />
      <main className="flex-1 overflow-y-auto bg-white p-6">
        <Outlet />
      </main>
    </div>
  )
}
