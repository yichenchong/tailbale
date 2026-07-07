import { useState } from "react"
import { CheckCircle, XCircle } from "lucide-react"
import { api } from "@/lib/api"
import { Field } from "@/components/settings/Field"

export function AccountTab() {
  const [currentPassword, setCurrentPassword] = useState("")
  const [newPassword, setNewPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [saving, setSaving] = useState(false)
  const [success, setSuccess] = useState("")
  const [error, setError] = useState("")

  const canSubmit =
    currentPassword.length > 0 &&
    newPassword.length >= 8 &&
    newPassword === confirmPassword &&
    !saving

  const handleChangePassword = async () => {
    setSaving(true)
    setError("")
    setSuccess("")
    try {
      await api.auth.changePassword({
        current_password: currentPassword,
        new_password: newPassword,
      })
      setSuccess("Password changed successfully")
      setCurrentPassword("")
      setNewPassword("")
      setConfirmPassword("")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to change password")
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-sm font-semibold text-zinc-700">Change Password</h3>
        <div className="mt-3 space-y-3">
          <Field
            label="Current Password"
            value={currentPassword}
            onChange={(v) => { setCurrentPassword(v); setError(""); setSuccess("") }}
            type="password"
            placeholder="Enter current password"
            autoComplete="current-password"
          />
          <Field
            label="New Password"
            value={newPassword}
            onChange={(v) => { setNewPassword(v); setError(""); setSuccess("") }}
            type="password"
            placeholder="Minimum 8 characters"
            hint="Minimum 8 characters"
            autoComplete="new-password"
          />
          <Field
            label="Confirm New Password"
            value={confirmPassword}
            onChange={(v) => { setConfirmPassword(v); setError(""); setSuccess("") }}
            type="password"
            placeholder="Confirm new password"
            autoComplete="new-password"
            error={confirmPassword.length > 0 && newPassword !== confirmPassword ? "Passwords do not match." : undefined}
          />
          <button
            onClick={handleChangePassword}
            disabled={!canSubmit}
            aria-busy={saving}
            className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800 disabled:opacity-50"
          >
            {saving ? "Changing..." : "Change Password"}
          </button>
          {success && (
            <div role="status" className="flex items-center gap-2 rounded-md bg-green-50 px-3 py-2 text-sm text-green-800">
              <CheckCircle className="h-4 w-4" /> {success}
            </div>
          )}
          {error && (
            <div role="alert" className="flex items-center gap-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-800">
              <XCircle className="h-4 w-4" /> {error}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
