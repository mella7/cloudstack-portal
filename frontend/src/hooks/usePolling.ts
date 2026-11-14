import { useCallback, useEffect, useRef, useState } from 'react'

/** Calls `load` now and then every `intervalMs`. `reload` triggers an extra call. */
export function usePolling<T>(load: () => Promise<T>, intervalMs: number) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const loadRef = useRef(load)
  useEffect(() => {
    loadRef.current = load
  })

  const reload = useCallback(async () => {
    try {
      setData(await loadRef.current())
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Request failed')
    }
  }, [])

  useEffect(() => {
    let active = true
    const tick = () => {
      if (active) void reload()
    }
    const first = setTimeout(tick, 0)
    const timer = setInterval(tick, intervalMs)
    return () => {
      active = false
      clearTimeout(first)
      clearInterval(timer)
    }
  }, [reload, intervalMs])

  return { data, error, reload }
}
