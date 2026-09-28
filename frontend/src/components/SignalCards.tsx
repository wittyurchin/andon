import { FRESHNESS_LABEL, TREND_ARROW, TREND_LABEL, formatFacetValue, relativeTime } from '../lib/format'
import type { NormalizedSituation, SignalAssessment } from '../types'
import type { InspectorFilter } from './EvidenceInspector'
import { Mark, Pips, markForKind, type Epistemic } from './marks'

const TITLES: Record<string, string> = {
  weather: 'Weather',
  traffic: 'Traffic',
  road_conditions: 'Road conditions',
}

const BASIS_MARK: Record<string, Epistemic | null> = {
  observation: 'measured',
  model: 'model',
  simulated: 'simulated',
  unknown: null,
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
  const disagreements = [
    { signal: situation.weather, filter: 'weather' as const },
    { signal: situation.traffic, filter: 'traffic' as const },
  ].filter(({ signal }) => signal.disagreement)

  return (
    <section className="signals">
      <header className="signals__head">
        <h2 className="section-title">Signals</h2>
        <p className="section-meta">One card per source, so a conflict stays visible instead of averaged away.</p>
      </header>

      {disagreements.map(({ signal, filter }) => (
        <div key={filter} className="disagreement" role="status">
          <span className="disagreement__tag">Sources disagree</span>
          <p>
            <strong>{TITLES[signal.kind]}:</strong> {signal.disagreement}. The nearest reliable source is used for
            scoring and confidence is lowered.
          </p>
          <button type="button" className="button button--quiet" onClick={() => onInspect(filter)}>
            Inspect the evidence
          </button>
        </div>
      ))}

      <div className="cards">
        {weatherTiles.map((signal, index) => (
          <SignalCard key={`weather-${signal.source_label ?? index}`} signal={signal} now={now} />
        ))}
        {trafficTiles.map((signal, index) => (
          <SignalCard key={`traffic-${signal.source_label ?? index}`} signal={signal} now={now} />
        ))}
        <SignalCard signal={situation.road_conditions} now={now} />
      </div>
    </section>
  )
}

function SignalCard({ signal, now }: { signal: SignalAssessment; now: number }) {
  const unavailable = signal.status === 'unavailable'
  const mocked = signal.sources.some((s) => s.mode === 'mock')
  const basis = mocked ? 'simulated' : BASIS_MARK[signal.basis]
  const stale = signal.freshness === 'stale'
  const labelled = signal.kind !== 'road_conditions' && signal.source_label
  const facets = signal.facets.filter((f) => f.available).slice(0, 4)

  return (
    <article className={`card ${unavailable ? 'card--down' : `sev-${signal.severity}`} ${stale ? 'card--stale' : ''}`}>
      <header className="card__head">
        <h3 className="card__title">
          {TITLES[signal.kind]}
          {labelled && <span className="card__source-label">{signal.source_label}</span>}
        </h3>
        {unavailable ? <span className="tag tag--down">Source down</span> : <Pips level={signal.severity} />}
      </header>

      {!unavailable && basis && <Mark kind={basis} />}

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

          {facets.length > 0 && (
            <dl className="facets">
              {facets.map((facet) => (
                <div key={facet.key} className={`facet sev-text-${facet.severity}`}>
                  <dt>
                    {facet.label}
                    {facet.kind !== 'observed' && <Mark kind={markForKind(facet.kind, mocked)} />}
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
            : `${stale ? 'STALE · ' : ''}${relativeTime(signal.observed_at, now)} · ${FRESHNESS_LABEL[signal.freshness]}`}
        </span>
        {!unavailable && <span className={`confidence confidence--${signal.confidence}`}>{signal.confidence} confidence</span>}
        <span className="card__source">
          {signal.sources.map((s) => s.name).join(', ')}
          {mocked && <span className="tag tag--mock">mock</span>}
        </span>
      </footer>

      {signal.coverage_gaps.length > 0 && <p className="card__gap">Not covered: {signal.coverage_gaps.join('; ')}</p>}
    </article>
  )
}
