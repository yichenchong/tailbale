import { useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type RefObject } from "react"

export type RowActionMenuItem =
  | { key: string; label: string; to: string }
  | { key: string; label: string; onSelect: () => void; danger?: boolean }

export interface RowActionMenuController {
  openMenuId: string | null
  menuPos: { top: number; left: number } | null
  menuRef: RefObject<HTMLDivElement>
  menuActiveIndex: number
  open: (id: string, trigger: HTMLButtonElement) => void
  close: (restoreFocus?: boolean) => void
  toggle: (id: string, trigger: HTMLButtonElement) => void
  handleMenuKeyDown: (e: ReactKeyboardEvent<HTMLDivElement>) => void
}

export function useRowActionMenu(): RowActionMenuController {
  const [openMenuId, setOpenMenuId] = useState<string | null>(null)
  const [menuPos, setMenuPos] = useState<{ top: number; left: number } | null>(null)
  const [menuActiveIndex, setMenuActiveIndex] = useState(0)
  const menuTriggerRef = useRef<HTMLButtonElement | null>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  const close = useCallback((restoreFocus = false) => {
    setOpenMenuId(null)
    setMenuPos(null)
    if (restoreFocus) menuTriggerRef.current?.focus()
  }, [])

  const open = useCallback((id: string, trigger: HTMLButtonElement) => {
    const rect = trigger.getBoundingClientRect()
    setMenuPos({ top: rect.bottom + 4, left: rect.right - 176 })
    setOpenMenuId(id)
    menuTriggerRef.current = trigger
  }, [])

  const toggle = useCallback((id: string, trigger: HTMLButtonElement) => {
    if (openMenuId === id) {
      close()
    } else {
      open(id, trigger)
    }
  }, [openMenuId, close, open])

  useEffect(() => {
    if (openMenuId === null) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return
      close(true)
    }
    document.addEventListener("keydown", onKeyDown)
    return () => document.removeEventListener("keydown", onKeyDown)
  }, [openMenuId, close])

  useEffect(() => {
    if (openMenuId === null) return
    setMenuActiveIndex(0)
    menuRef.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus()
  }, [openMenuId])

  useEffect(() => {
    if (openMenuId === null) return
    const dismiss = () => close()
    window.addEventListener("scroll", dismiss, true)
    window.addEventListener("resize", dismiss)
    return () => {
      window.removeEventListener("scroll", dismiss, true)
      window.removeEventListener("resize", dismiss)
    }
  }, [openMenuId, close])

  const handleMenuKeyDown = useCallback((e: ReactKeyboardEvent<HTMLDivElement>) => {
    const items = Array.from(
      e.currentTarget.querySelectorAll<HTMLElement>('[role="menuitem"]'),
    )
    if (items.length === 0) return
    const current = items.findIndex((el) => el === document.activeElement)
    let next: number
    switch (e.key) {
      case "ArrowDown":
        next = current < 0 ? 0 : (current + 1) % items.length
        break
      case "ArrowUp":
        next = current < 0 ? items.length - 1 : (current - 1 + items.length) % items.length
        break
      case "Home":
        next = 0
        break
      case "End":
        next = items.length - 1
        break
      default:
        return
    }
    e.preventDefault()
    setMenuActiveIndex(next)
    items[next].focus()
  }, [])

  return { openMenuId, menuPos, menuRef, menuActiveIndex, open, close, toggle, handleMenuKeyDown }
}
