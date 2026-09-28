import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchSituation } from '../api'
import type { RestaurantConfig, SituationResponse } from '../types'

export interface SituationState {
  data: SituationResponse | null
  error: string | null
  loading: boolean
  lastFetchedAt: number | null
  nextRefreshAt: number | null
  /** Auto-refresh came due while the tab was hidden; we skipped the call. */
  deferred: boolean
  now: number
  refresh: (force?: boolean) => void
}

/**
 * Fetches the situation and drives auto-refresh.
 *
 * Three ways this fires, three different requests:
 * - page load (mount): `poll: false` — replays the last polled response
 *   verbatim, never contacts a provider.
 * - auto-refresh timer / tab-visibility catch-up: `poll: true, force: false`
 *   — polls, but each provider still respects its own cache TTL.
 * - the "Refresh Now" button: `poll: true, force: true` — polls and bypasses
 *   every provider's cache.
 *
 * Refreshes are suppressed while the tab is hidden — an unattended dashboard
 * should not keep billing external APIs — and fire immediately on return if
 * the interval elapsed.
 *
 * Concurrency rule: a new request always supersedes an in-flight one, and only
 * the newest request may write state. Refusing to start while another request
 * is in flight is what previously deadlocked the spinner, because an aborted
 * request would leave `loading` set with nothing left to clear it.
 */
export function useSituation(config: RestaurantConfig | null): SituationState {
  const [data, setData] = useState<SituationResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [lastFetchedAt, setLastFetchedAt] = useState<number | null>(null)
  const [deferred, setDeferred] = useState(false)
  const [now, setNow] = useState(() => Date.now())

  const abortRef = useRef<AbortController | null>(null)
  const configRef = useRef(config)
  configRef.current = config

  const load = useCallback(async (opts: { force?: boolean; poll?: boolean } = {}) => {
    const current = configRef.current
    if (!current) return

    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    setLoading(true)

    // True only while this is still the newest request.
    const owns = () => abortRef.current === controller

    try {
      const result = await fetchSituation(current, { ...opts, signal: controller.signal })
      if (!owns()) return
      setData(result)
      setError(null)
      setDeferred(false)
    } catch (err) {
      if (!owns() || (err as Error).name === 'AbortError') return
      setError((err as Error).message)
    } finally {
      // A superseded request must not clear the flag — that would hide the
      // request that replaced it. The newest one always clears it, including
      // on failure, so a dead backend surfaces an error instead of a spinner.
      if (owns()) {
        // Recorded even on failure so a broken backend is retried on the
        // normal cadence rather than in a tight loop.
        setLastFetchedAt(Date.now())
        setLoading(false)
      }
    }
  }, [])

  const configKey = config ? `${config.name}|${config.lat}|${config.lon}` : null

  // Load on mount and whenever the restaurant changes — a page load, so it
  // never contacts a provider: it just replays whatever was last polled.
  useEffect(() => {
    if (!configKey) return
    setData(null)
    setLastFetchedAt(null)
    void load({ poll: false })
  }, [configKey, load])

  // One ticker drives both the countdown display and the refresh check.
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(id)
  }, [])

  const refreshMs = config?.refreshMs ?? 0
  const nextRefreshAt = refreshMs > 0 && lastFetchedAt ? lastFetchedAt + refreshMs : null

  useEffect(() => {
    if (!nextRefreshAt || now < nextRefreshAt || loading) return
    if (document.hidden) {
      setDeferred(true)
      return
    }
    void load({ poll: true, force: false })
  }, [now, nextRefreshAt, loading, load])

  // Catch up as soon as the tab is looked at again.
  useEffect(() => {
    const onVisible = () => {
      if (document.hidden) return
      setNow(Date.now())
      if (nextRefreshAt && Date.now() >= nextRefreshAt) void load({ poll: true, force: false })
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [nextRefreshAt, load])

  useEffect(() => () => abortRef.current?.abort(), [])

  return {
    data,
    error,
    loading,
    lastFetchedAt,
    nextRefreshAt,
    deferred,
    now,
    // The only manual trigger: always polls, and force=true bypasses caches.
    refresh: (force?: boolean) => void load({ poll: true, force: !!force }),
  }
}
