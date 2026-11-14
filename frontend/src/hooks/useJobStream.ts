import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { JobStatus, LogLine } from '../api/types'

export type Connection = 'connecting' | 'open' | 'reconnecting' | 'closed'

export interface JobStream {
  lines: LogLine[]
  status: JobStatus | null
  error: string | null
  connection: Connection
}

/**
 * Follows a job over Server-Sent Events.
 *
 * A fresh connection (including after a page reload) replays the whole log
 * from the backend, then continues live. If the connection drops, EventSource
 * reconnects by itself and sends Last-Event-ID, so the backend resumes after
 * the last line we got. The stream ends with an "end" event once the job is
 * finished. Mount the consumer with key={jobId} so state resets per job.
 */
export function useJobStream(jobId: string): JobStream {
  const [lines, setLines] = useState<LogLine[]>([])
  const [status, setStatus] = useState<JobStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [connection, setConnection] = useState<Connection>('connecting')

  useEffect(() => {
    const source = new EventSource(api.streamUrl(jobId))
    let lastSeq = 0

    source.onopen = () => setConnection('open')

    source.addEventListener('log', (event) => {
      const line = JSON.parse((event as MessageEvent<string>).data) as LogLine
      if (line.seq <= lastSeq) return // already shown, e.g. after a reconnect
      lastSeq = line.seq
      setLines((prev) => [...prev, line])
    })

    source.addEventListener('status', (event) => {
      const data = JSON.parse((event as MessageEvent<string>).data) as { status: JobStatus; error: string | null }
      setStatus(data.status)
      setError(data.error)
    })

    source.addEventListener('end', () => {
      source.close()
      setConnection('closed')
    })

    source.onerror = () => {
      // CLOSED means the browser gave up (for example a 404); otherwise it retries.
      setConnection(source.readyState === EventSource.CLOSED ? 'closed' : 'reconnecting')
    }

    return () => source.close()
  }, [jobId])

  return { lines, status, error, connection }
}
