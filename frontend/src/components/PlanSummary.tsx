import type { PlanSummary as Plan } from '../api/types'

const KINDS: Record<string, string> = { cloudstack_instance: 'instance', cloudstack_disk: 'data disk' }
const size = (gb: string | undefined) => (gb ? `${gb} GB` : '-')

/** `names` maps catalog ids to readable names, so the review does not show bare UUIDs. */
export function PlanSummary({ plan, names }: { plan: Plan; names: Record<string, string> }) {
  const show = (value: string | undefined) => (value === undefined ? '-' : (names[value] ?? value))
  return (
    <div>
      <p className="plan-counts">
        <strong className={plan.add > 0 ? 'add' : undefined}>{plan.add} to add</strong>,{' '}
        <strong>{plan.change} to change</strong>,{' '}
        <strong className={plan.destroy > 0 ? 'destroy' : undefined}>{plan.destroy} to destroy</strong>
      </p>
      {plan.resources.length === 0 ? (
        <p className="muted">Terraform found nothing to change.</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Action</th>
              <th>Kind</th>
              <th>Name</th>
              <th>Offering</th>
              <th>Template</th>
              <th>Size</th>
              <th>Keypair</th>
              <th>Starts</th>
            </tr>
          </thead>
          <tbody>
            {plan.resources.map((r) => (
              <tr key={r.address} title={r.address}>
                <td className={`action-${r.action}`}>{r.action}</td>
                <td>{KINDS[r.type] ?? r.type}</td>
                <td>{r.details.name ?? <code>{r.address}</code>}</td>
                <td>{show(r.details.service_offering ?? r.details.disk_offering)}</td>
                <td>{show(r.details.template)}</td>
                <td>{size(r.details.root_disk_size ?? r.details.size)}</td>
                <td>{show(r.details.keypair)}</td>
                <td>{r.details.start_vm === undefined ? '-' : r.details.start_vm === 'true' ? 'yes' : 'no'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
