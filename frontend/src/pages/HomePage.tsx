import { api } from '../api/client'
import { JobList } from '../components/JobList'
import { ProvisionForm } from '../components/ProvisionForm'
import { VmTable } from '../components/VmTable'
import { jobHref } from '../hooks/useHashRoute'
import { usePolling } from '../hooks/usePolling'

export function HomePage() {
  const jobs = usePolling(api.listJobs, 4000)
  const vms = usePolling(api.listVms, 5000)

  return (
    <>
      <section className="card">
        <h2>New instances</h2>
        <ProvisionForm onCreated={(job) => (window.location.hash = jobHref(job.id))} />
      </section>
      <section className="card">
        <h2>Jobs</h2>
        {jobs.error && !jobs.data ? <p className="error-box">{jobs.error}</p> : <JobList jobs={jobs.data} />}
      </section>
      <section className="card">
        <div className="section-head">
          <h2>Virtual machines</h2>
          <span className="muted">Live from the CloudStack API</span>
        </div>
        <VmTable vms={vms.data} error={vms.error} onChanged={() => void vms.reload()} />
      </section>
    </>
  )
}
