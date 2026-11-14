import type { Job } from '../api/types'
import { jobHref } from '../hooks/useHashRoute'
import { StatusBadge } from './StatusBadge'

export function JobList({ jobs }: { jobs: Job[] | null }) {
  if (!jobs) return <p className="muted">Loading jobs...</p>
  if (jobs.length === 0) return <p className="muted">No jobs yet. Fill in the form to create the first plan.</p>
  return (
    <table>
      <thead>
        <tr>
          <th>Name</th>
          <th>Instances</th>
          <th>Status</th>
          <th>Created</th>
        </tr>
      </thead>
      <tbody>
        {jobs.map((job) => (
          <tr key={job.id}>
            <td>
              <a href={jobHref(job.id)}>{job.request.name}</a>
              {job.kind === 'destroy' && <span className="badge badge-destroy">destroy</span>}
              {job.destroyed_at && <span className="muted"> (destroyed)</span>}
            </td>
            <td>{job.request.instance_count}</td>
            <td>
              <StatusBadge status={job.status} />
            </td>
            <td>{new Date(job.created_at).toLocaleString()}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
