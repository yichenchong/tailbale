export function SaveButton({ saving, onClick, label, disabled }: { saving: boolean; onClick: () => void | Promise<void>; label?: string; disabled?: boolean }) {
  const handleClick = () => {
    void Promise.resolve(onClick()).catch(() => undefined)
  }
  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={saving || disabled}
      aria-busy={saving}
      className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800 disabled:opacity-50"
    >
      {saving ? "Saving..." : label ?? "Save"}
    </button>
  )
}
