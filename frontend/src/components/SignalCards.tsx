import {
  FRESHNESS_LABEL,
  KIND_LABEL,
  SEVERITY_LABEL,
  TREND_ARROW,
  TREND_LABEL,
  formatFacetValue,
  relativeTime,
} from '../lib/format'
import type { NormalizedSituation, SignalAssessment } from '../types'
import type { InspectorFilter } from './EvidenceInspector'

const TITLES: Record<string, string> = {
  weather: 'Weather',
  traffic: 'Traffic',
  road_conditions: 'Road conditions',
}

const BASIS_LABEL: Record<string, string> = {
  observation: 'Observed',
  model: 'Model',
  simulated: 'Simulated',
  unknown: '',
}

export function SignalCards({
  situation,
  now,
  onInspect,
}: {
  situation: NormalizedSituation
  now: number
  onInspect: (filter: InspectorFilter) => void
}) {
  // When several providers cover a signal, each gets its own tile and the
  // consolidated view is represented by the disagreement banner instead — one
  // tile per source is more useful than a tile that hides the conflict.
  const weatherTiles = situation.weather_sources.length > 0 ? situation.weather_sources : [situation.weather]
  const trafficTiles = situation.traffic_sources.length > 0 ? situation.traffic_sources : [situation.traffic]

  return (
    <>
      {[
        { signal: situation.weather, filter: 'weather' as const },
        { signal: situation.traffic, filter: 'traffic' as const },
      ]
        .filter(({ signal }) => signal.disagreement)
        .map(({ signal, filter }) => (
          <p key={filter} className="disagreement" role="status">
            <strong>Sources disagree ({TITLES[signal.kind].toLowerCase()}):</strong> {signal.disagreement}.
            {' '}The nearest reliable source is used for scoring and confidence is lowered.{' '}
            <button type="button" className="link link--inline" onClick={() => onInspect(filter)}>
              Inspect the evidence
            </button>
          </p>
        ))}
      <section className="cards">
        {weatherTiles.map((signal, index) => (
          <SignalCard key={`weather-${signal.source_label ?? index}`} signal={signal} now={now} />
        ))}
        {trafficTiles.map((signal, index) => (
          <SignalCard key={`traffic-${signal.source_label ?? index}`} signal={signal} now={now} />
        ))}
        <SignalCard signal={situation.road_conditions} now={now} />
      </section>
    </>
  )
}

function SignalCard({ signal, now }: { signal: SignalAssessment; now: number }) {
  const unavailable = signal.status === 'unavailable'
  const mocked = signal.sources.some((s) => s.mode === 'mock')
  const basis = BASIS_LABEL[signal.basis]
  const stale = signal.freshness === 'stale'
  const labelled = signal.kind !== 'road_conditions' && signal.source_label

  return (
    <article className={`card ${unavailable ? 'card--down' : `sev-${signal.severity}`} ${stale ? 'card--stale' : ''}`}>
      <header className="card__head">
        <h3 className="card__title">
          {TITLES[signal.kind]}
          {labelled && <span className="card__source-label">{signal.source_label}</span>}
        </h3>
        {unavailable ? (
          <span className="tag tag--down">Source down</span>
        ) : (
          <span className="tag">{SEVERITY_LABEL[signal.severity]}</span>
        )}
      </header>

      {!unavailable && basis && (
        <span className={`basis basis--${mocked ? 'simulated' : signal.basis}`}>
          {mocked ? `Simulated ${basis.toLowerCase()}` : basis}
        </span>
      )}

      <p className="card__headline">{signal.headline}</p>
      {signal.detail && <p className="card__detail">{signal.detail}</p>}

      {unavailable ? (
        <p className="card__error" title={signal.unavailable_reason ?? undefined}>
          {signal.unavailable_reason}
        </p>
      ) : (
        <>
          <div className={`card__trend trend-${signal.trend}`}>
            <p className="card__trend-main">
              <span aria-hidden="true">{TREND_ARROW[signal.trend]}</span>
              {TREND_LABEL[signal.trend]}
            </p>
            {signal.trend_note && <p className="card__trend-note">{signal.trend_note}</p>}
          </div>

          {signal.facets.filter((f) => f.available).length > 0 && (
            <dl className="facets">
              {signal.facets
                .filter((facet) => facet.available)
                .slice(0, 4)
                .map((facet) => (
                  <div key={facet.key} className={`facet sev-text-${facet.severity}`}>
                    <dt>
                      {facet.label}
                      {facet.kind !== 'observed' && (
                        <span className={`kind kind--${facet.kind}`}>{KIND_LABEL[facet.kind]}</span>
                      )}
                    </dt>
                    <dd title={facet.note ?? undefined}>{formatFacetValue(facet.value, facet.unit)}</dd>
                  </div>
                ))}
            </dl>
          )}
        </>
      )}

      <footer className="card__foot">
        <span className={`freshness freshness--${signal.freshness}`}>
          {unavailable
            ? 'No data'
            : `${stale ? 'STALE · ' : ''}Updated ${relativeTime(signal.observed_at, now)} · ${FRESHNESS_LABEL[signal.freshness]}`}
        </span>
        {!unavailable && <span className={`confidence confidence--${signal.confidence}`}>{signal.confidence}</span>}
        <span className="card__source">
          {signal.sources.map((s) => s.name).join(', ')}
          {mocked && <span className="tag tag--mock">mock</span>}
        </span>
      </footer>

      {signal.coverage_gaps.length > 0 && <p className="card__gap">Not covered: {signal.coverage_gaps.join('; ')}</p>}
    </article>
  )
}
