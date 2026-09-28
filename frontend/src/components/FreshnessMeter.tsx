import { FRESHNESS_LABEL } from '../lib/format'
import type { Freshness } from '../types'

const STAGES: Freshness[] = ['fresh', 'recent', 'aging', 'stale']

/**
 * How far a reading's age sits against this source's own freshness policy —
 * the same fresh/recent/aging/stale grading already used for situation
 * signals (evidence/freshness.py FreshnessPolicy.grade), not a new scale.
 */
export function FreshnessMeter({
  grade,
  ageLabel,
  staleAfterSeconds,
}: {
  grade: Freshness
  ageLabel: string
  staleAfterSeconds: number | null | undefined
}) {
  const reached = STAGES.indexOf(grade)
  return (
    <span className="freshness-meter tip" title={`${FRESHNESS_LABEL[grade]}${
      staleAfterSeconds ? ` · stale after ${Math.round(staleAfterSeconds / 60)} min` : ''
    }`}>
      <span className="freshness-meter__bar">
        {STAGES.map((stage, i) => (
          <span
            key={stage}
            className={`freshness-meter__seg freshness--${stage} ${i <= reached ? 'is-filled' : ''}`}
          />
        ))}
      </span>
      <span className={`freshness-meter__label freshness--${grade}`}>{ageLabel}</span>
    </span>
  )
}
