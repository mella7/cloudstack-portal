import { useEffect, useRef } from 'react'
import type { LogLine } from '../api/types'
import type { Connection } from '../hooks/useJobStream'

const CONNECTION_LABEL: Record<Connection, string> = {
  connecting: 'connecting...',
  open: 'live',
  reconnecting: 'connection lost, reconnecting...',
  closed: 'stream closed',
}

export function LogPanel({ lines, connection }: { lines: LogLine[]; connection: Connection }) {
  const box = useRef<HTMLDivElement>(null)
  const stickToBottom = useRef(true)

  useEffect(() => {
    const el = box.current
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight
  }, [lines.length])

  return (
    <section>
      <div className="section-head">
        <h2>Terraform log</h2>
        <span className={`conn conn-${connection}`}>{CONNECTION_LABEL[connection]}</span>
      </div>
      <div
        className="log"
        ref={box}
        role="log"
        aria-live="off"
        tabIndex={0}
        onScroll={(e) => {
          // Only follow new output while the user is at the bottom.
          const el = e.currentTarget
          stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24
        }}
      >
        {lines.length === 0 && <span className="muted">No output yet.</span>}
        {lines.map((l) => (
          <div key={l.seq} className={lineClass(l.line)}>
            <span className="log-time">{new Date(l.ts).toLocaleTimeString()}</span>
            {l.line || ' '}
          </div>
        ))}
      </div>
    </section>
  )
}

function lineClass(line: string): string {
  if (/^(ERROR|Error)\b/.test(line)) return 'log-line log-error'
  if (/^\[dry-run\] \$|^\$ /.test(line)) return 'log-line log-cmd'
  if (/^(Plan:|Apply complete!|Success!)/.test(line)) return 'log-line log-ok'
  return 'log-line'
}
