/**
 * The visual vocabulary shared by every section. Each mark pairs a shape with
 * a word, so nothing here relies on color alone.
 *
 *   How we know it (epistemic):  ● measured   ○ model   ◌ forecast   ◇ inferred   ▨ simulated
 *   Is the source working:       lit lamp, half lamp, lamp out, lamp not wired
 *   How bad:                     0 to 4 pips (clear, low, medium, high, severe)
 */
import type { EvidenceKind, HealthStatus, Observation, Severity } from '../types'

export type Epistemic = 'measured' | 'model' | 'forecast' | 'inferred' | 'simulated'

export const EPISTEMIC_LABEL: Record<Epistemic, string> = {
  measured: 'Measured',
  model: 'Model',
  forecast: 'Forecast',
  inferred: 'Inferred',
  simulated: 'Simulated',
}

export const EPISTEMIC_MEANING: Record<Epistemic, string> = {
  measured: 'Measured or reported by the source itself, at a known place.',
  model: "A model's estimate of conditions now. Not a measurement.",
  forecast: "A model's value for a future time.",
  inferred: "This system's own deduction from other evidence. Never a report.",
  simulated: 'Simulated data from a mock source. Not real.',
}

/** Which mark an observation earns. Mock beats everything; then measured vs model vs forecast. */
export function markFor(o: Pick<Observation, 'kind' | 'source_type' | 'valid_at' | 'observed_at' | 'category'>): Epistemic {
  if (o.source_type === 'mock') return 'simulated'
  if (o.kind === 'observation') return 'measured'
  // An official warning is an authority's statement about what is expected,
  // not a model's estimate of now.
  if (o.category === 'weather.alert') return 'forecast'
  if (o.valid_at && o.valid_at !== o.observed_at && new Date(o.valid_at) > new Date(o.observed_at)) return 'forecast'
  return 'model'
}

/** Situation-layer evidence kind to mark. */
export function markForKind(kind: EvidenceKind, simulated = false): Epistemic {
  if (simulated) return 'simulated'
  return kind === 'observed' ? 'measured' : kind === 'inferred' ? 'inferred' : 'forecast'
}

export function Glyph({ kind, size = 10 }: { kind: Epistemic; size?: number }) {
  const s = size
  const c = s / 2
  const r = s / 2 - 1
  return (
    <svg className={`glyph glyph--${kind}`} width={s} height={s} viewBox={`0 0 ${s} ${s}`} aria-hidden="true">
      {kind === 'measured' && <circle cx={c} cy={c} r={r} />}
      {kind === 'model' && <circle cx={c} cy={c} r={r - 0.4} />}
      {kind === 'forecast' && <circle cx={c} cy={c} r={r - 0.4} />}
      {kind === 'inferred' && <path d={`M${c} 1 L${s - 1} ${c} L${c} ${s - 1} L1 ${c} Z`} />}
      {kind === 'simulated' && (
        <>
          <defs>
            <pattern id={`hatch-${s}`} width="3" height="3" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
              <line x1="0" y1="0" x2="0" y2="3" />
            </pattern>
          </defs>
          <rect x="1" y="1" width={s - 2} height={s - 2} fill={`url(#hatch-${s})`} />
        </>
      )}
    </svg>
  )
}

/** Glyph plus word. `compact` drops the word but keeps it for assistive tech and hover. */
export function Mark({ kind, compact = false, label }: { kind: Epistemic; compact?: boolean; label?: string }) {
  return (
    <span className={`mark mark--${kind}`} title={EPISTEMIC_MEANING[kind]}>
      <Glyph kind={kind} />
      {compact ? <span className="visually-hidden">{label ?? EPISTEMIC_LABEL[kind]}</span> : (label ?? EPISTEMIC_LABEL[kind])}
    </span>
  )
}

// ---------------------------------------------------------------- health

export type LampState = 'lit' | 'dim' | 'out' | 'unwired'

export function lampFor(status: HealthStatus): LampState {
  if (status === 'healthy') return 'lit'
  if (status === 'stale' || status === 'degraded') return 'dim'
  if (status === 'unavailable' || status === 'unauthorized') return 'out'
  return 'unwired'
}

export const HEALTH_WORD: Record<HealthStatus, string> = {
  healthy: 'Healthy',
  stale: 'Stale',
  degraded: 'Degraded',
  unavailable: 'Unavailable',
  unauthorized: 'Key rejected',
  misconfigured: 'Not configured',
  disabled: 'Disabled',
}

export function Lamp({ status, showLabel = true }: { status: HealthStatus; showLabel?: boolean }) {
  const state = lampFor(status)
  return (
    <span className={`lamp lamp--${state}`}>
      <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
        {state === 'lit' && <circle cx="5" cy="5" r="4" />}
        {state === 'dim' && (
          <>
            <circle cx="5" cy="5" r="3.6" className="lamp__ring" />
            <path d="M5 1.4 A3.6 3.6 0 0 1 5 8.6 Z" />
          </>
        )}
        {state === 'out' && (
          <>
            <circle cx="5" cy="5" r="3.6" className="lamp__ring" />
            <line x1="2.2" y1="7.8" x2="7.8" y2="2.2" />
          </>
        )}
        {state === 'unwired' && <circle cx="5" cy="5" r="3.6" className="lamp__ring lamp__ring--dashed" />}
      </svg>
      {showLabel ? HEALTH_WORD[status] : <span className="visually-hidden">{HEALTH_WORD[status]}</span>}
    </span>
  )
}

// ---------------------------------------------------------------- severity

export const SEVERITY_RANK: Record<Severity, number> = { none: 0, low: 1, medium: 2, high: 3, severe: 4 }

export const SEVERITY_WORD: Record<Severity, string> = {
  none: 'Clear',
  low: 'Low',
  medium: 'Medium',
  high: 'High',
  severe: 'Severe',
}

/** Four pips, filled up to the level: a colour-blind-safe reading of severity. */
export function Pips({ level, label = true }: { level: Severity; label?: boolean }) {
  const rank = SEVERITY_RANK[level]
  return (
    <span className={`pips sev-${level}`} title={`Severity: ${SEVERITY_WORD[level]}`}>
      <span className="pips__row" aria-hidden="true">
        {[1, 2, 3, 4].map((i) => (
          <span key={i} className={`pips__pip ${i <= rank ? 'is-on' : ''}`} />
        ))}
      </span>
      {label ? <span className="pips__word">{SEVERITY_WORD[level]}</span> : <span className="visually-hidden">{SEVERITY_WORD[level]}</span>}
    </span>
  )
}

/** The andon stack light: four lamps, lit from the bottom up to the current level. */
export function AndonStack({ level }: { level: Severity }) {
  const rank = SEVERITY_RANK[level]
  const order: Severity[] = ['severe', 'high', 'medium', 'low']
  return (
    <div className="andon" role="img" aria-label={`Situation level ${SEVERITY_WORD[level]}, ${rank} of 4`}>
      {order.map((s) => (
        <span key={s} className={`andon__lamp sev-${s} ${SEVERITY_RANK[s] <= rank ? 'is-on' : ''}`}>
          <span className="andon__word">{SEVERITY_WORD[s]}</span>
        </span>
      ))}
      <span className="andon__base" />
    </div>
  )
}
