import { Link } from "react-router-dom"
import { MoreVertical } from "lucide-react"

import { cn } from "@/lib/utils"
import type { RowActionMenuController, RowActionMenuItem } from "./useRowActionMenu"

export function RowActionsMenu({
  rowId,
  items,
  menu,
  label = "Actions",
}: {
  rowId: string
  items: RowActionMenuItem[]
  menu: RowActionMenuController
  label?: string
}) {
  const open = menu.openMenuId === rowId
  return (
    <div className="inline-block">
      <button
        type="button"
        onClick={(e) => menu.toggle(rowId, e.currentTarget)}
        className="rounded p-1 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700"
        aria-label={label}
        aria-haspopup="true"
        aria-expanded={open}
      >
        <MoreVertical className="h-4 w-4" />
      </button>
      {open && menu.menuPos && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => menu.close()} />
          <div
            ref={menu.menuRef}
            role="menu"
            aria-label="Service actions"
            className="fixed z-50 w-44 rounded-md border border-zinc-200 bg-white py-1 shadow-lg"
            style={{ top: menu.menuPos.top, left: menu.menuPos.left }}
            onKeyDown={menu.handleMenuKeyDown}
          >
            {items.map((item, index) => {
              const tabIndex = index === menu.menuActiveIndex ? 0 : -1
              const base = "block w-full px-3 py-1.5 text-left text-sm"
              if ("to" in item) {
                return (
                  <Link
                    key={item.key}
                    to={item.to}
                    role="menuitem"
                    tabIndex={tabIndex}
                    className={cn(base, "text-zinc-700 hover:bg-zinc-50")}
                  >
                    {item.label}
                  </Link>
                )
              }
              return (
                <button
                  key={item.key}
                  type="button"
                  role="menuitem"
                  tabIndex={tabIndex}
                  onClick={item.onSelect}
                  className={cn(
                    base,
                    item.danger ? "text-red-600 hover:bg-red-50" : "text-zinc-700 hover:bg-zinc-50",
                  )}
                >
                  {item.label}
                </button>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}
