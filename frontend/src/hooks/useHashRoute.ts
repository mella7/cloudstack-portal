import { useSyncExternalStore } from 'react'

function subscribe(onChange: () => void) {
  window.addEventListener('hashchange', onChange)
  return () => window.removeEventListener('hashchange', onChange)
}

/** Tiny hash router: "#/jobs/<id>" survives a page reload without server config. */
export function useHashRoute(): { page: 'home' } | { page: 'job'; jobId: string } {
  const hash = useSyncExternalStore(subscribe, () => window.location.hash)
  const match = /^#\/jobs\/([A-Za-z0-9-]+)$/.exec(hash)
  return match?.[1] ? { page: 'job', jobId: match[1] } : { page: 'home' }
}

export const jobHref = (jobId: string) => `#/jobs/${jobId}`
