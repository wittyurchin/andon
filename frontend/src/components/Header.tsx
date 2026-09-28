import { clockTime, countdown, relativeTime } from '../lib/format'
import type { RestaurantConfig, SituationResult } from '../types'
import { RefreshSelect } from './RefreshSelect'

interface Props {
  config: RestaurantConfig
  result: SituationResult | null
  loading: boolean
  now: number
  lastFetchedAt: number | null
  nextRefreshAt: number | null
  deferred: boolean
  onRefresh: () => void
  onRefreshMsChange: (value: number) => void
  onEdit: () => void
}

export function Header({
  config,
  result,
  loading,
  now,
  lastFetchedAt,
  nextRefreshAt,
  deferred,
  onRefresh,
  onRefreshMsChange,
  onEdit,
}: Props) {
  const generatedAt = result?.situation.generated_at ?? null
  const freshnessClass = staleness(lastFetchedAt, now, config.refreshMs)

  return (
    <header className="header">
      <div className="header__identity">
        <p className="eyebrow">Andon · Restaurant situation</p>
        <h1 className="header__name">{result?.situation.restaurant.name ?? config.name}</h1>
        <p className="header__coords">
          <span className="mono">{config.lat.toFixed(5)}, {config.lon.toFixed(5)}</span>
          <button className="link" onClick={onEdit} type="button">
            Change location
          </button>
        </p>
      </div>

      <div className="header__controls">
        <div className="header__clock">
          <p className={`header__updated ${freshnessClass}`}>
            <span className="dot" aria-hidden="true" />
            {loading ? 'Updating…' : `Updated ${relativeTime(generatedAt, now)}`}
          </p>
          <p className="header__timing">
            <span>Last {clockTime(generatedAt)}</span>
            <span>
              {config.refreshMs === 0
                ? 'Next: manual only'
                : deferred
                  ? 'Next: paused (tab hidden)'
                  : nextRefreshAt
                    ? `Next ${clockTime(new Date(nextRefreshAt).toISOString())} (in ${countdown(nextRefreshAt - now)})`
                    : 'Next: scheduling…'}
            </span>
          </p>
        </div>
        <div className="header__buttons">
          <RefreshSelect value={config.refreshMs} onChange={onRefreshMsChange} />
          <button
            className="button button--primary"
            onClick={onRefresh}
            disabled={loading}
            type="button"
          >
            {loading ? 'Refreshing…' : 'Refresh now'}
          </button>
        </div>
      </div>
    </header>
  )
}

function staleness(lastFetchedAt: number | null, now: number, refreshMs: number): string {
  if (!lastFetchedAt) return 'is-unknown'
  const age = now - lastFetchedAt
  const limit = refreshMs > 0 ? refreshMs * 2 : 15 * 60_000
  if (age > limit) return 'is-stale'
  if (age > limit / 2) return 'is-ageing'
  return 'is-fresh'
}
