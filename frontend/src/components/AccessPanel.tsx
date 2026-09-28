import { corridorStates } from '../lib/corridors'
import { distance } from '../lib/evidence'
import { TREND_ARROW } from '../lib/format'
import type { EvidenceBundle, Observation, Trend } from '../types'
import { Mark, Pips, markFor } from './marks'

/**
 * Which roads into the kitchen are affected, corridor by corridor. This is the
 * operational view of traffic and incidents: "the north approach is jammed"
 * is actionable where "traffic is heavy" is not.
 */
export function AccessPanel({ evidence }: { evidence: EvidenceBundle }) {
  const graph = evidence.access_graph
  if (!graph) return null
  const states = corridorStates(evidence)

  return (
    <section className="panel">
      <header className="panel__head">
        <h2 className="panel__title">Access roads</h2>
        <span className="panel__meta" title={graph.derivation}>
          {graph.source === 'osm' && 'Derived from OpenStreetMap roads'}
          {graph.source === 'radial' && 'Radial approximation: road data unavailable'}
          {graph.source === 'configured' && 'Operator-configured points'}
          {graph.source === 'mock' && 'Simulated road network'}
          {' · '}
          {graph.approaches.length} approaches
        </span>
      </header>

      {graph.source === 'radial' && (
        <p className="access__warning">
          These are points at a fixed distance and bearing, not roads. Traffic sampled at them may not
          reflect how riders actually reach the kitchen.
          {graph.notes[0] ? ` (${graph.notes[0]})` : ''}
        </p>
      )}

      <ul className="access">
        {graph.approaches.map((approach) => {
          const { readings, incidents, level } = states[approach.id]
          const trend = evidence.trends.find(
            (t) => t.subject_id === approach.id && (t.category === 'traffic.flow' || t.category === 'traffic.corridor_eta'),
          )

          return (
            <li key={approach.id} className={`access__item ${level ? `sev-${level}` : 'is-unknown'}`}>
              <div className="access__head">
                <span className="access__label">{approach.label}</span>
                {level ? <Pips level={level} /> : <span className="tag tag--unknown">Unknown</span>}
              </div>
              <span className="access__meta">
                {approach.road_class ?? 'unclassified'} · nearest point {distance(approach.distance_m)}
                {approach.is_approximation && <span className="tag tag--mock">approximation</span>}
              </span>

              {readings.length === 0 ? (
                <p className="access__none">No traffic reading for this road: unknown, not clear.</p>
              ) : (
                readings.map((o) => <Reading key={o.id} o={o} />)
              )}

              {trend && (
                <p className={`access__trend trend-${trend.direction as Trend}`}>
                  {TREND_ARROW[trend.direction as Trend]} {trend.direction} over {trend.window_minutes} min
                  ({trend.points} readings)
                </p>
              )}

              {incidents.map((i) => (
                <p key={i.id} className="access__incident">
                  <span className="access__incident-type">{i.incident_type.replace(/_/g, ' ')} on this road</span>
                  {i.description}
                  <span className="access__src">
                    {i.source_name}
                    {i.source_type === 'mock' && ' (mock)'}
                  </span>
                </p>
              ))}
            </li>
          )
        })}
      </ul>

      {graph.attribution && <p className="attribution">{graph.attribution}</p>}
    </section>
  )
}

function Reading({ o }: { o: Observation }) {
  const v = o.value
  const text =
    o.category === 'traffic.corridor_eta'
      ? `${Math.round(Number(v.travel_time_s))} s along the road (no free-flow reference)`
      : v.congestion_index === null || v.congestion_index === undefined
        ? `${v.current_speed_kmh ?? '—'} km/h, no free-flow reference`
        : `${v.band} · ${Math.round(Number(v.current_speed_kmh))} of ${Math.round(Number(v.free_flow_speed_kmh))} km/h free-flow`
  return (
    <p className={`access__reading sev-text-${(v.band as string) ?? 'none'}`}>
      <Mark kind={markFor(o)} compact />
      <span className="access__value">{text}</span>
      <span className="access__src">
        {o.source_name}
        {o.source_type === 'mock' && ' (mock)'}
        {o.stale && ' · stale'}
      </span>
      {typeof v.match_warning === 'string' && <span className="evidence-table__warn">{v.match_warning}</span>}
    </p>
  )
}
