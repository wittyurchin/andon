import type { EvidenceBundle, IncidentEvidence, Observation, Severity } from '../types'

const RANK: Record<string, number> = { none: 0, low: 1, medium: 2, high: 3, severe: 4 }

export interface CorridorState {
  readings: Observation[]
  incidents: IncidentEvidence[]
  /** Near this road but not on it: listed, never counted in its level. */
  nearby: IncidentEvidence[]
  /** null when nothing reports on this road: unknown, not clear. */
  level: Severity | null
}

/**
 * One rule, used by both the corridor list and the site compass: the worst
 * traffic band on the road, raised to high if an active incident sits on it.
 */
export function corridorStates(evidence: EvidenceBundle): Record<string, CorridorState> {
  const graph = evidence.access_graph
  if (!graph) return {}
  const flows = evidence.observations.filter((o) => o.category === 'traffic.flow' || o.category === 'traffic.corridor_eta')
  const active = evidence.incidents.filter((i) => i.status === 'active')

  const out: Record<string, CorridorState> = {}
  for (const a of graph.approaches) {
    const readings = flows.filter((o) => o.subject_id === a.id)
    const incidents = active.filter((i) => i.spatial.approach_ids.includes(a.id))
    const nearby = active.filter((i) => (i.spatial.near_approach_ids ?? []).includes(a.id))
    const worst = readings.reduce<Severity | null>((acc, o) => {
      const band = o.value.band as Severity | null
      return band && (acc === null || RANK[band] > RANK[acc]) ? band : acc
    }, null)
    // A reading with no band (e.g. a corridor ETA with no free-flow
    // reference) is still a reading: shown as "reported, not graded".
    let level: Severity | null = worst ?? (readings.length ? 'none' : null)
    if (incidents.length && RANK[worst ?? 'none'] < 3) level = 'high'
    out[a.id] = { readings, incidents, nearby, level }
  }
  return out
}

export function corridorLevels(evidence: EvidenceBundle): Record<string, Severity | null> {
  return Object.fromEntries(Object.entries(corridorStates(evidence)).map(([k, v]) => [k, v.level]))
}
