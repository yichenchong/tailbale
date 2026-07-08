import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { api, type ServiceItem } from "@/lib/api"
import { errorMessage } from "@/lib/utils"

export interface UseServiceActionsParams {
  service: ServiceItem
  id: string | undefined
  refresh: (opts?: { background?: boolean }) => Promise<void>
  showActionMsg: (msg: string) => void
  clearActionMsg: () => void
  applyServiceUpdate: (svc: ServiceItem) => void
  setError: (value: string | null) => void
}

/**
 * Network/mutation logic + confirmation and loading state behind the service
 * action bar (AR10). Extracted from `ServiceActions` so that component stays
 * purely presentational. Behavior is unchanged: successes/soft-failures route
 * through `showActionMsg`, delete/toggle hard errors through `setError`, and a
 * background `refresh` follows each mutating action.
 */
export function useServiceActions({
  service,
  id,
  refresh,
  showActionMsg,
  clearActionMsg,
  applyServiceUpdate,
  setError,
}: UseServiceActionsParams) {
  const navigate = useNavigate()
  const [deleting, setDeleting] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [cleanupDns, setCleanupDns] = useState(true)
  const [confirmDisable, setConfirmDisable] = useState(false)
  const [confirmRecreate, setConfirmRecreate] = useState(false)
  const [confirmForceRenew, setConfirmForceRenew] = useState(false)
  const [renewing, setRenewing] = useState(false)

  const runAction = async (action: () => Promise<unknown>) => {
    clearActionMsg()
    try {
      await action()
      void refresh({ background: true })
    } catch (e) {
      showActionMsg(errorMessage(e))
    }
  }

  const handleToggleEnabled = async () => {
    setConfirmDisable(false)
    try {
      if (service.enabled) {
        const svc = await api.services.disable(id ?? "")
        applyServiceUpdate(svc)
      } else {
        const svc = await api.services.update(id ?? "", { enabled: true })
        applyServiceUpdate(svc)
      }
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  const handleRecreateEdge = async () => {
    setConfirmRecreate(false)
    runAction(() => api.services.recreateEdge(id ?? ""))
  }

  const handleDelete = async () => {
    setDeleting(true)
    try {
      await api.services.remove(id ?? "", { cleanupDns })
      navigate("/services")
    } catch (e) {
      setError(errorMessage(e))
      setDeleting(false)
    }
  }

  // Renew the certificate. The first call never forces: the backend refuses
  // (needs_force) when the cert is healthy and far from expiry, and we surface a
  // modal so the user can deliberately opt into a rate-limited Let's Encrypt
  // hit. Near/expired certs renew straight away with no popup.
  const handleRenewCert = async () => {
    clearActionMsg()
    setRenewing(true)
    try {
      const r = await api.services.renewCert(id ?? "")
      if (r.needs_force) {
        setConfirmForceRenew(true)
      } else {
        showActionMsg(r.message)
        void refresh({ background: true })
      }
    } catch (e) {
      showActionMsg(errorMessage(e))
    } finally {
      setRenewing(false)
    }
  }

  const handleForceRenewCert = async () => {
    setConfirmForceRenew(false)
    clearActionMsg()
    setRenewing(true)
    try {
      const r = await api.services.renewCert(id ?? "", { force: true })
      showActionMsg(r.message)
      void refresh({ background: true })
    } catch (e) {
      showActionMsg(errorMessage(e))
    } finally {
      setRenewing(false)
    }
  }

  return {
    deleting,
    confirmDelete,
    setConfirmDelete,
    cleanupDns,
    setCleanupDns,
    confirmDisable,
    setConfirmDisable,
    confirmRecreate,
    setConfirmRecreate,
    confirmForceRenew,
    setConfirmForceRenew,
    renewing,
    handleToggleEnabled,
    runAction,
    handleRecreateEdge,
    handleDelete,
    handleRenewCert,
    handleForceRenewCert,
  }
}
