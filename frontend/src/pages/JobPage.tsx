import { useEffect, useMemo, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { Catalog, Job } from '../api/types'
import { LogPanel } from '../components/LogPanel'
import { PlanSummary } from '../components/PlanSummary'
import { StatusBadge } from '../components/StatusBadge'
import { jobHref } from '../hooks/useHashRoute'
import { useJobStream } from '../hooks/useJobStream'

export function JobPage({ jobId }: { jobId: string }) {
  const stream = useJobStream(jobId)
  const [job, setJob] = useState<Job | null>(null)
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [acting, setActing] = useState(false)

  // The stream carries logs and status; details such as the plan summary come
  // from the job itself, so fetch it again whenever the status changes.
  useEffect(() => {
    let active = true
    api
      .getJob(jobId)
      .then((j) => active && setJob(j))
      .catch((e: unknown) => active && e instanceof ApiError && e.status === 404 && setNotFound(true))
    return () => {
      active = false
    }
  }, [jobId, stream.status])

  // Only used to show names instead of ids; the page works without it.
  useEffect(() => {
    api
      .catalog()
      .then(setCatalog)
      .catch(() => undefined)
  }, [])

  const names = useMemo(() => {
    const map: Record<string, string> = {}
    if (catalog) {
      for (const item of [
        ...catalog.zones,
        ...catalog.service_offerings,
        ...catalog.templates,
        ...catalog.networks,
        ...catalog.disk_offerings,
      ])
        map[item.id] = item.name
    }
    return map
  }, [catalog])

  async function act(run: () => Promise<Job>, then?: (result: Job) => void) {
    setActing(true)
    setActionError(null)
    try {
      const result = await run()
      if (then) then(result)
      else setJob(result)
    } catch (e) {
      setActionError(e instanceof Error ? e.message : 'Request failed')
    } finally {
      setActing(false)
    }
  }

  if (notFound) {
    return (
      <section className="card">
        <h2>Job not found</h2>
        <p>
          <a href="#/">Back to the overview</a>
        </p>
      </section>
    )
  }

  const status = stream.status ?? job?.status ?? null
  const error = stream.error ?? job?.error ?? null
  const awaiting = status === 'awaiting_confirmation'
  const isDestroy = job?.kind === 'destroy'
  const req = job?.request
  const name = (id: string) => names[id] ?? id

  return (
    <>
      <p>
        <a href="#/">&larr; All jobs</a>
      </p>
      <section className="card">
        <div className="section-head">
          <h2>
            {req ? req.name : 'Job'}
            {isDestroy && <span className="badge badge-destroy">destroy</span>}
          </h2>
          {status && <StatusBadge status={status} />}
        </div>
        {job && req && (
          <dl className="facts">
            <dt>Instances</dt>
            <dd>{req.instance_count}</dd>
            <dt>Zone / network</dt>
            <dd>
              {name(req.zone)} / {name(req.network)}
            </dd>
            <dt>Offering</dt>
            <dd>{name(req.service_offering)}</dd>
            <dt>Template</dt>
            <dd>{name(req.template)}</dd>
            <dt>Root disk</dt>
            <dd>{req.disk_size_gb} GB</dd>
            <dt>SSH keypair</dt>
            <dd>{req.keypair ?? 'none'}</dd>
            {req.data_disk_offering && (
              <>
                <dt>Data disk</dt>
                <dd>
                  {name(req.data_disk_offering)}
                  {req.data_disk_size_gb ? `, ${req.data_disk_size_gb} GB` : ''}
                </dd>
              </>
            )}
            {req.group && (
              <>
                <dt>Group</dt>
                <dd>{req.group}</dd>
              </>
            )}
            {Object.keys(req.tags).length > 0 && (
              <>
                <dt>Tags</dt>
                <dd>
                  {Object.entries(req.tags)
                    .map(([k, v]) => `${k}=${v}`)
                    .join(', ')}
                </dd>
              </>
            )}
            <dt>User data</dt>
            <dd>{req.user_data === null ? 'none' : 'provided (hidden)'}</dd>
            {!req.start_vm && (
              <>
                <dt>After creation</dt>
                <dd>left stopped</dd>
              </>
            )}
            <dt>Started</dt>
            <dd>{new Date(job.created_at).toLocaleString()}</dd>
            {job.source_job_id && (
              <>
                <dt>Destroys</dt>
                <dd>
                  <a href={jobHref(job.source_job_id)}>the instances of this job</a>
                </dd>
              </>
            )}
            <dt>Job ID</dt>
            <dd>
              <code>{job.id}</code>
            </dd>
          </dl>
        )}
        {status === 'failed' && error && <p className="error-box">Failed: {error}</p>}
        {status === 'failed' && job?.destroyable && (
          <p className="muted">The apply may have created some instances before it failed. You can destroy them below.</p>
        )}
        {status === 'done' && !isDestroy && !job?.destroyed_at && (
          <p className="ok-box">Apply finished. The instances are listed on the overview.</p>
        )}
        {status === 'done' && isDestroy && <p className="ok-box">The instances were destroyed.</p>}
        {job?.destroyed_at && (
          <p className="muted">These instances were destroyed on {new Date(job.destroyed_at).toLocaleString()}.</p>
        )}
        {status === 'cancelled' && <p className="muted">Cancelled, rejected or expired. Nothing was changed.</p>}

        {(status === 'queued' || status === 'planning') && (
          <div className="actions">
            <button disabled={acting} onClick={() => void act(() => api.cancelJob(jobId))}>
              Cancel
            </button>
          </div>
        )}
        {job?.destroyable && (
          <div className="actions">
            <button
              disabled={acting}
              onClick={() => void act(() => api.destroyJob(jobId), (d) => (window.location.hash = jobHref(d.id)))}
            >
              Destroy these instances...
            </button>
            <span className="muted">Creates a destroy plan for you to review first.</span>
          </div>
        )}
        {!awaiting && actionError && <p className="error-box">{actionError}</p>}
      </section>

      {job?.plan_summary && (
        <section className={awaiting ? (isDestroy ? 'card card-danger' : 'card card-attention') : 'card'}>
          <h2>{awaiting ? 'Review the plan' : 'Plan'}</h2>
          <PlanSummary plan={job.plan_summary} names={names} />
          {awaiting && (
            <>
              <div className="actions">
                <button
                  className={isDestroy ? 'danger' : 'primary'}
                  disabled={acting}
                  onClick={() => void act(() => api.confirmJob(jobId))}
                >
                  {isDestroy ? 'Confirm and destroy' : 'Confirm and apply'}
                </button>
                <button disabled={acting} onClick={() => void act(() => api.cancelJob(jobId))}>
                  Reject plan
                </button>
                <span className="muted">
                  {isDestroy ? 'Nothing has been destroyed yet.' : 'Nothing has been created yet.'} Unconfirmed plans
                  expire.
                </span>
              </div>
              {actionError && <p className="error-box">{actionError}</p>}
            </>
          )}
        </section>
      )}

      <section className="card">
        <LogPanel lines={stream.lines} connection={stream.connection} />
      </section>
    </>
  )
}
