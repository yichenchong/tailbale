/**
 * Definition-list row: a muted label on the left, a value on the right. Shared by
 * the ServiceDetail Configuration (read view) and Runtime panels. `valueClassName`
 * defaults to the standard emphasized value styling but can be overridden (e.g.
 * the cert-expiry row colors the value by urgency via {@link formatCertExpiry}).
 */
export function Row({
  label,
  value,
  valueClassName = "font-medium text-zinc-700",
}: {
  label: string
  value: string
  valueClassName?: string
}) {
  return (
    <div className="flex justify-between">
      <dt className="text-zinc-500">{label}</dt>
      <dd className={valueClassName}>{value}</dd>
    </div>
  )
}
