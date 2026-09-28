import { distance } from '../lib/evidence'
import { bearingDeg, distanceM, labelToDeg } from '../lib/geo'
import type { EvidenceBundle, GeoPoint, Observation, Severity } from '../types'
import { EPISTEMIC_LABEL, SEVERITY_RANK, markFor, type Epistemic } from './marks'

/**
 * Place before number. The kitchen sits at the centre; rings are the same
 * applicability bands the backend uses (evidence/spatial.py): at site 150 m,
 * local 1 km, nearby 5 km, regional 30 km. Each band gets equal width so a
 * station 4 km away and a model cell 1.9 km away are both legible.
 */
const BOUNDS = [0, 150, 1_000, 5_000, 30_000, 60_000]
const RING_LABEL = ['150 m', '1 km', '5 km', '30 km']
const SIZE = 360
const C = SIZE / 2
const R = SIZE / 2 - 26

function radius(d: number): number {
  const clamped = Math.max(0, Math.min(d, BOUNDS[BOUNDS.length - 1]))
  const k = Math.max(0, BOUNDS.findIndex((b, i) => clamped >= b && clamped < (BOUNDS[i + 1] ?? Infinity)))
  const band = Math.min(k, BOUNDS.length - 2)
  const frac = (clamped - BOUNDS[band]) / (BOUNDS[band + 1] - BOUNDS[band])
  return (R * (band + frac)) / (BOUNDS.length - 1)
}

function xy(deg: number, r: number): [number, number] {
  const a = ((deg - 90) * Math.PI) / 180
  return [C + r * Math.cos(a), C + r * Math.sin(a)]
}

interface Placed {
  key: string
  x: number
  y: number
  shape: 'station' | 'model' | 'radar' | 'incident'
  mark: Epistemic
  level: Severity | null
  onCorridor?: boolean
  title: string
  label?: string
}

// Any located weather reading places its source; alerts and verbatim reports
// carry no measurement location of their own.
const WEATHER = (o: Observation) =>
  o.category.startsWith('weather.') && o.category !== 'weather.alert' && !o.category.endsWith('_report')

const SHORT: Record<string, string> = {
  'awc-metar': 'METAR',
  'open-meteo': 'Open-Meteo',
  openweathermap: 'OpenWeatherMap',
  weatherapi: 'WeatherAPI',
  'imd-aws': 'IMD',
  'mock-weather': 'Mock model',
  'mock-station': 'Mock station',
}

function shortLabel(o: Observation): string {
  const base = SHORT[o.source_id] ?? o.source_name.replace(/\s*\(.*\)$/, '')
  const station = o.value.location_label as string | undefined
  // A station's own identifier is the useful part ("VOBG"), not its long name.
  return markFor(o) === 'measured' && station ? `${base} ${station.split(' ')[0]}` : base
}

export function SiteCompass({ evidence, corridorLevels }: { evidence: EvidenceBundle; corridorLevels: Record<string, Severity | null> }) {
  const home = evidence.restaurant.location
  const graph = evidence.access_graph

  const place = (loc: GeoPoint | null, fallbackBearing: string | null, fallbackDistance: number | null) => {
    if (loc) return { deg: bearingDeg(home, loc), d: distanceM(home, loc) }
    const deg = labelToDeg(fallbackBearing)
    return deg !== null && fallbackDistance !== null ? { deg, d: fallbackDistance } : null
  }

  // One point per weather source (its reading location), radar cells, active incidents.
  const points: Placed[] = []
  const unplaced: string[] = []
  const seen = new Set<string>()
  // Precipitation first, so a source's point carries its rain band when it has one.
  const weather = [...evidence.observations, ...evidence.forecasts]
    .filter(WEATHER)
    .sort((a, b) => Number(b.category === 'weather.precipitation') - Number(a.category === 'weather.precipitation'))
  for (const o of weather) {
    if (seen.has(o.source_id)) continue
    seen.add(o.source_id)
    const p = place(o.location, o.spatial.bearing, o.spatial.distance_m)
    const mark = markFor(o)
    const label = (o.value.location_label as string | undefined) ?? o.source_name
    if (!p) {
      unplaced.push(`${o.source_name}: no location reported`)
      continue
    }
    const [x, y] = xy(p.deg, radius(p.d))
    points.push({
      key: `w-${o.source_id}`,
      x,
      y,
      shape: mark === 'measured' ? 'station' : 'model',
      mark,
      level: (o.value.band as Severity | undefined) ?? null,
      title: `${o.source_name} · ${EPISTEMIC_LABEL[mark]} · ${label} · ${distance(p.d)} away`,
      label: shortLabel(o),
    })
  }
  for (const o of evidence.observations.filter((x) => x.category === 'radar.reflectivity' && x.location)) {
    const p = place(o.location, null, null)
    if (!p) continue
    const [x, y] = xy(p.deg, radius(p.d))
    points.push({
      key: `r-${o.id}`,
      x,
      y,
      shape: 'radar',
      mark: markFor(o),
      level: (o.value.band as Severity | undefined) ?? null,
      title: `${o.source_name} radar cell · ${distance(p.d)} away`,
    })
  }
  const alerts = evidence.forecasts.filter((f) => f.category === 'weather.alert' && !f.location)
  for (const a of alerts) unplaced.push(`${a.source_name}: ${String(a.value.headline ?? a.value.event).slice(0, 90)}`)

  for (const i of evidence.incidents.filter((x) => x.status === 'active')) {
    const p = place(i.location, i.spatial.bearing, i.spatial.distance_m)
    if (!p) continue
    const [x, y] = xy(p.deg, radius(p.d))
    points.push({
      key: `i-${i.id}`,
      x,
      y,
      shape: 'incident',
      mark: i.source_type === 'mock' ? 'simulated' : 'measured',
      level: null,
      onCorridor: i.spatial.on_approach,
      title: `${i.incident_type.replace(/_/g, ' ')} · ${i.description} · ${distance(p.d)} ${i.spatial.bearing ?? ''} · ${i.spatial.on_approach ? 'on an access road' : 'off the access roads'}`,
    })
  }

  // Draw incidents first so weather sources stay on top and readable.
  points.sort((a, b) => (a.shape === 'incident' ? 0 : 1) - (b.shape === 'incident' ? 0 : 1))

  return (
    <figure className="compass" aria-label="Map of sources and access roads around the kitchen">
      <svg viewBox={`0 0 ${SIZE} ${SIZE}`} className="compass__svg" role="img">
        {[1, 2, 3, 4].map((i) => (
          <circle key={i} cx={C} cy={C} r={(R * i) / 5} className={`compass__ring ${i === 1 ? 'compass__ring--site' : ''}`} />
        ))}
        <circle cx={C} cy={C} r={R} className="compass__ring compass__ring--edge" />
        {[0, 90, 180, 270].map((deg) => {
          const [x1, y1] = xy(deg, (R * 1) / 5)
          const [x2, y2] = xy(deg, R)
          return <line key={deg} x1={x1} y1={y1} x2={x2} y2={y2} className="compass__axis" />
        })}
        {(['N', 'E', 'S', 'W'] as const).map((l, i) => {
          const [x, y] = xy(i * 90, R + 14)
          return (
            <text key={l} x={x} y={y} className="compass__cardinal" textAnchor="middle" dominantBaseline="central">
              {l}
            </text>
          )
        })}
        {RING_LABEL.map((label, i) => {
          const [x, y] = xy(180, (R * (i + 1)) / 5)
          return (
            <text key={label} x={x + 4} y={y - 3} className="compass__ringlabel">
              {label}
            </text>
          )
        })}

        {graph?.approaches.map((a) => {
          const deg = bearingDeg(home, a.probe)
          const from = xy(deg, radius(a.distance_m))
          const to = xy(deg, radius(a.distance_m + a.length_m))
          const level = corridorLevels[a.id]
          return (
            <line
              key={a.id}
              x1={from[0]}
              y1={from[1]}
              x2={to[0]}
              y2={to[1]}
              className={`compass__road ${level ? `sev-${level}` : 'compass__road--unknown'} ${a.is_approximation ? 'compass__road--approx' : ''}`}
            >
              <title>
                {a.label} · {level ? `traffic ${level}` : 'no traffic reading, unknown not clear'}
                {a.is_approximation ? ' · approximation, not a mapped road' : ''}
              </title>
            </line>
          )
        })}

        {points.map((p) => (
          <g key={p.key} transform={`translate(${p.x} ${p.y})`} className={`compass__pt compass__pt--${p.shape} ${p.level && SEVERITY_RANK[p.level] > 0 ? `sev-${p.level} is-severe` : ''} ${p.onCorridor ? 'is-on-road' : ''} mark--${p.mark}`}>
            <title>{p.title}</title>
            {p.shape === 'station' && <circle r="5.5" />}
            {p.shape === 'model' && <circle r="5.5" />}
            {p.shape === 'radar' && <rect x="-3" y="-3" width="6" height="6" />}
            {p.shape === 'incident' && <path d="M0 -4.5 L4 3 L-4 3 Z" />}
            {p.label && (
              <text
                x={p.x > C + R * 0.35 ? -9 : 9}
                y="0"
                dominantBaseline="central"
                textAnchor={p.x > C + R * 0.35 ? 'end' : 'start'}
                className="compass__label"
              >
                {p.label}
              </text>
            )}
          </g>
        ))}

        <g transform={`translate(${C} ${C})`} className="compass__home">
          <title>{evidence.restaurant.name}</title>
          <rect x="-5" y="-5" width="10" height="10" />
        </g>
      </svg>

      <figcaption className="compass__legend">
        <span><i className="lg lg--home" />Kitchen</span>
        <span><i className="lg lg--station" />Measured</span>
        <span><i className="lg lg--model" />Model</span>
        <span><i className="lg lg--radar" />Radar cell</span>
        <span><i className="lg lg--incident" />Incident</span>
        <span><i className="lg lg--road" />Access road</span>
        <span><i className="lg lg--unknown" />No reading</span>
      </figcaption>
      {unplaced.length > 0 && (
        <ul className="compass__unplaced">
          {unplaced.map((u) => (
            <li key={u}>Not on the map: {u}</li>
          ))}
        </ul>
      )}
    </figure>
  )
}
