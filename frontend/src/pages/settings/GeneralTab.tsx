import { type AllSettings } from "@/lib/api"
import { isPositiveInt, isNonBlank, isEmailLike, isBaseDomain } from "@/lib/validation"
import { useDirtyForm } from "@/lib/useDirtyForm"
import { Field } from "@/components/settings/Field"
import { SaveButton } from "@/components/settings/SaveButton"
import { type SaveHandler } from "./useSettings"

export function GeneralTab({
  settings,
  onSave,
  saving,
  version,
}: {
  settings: AllSettings["general"]
  onSave: SaveHandler
  saving: boolean
  version: string | null
}) {
  const { values, set, bind, save } = useDirtyForm(settings, (s) => ({
    base_domain: s.base_domain,
    acme_email: s.acme_email,
    reconcile_interval_seconds: String(s.reconcile_interval_seconds),
    health_check_interval_seconds: String(s.health_check_interval_seconds),
    cert_renewal_window_days: String(s.cert_renewal_window_days),
    event_retention_days: String(s.event_retention_days),
    timezone: s.timezone,
    developer_mode: s.developer_mode,
  }))

  const handleSave = () =>
    save(() =>
      onSave({
        base_domain: values.base_domain,
        acme_email: values.acme_email,
        reconcile_interval_seconds: Number(values.reconcile_interval_seconds),
        health_check_interval_seconds: Number(values.health_check_interval_seconds),
        cert_renewal_window_days: Number(values.cert_renewal_window_days),
        event_retention_days: Number(values.event_retention_days),
        timezone: values.timezone,
        developer_mode: values.developer_mode,
      }),
    )

  // Client-side validation is a UX optimization, NOT a correctness dependency:
  // save() surfaces any backend rejection in the error banner regardless (the
  // server is authoritative), so the page never relies on this gate being
  // exhaustive. We mirror the backend constraints only for inline feedback + to
  // skip an obviously-doomed PUT: base_domain -> normalize_base_domain; acme_email
  // -> isEmailLike (same shape the backend enforces); numeric fields -> Field(ge=1),
  // with cert_renewal_window_days additionally capped at Field(le=10000) server-side.
  // Layers on top of the per-field dirty guard above without touching it.
  const baseDomainValid = isBaseDomain(values.base_domain)
  const acmeEmailValid = isEmailLike(values.acme_email)
  const reconcileValid = isPositiveInt(values.reconcile_interval_seconds)
  const healthValid = isPositiveInt(values.health_check_interval_seconds)
  // cert_renewal_window_days mirrors the backend Field(ge=1, le=10000); the upper
  // bound stops an obviously-doomed PUT (backend 422s "less than or equal to 10000").
  const renewalValid =
    isPositiveInt(values.cert_renewal_window_days) && Number(values.cert_renewal_window_days) <= 10000
  const retentionValid = isPositiveInt(values.event_retention_days)
  const formValid =
    baseDomainValid && acmeEmailValid && reconcileValid && healthValid && renewalValid && retentionValid
  // `Intl.supportedValuesOf` is absent on some runtimes; without this guard the
  // .map below throws and crashes the General tab. Always include the current
  // configured zone and UTC (the backend default) so the constrained select can
  // represent the stored value even if the runtime's IANA list omits it.
  const tzOptions = Array.from(
    new Set(
      [
        ...(typeof Intl.supportedValuesOf === "function"
          ? Intl.supportedValuesOf("timeZone")
          : []),
        "UTC",
        settings.timezone,
      ].filter(Boolean),
    ),
  )

  return (
    <div className="space-y-4">
      <Field label="Base Domain" value={values.base_domain} onChange={bind("base_domain")} placeholder="mydomain.com" error={baseDomainValid ? undefined : isNonBlank(values.base_domain) ? "Enter a valid base domain" : "Required — cannot be blank"} />
      <Field label="ACME Email" value={values.acme_email} onChange={bind("acme_email")} type="email" placeholder="admin@mydomain.com" error={acmeEmailValid ? undefined : isNonBlank(values.acme_email) ? "Enter a valid email address" : "Required — cannot be blank"} />
      <Field
        label="Full reconciliation interval (seconds)"
        value={values.reconcile_interval_seconds}
        onChange={bind("reconcile_interval_seconds")}
        type="number"
        hint="How often the reconciler runs a full sweep of all services (default 3600)"
        error={reconcileValid ? undefined : "Must be a whole number of at least 1"}
      />
      <Field
        label="Health check interval (seconds)"
        value={values.health_check_interval_seconds}
        onChange={bind("health_check_interval_seconds")}
        type="number"
        hint="How often the edge runs container health checks"
        error={healthValid ? undefined : "Must be a whole number of at least 1"}
      />
      <Field
        label="Cert Renewal Window (days)"
        value={values.cert_renewal_window_days}
        onChange={bind("cert_renewal_window_days")}
        type="number"
        hint="Renew certs this many days before expiry"
        error={renewalValid ? undefined : "Must be a whole number from 1 to 10000"}
      />
      <Field
        label="Keep event log for (days)"
        value={values.event_retention_days}
        onChange={bind("event_retention_days")}
        type="number"
        hint="Older event-log entries are pruned after this many days"
        error={retentionValid ? undefined : "Must be a whole number of at least 1"}
      />
      <label className="block">
        <span className="text-sm font-medium text-zinc-700">Timezone</span>
        <select
          value={values.timezone}
          onChange={(e) => set("timezone", e.target.value)}
          className="mt-1 block w-full rounded-md border border-zinc-300 px-3 py-2 text-sm shadow-sm focus:border-zinc-500 focus:outline-none focus:ring-1 focus:ring-zinc-500"
        >
          {tzOptions.map((tzName: string) => (
            <option key={tzName} value={tzName}>{tzName.replace(/_/g, " ")}</option>
          ))}
        </select>
        <p className="mt-1 text-xs text-zinc-400">Timezone used for all displayed times</p>
      </label>
      <label className="flex items-start gap-3 rounded-md border border-zinc-200 px-3 py-3">
        <input
          type="checkbox"
          checked={values.developer_mode}
          onChange={(e) => set("developer_mode", e.target.checked)}
          className="mt-0.5 h-4 w-4 rounded border-zinc-300 text-zinc-900 focus:ring-zinc-500"
        />
        <span>
          <span className="block text-sm font-medium text-zinc-700">Developer Mode</span>
          <span className="mt-1 block text-xs text-zinc-400">
            Shows advanced reset controls, including setup reset and full data reset.
          </span>
        </span>
      </label>
      <SaveButton
        saving={saving}
        onClick={handleSave}
        disabled={!formValid}
      />

      <div className="mt-6 border-t border-zinc-200 pt-4">
        <div className="text-sm text-zinc-500">
          <span className="font-medium text-zinc-700">tailBale</span>
          {version ? (
            <span className="ml-2 rounded-full bg-zinc-100 px-2 py-0.5 text-xs font-medium text-zinc-600">
              v{version}
            </span>
          ) : (
            <span className="ml-2 text-xs text-zinc-400">version unknown</span>
          )}
        </div>
      </div>
    </div>
  )
}
