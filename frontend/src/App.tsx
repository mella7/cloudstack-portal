import { useEffect, useState } from 'react'
import { api } from './api/client'
import { useHashRoute } from './hooks/useHashRoute'
import { HomePage } from './pages/HomePage'
import { JobPage } from './pages/JobPage'

export default function App() {
  const route = useHashRoute()
  const [dryRun, setDryRun] = useState(false)

  useEffect(() => {
    api
      .health()
      .then((h) => setDryRun(h.dry_run))
      .catch(() => undefined)
  }, [])

  return (
    <>
      <header className="topbar">
        <a href="#/" className="brand">
          CloudStack provisioning portal
        </a>
        {dryRun && <span className="badge badge-dry">DRY_RUN - nothing real is created</span>}
      </header>
      <main>
        {route.page === 'job' ? <JobPage key={route.jobId} jobId={route.jobId} /> : <HomePage />}
      </main>
    </>
  )
}
