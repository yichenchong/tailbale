import { api, type ServiceItem } from "@/lib/api"
import { type ServiceLifecycleActionKey } from "@/lib/serviceTypes"

export interface ServiceLifecycleAction<TResult = unknown> {
  key: ServiceLifecycleActionKey
  label: string
  run: () => Promise<TResult>
}

export interface ServiceLifecycleActions {
  enable: ServiceLifecycleAction<ServiceItem>
  disable: ServiceLifecycleAction<ServiceItem>
  reload: ServiceLifecycleAction
  restart: ServiceLifecycleAction
  recreate: ServiceLifecycleAction
  reconcile: ServiceLifecycleAction
}

export function serviceLifecycleActions(serviceId: string): ServiceLifecycleActions {
  return {
    enable: {
      key: "enable",
      label: "Enable",
      run: () => api.services.update(serviceId, { enabled: true }),
    },
    disable: {
      key: "disable",
      label: "Disable",
      run: () => api.services.disable(serviceId),
    },
    reload: {
      key: "reload",
      label: "Reload Caddy",
      run: () => api.services.reload(serviceId),
    },
    restart: {
      key: "restart",
      label: "Restart Edge",
      run: () => api.services.restartEdge(serviceId),
    },
    recreate: {
      key: "recreate",
      label: "Recreate Edge",
      run: () => api.services.recreateEdge(serviceId),
    },
    reconcile: {
      key: "reconcile",
      label: "Re-run Reconcile",
      run: () => api.services.reconcile(serviceId),
    },
  }
}
