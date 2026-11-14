import type { JobStatus } from '../api/types'

const LABELS: Record<JobStatus, string> = {
  queued: 'Queued',
  planning: 'Planning',
  awaiting_confirmation: 'Waiting for confirmation',
  applying: 'Applying',
  done: 'Done',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

export function StatusBadge({ status }: { status: JobStatus }) {
  const busy = status === 'planning' || status === 'applying' || status === 'queued'
  return (
    <span className={`badge status-${status}`}>
      {busy && <span className="spinner" aria-hidden="true" />}
      {LABELS[status]}
    </span>
  )
}
