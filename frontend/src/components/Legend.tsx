import { EPISTEMIC_LABEL, Glyph, Lamp, Pips, type Epistemic } from './marks'

const KINDS: Epistemic[] = ['measured', 'model', 'forecast', 'inferred', 'simulated']

/** How to read every mark on this page. Shown once, always visible. */
export function Legend() {
  return (
    <aside className="legend" aria-label="How to read this page">
      <span className="legend__group">
        <span className="legend__title">How we know</span>
        {KINDS.map((k) => (
          <span key={k} className={`mark mark--${k}`}>
            <Glyph kind={k} />
            {EPISTEMIC_LABEL[k]}
          </span>
        ))}
      </span>
      <span className="legend__group">
        <span className="legend__title">Source</span>
        <Lamp status="healthy" />
        <Lamp status="stale" />
        <Lamp status="unavailable" />
        <Lamp status="misconfigured" />
      </span>
      <span className="legend__group">
        <span className="legend__title">Severity</span>
        <Pips level="low" />
        <Pips level="medium" />
        <Pips level="high" />
        <Pips level="severe" />
      </span>
    </aside>
  )
}
