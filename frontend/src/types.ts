// Mirrors backend/andon/domain. The UI speaks only this normalized vocabulary —
// it has no knowledge of Open-Meteo, TomTom, or any other provider.

export type Severity = 'none' | 'low' | 'medium' | 'high' | 'severe'
export type Trend = 'improving' | 'steady' | 'worsening' | 'unknown'
export type Freshness = 'fresh' | 'recent' | 'aging' | 'stale' | 'unknown'
export type Confidence = 'low' | 'medium' | 'high'
export type EvidenceKind = 'observed' | 'forecast' | 'inferred'
export type SourceStatus = 'ok' | 'degraded' | 'unavailable'
export type SignalKind = 'weather' | 'traffic' | 'road_conditions'
export type SignalBasis = 'observation' | 'model' | 'simulated' | 'unknown'

export interface SourceRef {
  id: string
  name: string
  kind: string
  mode: 'live' | 'mock'
  attribution: string | null
  docs_url: string | null
  licence_note: string | null
}

export interface Evidence {
  text: string
  kind: EvidenceKind
  source_id: string
  source_name: string
  observed_at: string | null
  age_seconds: number | null
  freshness: Freshness
  confidence: Confidence
  distance_km: number | null
  weight: number
}

export interface Facet {
  key: string
  label: string
  severity: Severity
  value: number | string | null
  unit: string | null
  kind: EvidenceKind
  note: string | null
  available: boolean
}

export interface SignalAssessment {
  kind: SignalKind
  status: SourceStatus
  basis: SignalBasis
  /** Short tile label when several sources cover the same signal. */
  source_label: string | null
  /** Set on a consolidated assessment whose sources conflict. */
  disagreement: string | null
  severity: Severity
  headline: string
  detail: string | null
  trend: Trend
  trend_note: string | null
  confidence: Confidence
  observed_at: string | null
  age_seconds: number | null
  freshness: Freshness
  facets: Facet[]
  evidence: Evidence[]
  sources: SourceRef[]
  unavailable_reason: string | null
  coverage_gaps: string[]
}

export interface OverallAssessment {
  level: Severity
  label: string
  score: number
  headline: string
  drivers: string[]
  trend: Trend
  trend_note: string | null
  confidence: Confidence
}

export interface NormalizedSituation {
  restaurant: { name: string; location: { lat: number; lon: number } }
  generated_at: string
  overall: OverallAssessment
  weather: SignalAssessment
  /** One per configured weather provider; empty when only one is configured. */
  weather_sources: SignalAssessment[]
  traffic: SignalAssessment
  traffic_sources: SignalAssessment[]
  road_conditions: SignalAssessment
  missing_signals: string[]
  coverage_gaps: string[]
  confidence: Confidence
  fingerprint: string
}

export interface SituationReport {
  situation_title: string
  summary: string
  contributing_factors: string[]
  improving: string[]
  worsening: string[]
  operational_impact: string[]
  outlook_30_60min: string
  confidence: Confidence
  confidence_rationale: string
  uncertainties: string[]
  key_evidence: string[]
  generator: 'llm' | 'rules-fallback'
  model: string | null
  generated_at: string | null
  reused: boolean
  reuse_reason: string | null
  input_tokens: number | null
  output_tokens: number | null
}

export interface TrendItem {
  label: string
  trend: Trend
  detail: string | null
  kind: EvidenceKind
}

export interface SituationSnapshot {
  at: string
  level: Severity
  score: number
  headline: string
  weather: Severity
  traffic: Severity
  road_conditions: Severity
  traffic_index: number | null
  precipitation_mm_h: number | null
}

export interface SourceHealth {
  source: SourceRef
  status: SourceStatus
  observed_at: string | null
  fetched_at: string | null
  age_seconds: number | null
  freshness: Freshness
  cached: boolean
  latency_ms: number | null
  error: string | null
}

export interface SituationResult {
  situation: NormalizedSituation
  report: SituationReport
  trends: TrendItem[]
  history: SituationSnapshot[]
  history_note: string | null
  sources: SourceHealth[]
}

export interface StatusResponse {
  providers: { weather: SourceRef[]; traffic: SourceRef; incidents: SourceRef }
  llm: { available: boolean; status: string; model: string | null; effort: string | null }
  cached_reports: number
  mock_scenario: string
}

/** User-controlled settings, persisted in localStorage. */
export interface RestaurantConfig {
  name: string
  lat: number
  lon: number
  refreshMs: number
}

// ---------------------------------------------------------------------------
// Evidence layer (backend/andon/evidence/models.py)
// ---------------------------------------------------------------------------

export type HealthStatus =
  | 'healthy'
  | 'stale'
  | 'degraded'
  | 'unavailable'
  | 'misconfigured'
  | 'unauthorized'
  | 'disabled'
export type Applicability = 'at_site' | 'local' | 'nearby' | 'regional' | 'distant' | 'unknown'
export type SourceType = 'live' | 'mock' | 'derived' | 'configured'

export interface GeoPoint {
  lat: number
  lon: number
}

export interface SpatialContext {
  distance_m: number | null
  bearing: string | null
  applicability: Applicability
  relevance: number
  nearest_segment_id: string | null
  nearest_segment_distance_m: number | null
  approach_ids: string[]
  on_approach: boolean
  /** Access roads it is near but not on (parallel street, junction), nearest first. */
  near_approach_ids?: string[]
  near_distance_m?: number | null
  basis: string
}

export interface Observation {
  id: string
  restaurant_id: string
  source_id: string
  source_name: string
  source_type: SourceType
  category: string
  subject_id: string | null
  kind: 'observation' | 'forecast'
  observed_at: string
  observed_at_basis: 'source' | 'received'
  valid_at: string | null
  received_at: string
  location: GeoPoint | null
  spatial: SpatialContext
  value: Record<string, unknown>
  confidence: number
  freshness_seconds: number | null
  stale: boolean
  stale_after_seconds: number | null
  source_record_id: string | null
  raw_reference: string | null
}

export interface IncidentEvidence {
  id: string
  source_id: string
  source_name: string
  source_type: SourceType
  source_record_id: string | null
  incident_type: string
  status: 'active' | 'cleared'
  description: string
  location: GeoPoint | null
  first_seen: string
  last_seen: string
  observed_at: string | null
  cleared_at: string | null
  confidence: number
  spatial: SpatialContext
  attributes: Record<string, unknown>
  freshness_seconds: number | null
  stale: boolean
}

export interface SourceHealthRecord {
  source_id: string
  source_name: string
  source_kind: string
  source_type: SourceType
  status: HealthStatus
  checked_at: string
  last_success_at: string | null
  last_failure_at: string | null
  consecutive_failures: number
  latency_ms: number | null
  response_age_seconds: number | null
  /** response_age_seconds graded against this source's freshness policy. */
  freshness_grade: Freshness
  error: string | null
  licence_note: string | null
  details: Record<string, unknown>
}

export interface ChangeEvent {
  id: string
  detected_at: string
  change_type: string
  category: string
  subject_id: string | null
  source_id: string | null
  summary: string
  magnitude: number | null
}

export interface Approach {
  id: string
  label: string
  road_name: string | null
  road_class: string | null
  bearing: string | null
  distance_m: number
  length_m: number
  probe: GeoPoint
  segment_ids: string[]
  derivation: string
  is_approximation: boolean
}

export interface AccessGraph {
  source: 'osm' | 'configured' | 'radial' | 'mock'
  source_type: SourceType
  built_at: string
  radius_m: number
  derivation: string
  attribution: string | null
  approaches: Approach[]
  notes: string[]
}

export interface Conflict {
  category: string
  subject: string
  summary: string
  spread: number
  members: {
    observation_id: string
    source_id: string
    source_name: string
    kind: 'observation' | 'forecast'
    band: string | null
    distance_m: number | null
    relevance: number
    observed_at: string
  }[]
}

export interface TrendSummary {
  category: string
  source_id: string
  subject_id: string | null
  direction: 'worsening' | 'improving' | 'steady'
  window_minutes: number
  points: number
}

export interface EvidenceBundle {
  restaurant: { id: string; name: string; location: GeoPoint; refresh_interval_seconds: number }
  generated_at: string
  refresh: {
    refresh_id: string
    providers_called: number
    providers_failed: number
    observations_new: number
    changes_detected: number
  } | null
  observations: Observation[]
  forecasts: Observation[]
  incidents: IncidentEvidence[]
  source_health: SourceHealthRecord[]
  access_graph: AccessGraph | null
  changes: ChangeEvent[]
  conflicts: Conflict[]
  trends: TrendSummary[]
  coverage: {
    weather?: { statement: string; has_local_observation: boolean; nearest_observation_m: number | null }
    traffic?: { approaches_total: number; approaches_observed: number; approaches_unobserved: string[]; graph_is_approximation: boolean }
    incidents?: { categories_not_covered: string[]; sources_usable: string[] }
    stale_sources?: string[]
  }
  attributions: string[]
}

export interface SituationResponse extends SituationResult {
  restaurant_id: string
  evidence: EvidenceBundle
}

export interface SourceDetail {
  restaurant_id: string
  source: SourceRef | null
  health: SourceHealthRecord | null
  observations: Observation[]
  forecasts: Observation[]
  incidents: IncidentEvidence[]
  history: Observation[]
  history_minutes: number
  verification_doc: string
}
