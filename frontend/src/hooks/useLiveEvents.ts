import { useEffect, useRef, useState } from 'react'

import { api, apiBase } from '../api/client'

export interface LiveEvent {
  type: string
  timestamp: string
  data: Record<string, unknown>
}

/**
 * Server-Sent Events subscription for the live dashboard.
 *
 * EventSource cannot send an Authorization header, so a short-lived,
 * stream-scoped token is fetched first and passed in the query string. That
 * token has a five-minute lifetime and no API authority beyond the stream.
 *
 * Reconnection uses exponential backoff with a cap: a backend restart should
 * not turn every open dashboard into a reconnect storm.
 */
export function useLiveEvents(enabled: boolean, onEvent: (event: LiveEvent) => void) {
  const [connected, setConnected] = useState(false)
  const sourceRef = useRef<EventSource | null>(null)
  const attemptRef = useRef(0)
  const timerRef = useRef<number | undefined>(undefined)
  const handlerRef = useRef(onEvent)

  handlerRef.current = onEvent

  useEffect(() => {
    if (!enabled) return
    let cancelled = false

    const connect = async () => {
      if (cancelled) return
      try {
        const { streamToken } = await api.streamToken()
        if (cancelled) return

        const source = new EventSource(
          `${apiBase}/stream?access_token=${encodeURIComponent(streamToken)}`,
        )
        sourceRef.current = source

        source.onopen = () => {
          attemptRef.current = 0
          setConnected(true)
        }

        const forward = (event: MessageEvent) => {
          try {
            handlerRef.current(JSON.parse(event.data) as LiveEvent)
          } catch {
            /* a malformed frame must not kill the stream */
          }
        }

        for (const type of [
          'customer.created',
          'check.started',
          'check.completed',
          'check.error',
          'customer.location_updated',
          'service_location.updated',
          'config.updated',
          'import.completed',
        ]) {
          source.addEventListener(type, forward as EventListener)
        }
        source.addEventListener('heartbeat', () => setConnected(true))

        source.onerror = () => {
          setConnected(false)
          source.close()
          sourceRef.current = null
          if (cancelled) return
          attemptRef.current += 1
          const delay = Math.min(30_000, 1000 * 2 ** (attemptRef.current - 1))
          timerRef.current = window.setTimeout(connect, delay)
        }
      } catch {
        if (cancelled) return
        attemptRef.current += 1
        const delay = Math.min(30_000, 1000 * 2 ** (attemptRef.current - 1))
        timerRef.current = window.setTimeout(connect, delay)
      }
    }

    void connect()

    return () => {
      cancelled = true
      window.clearTimeout(timerRef.current)
      sourceRef.current?.close()
      sourceRef.current = null
      setConnected(false)
    }
  }, [enabled])

  return { connected }
}
