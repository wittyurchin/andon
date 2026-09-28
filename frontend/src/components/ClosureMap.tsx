import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useEffect, useMemo, useRef, useState } from 'react'
import { corridorLevels } from '../lib/corridors'
import { distance, incidentSpan } from '../lib/evidence'
import { distanceM } from '../lib/geo'
import type { EvidenceBundle, Geometry, IncidentEvidence, Observation } from '../types'

/**
 * Road closures on a real street map, so "which road" is answered by the
 * street itself. TomTom gives each closure's exact stretch but not the name
 * of the closed road (its from/to are the cross streets at either end), so
 * the map carries that part of the answer.
 *
 * Tiles: OpenStreetMap's public tile server. Its policy (checked 2026-09-28)
 * asks for attribution, the browser's normal Referer, and no bulk or offline
 * downloading, and gives no service guarantee: fine for a prototype, not for
 * production.
 */
const TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
const ATTRIBUTION = '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
// The first view frames the kitchen, its access roads and closures this close.
const FIRST_VIEW_M = 2000

type Kind = 'road_closure' | 'lane_closure'

interface Closure {
  incident: IncidentEvidence
  kind: Kind
  lines: L.LatLngTuple[][]
  point: L.LatLngTuple | null
  distanceM: number | null
}

const KIND_WORD: Record<Kind, string> = { road_closure: 'Road closed', lane_closure: 'Lane closed' }

function toLines(geometry: Geometry | null | undefined): { lines: L.LatLngTuple[][]; point: L.LatLngTuple | null } {
  const flip = (c: unknown): L.LatLngTuple | null =>
    Array.isArray(c) && typeof c[0] === 'number' && typeof c[1] === 'number' ? [c[1], c[0]] : null
  if (!geometry) return { lines: [], point: null }
  if (geometry.type === 'LineString' && Array.isArray(geometry.coordinates)) {
    const line = (geometry.coordinates as unknown[]).map(flip).filter((p): p is L.LatLngTuple => p !== null)
    return { lines: line.length > 1 ? [line] : [], point: null }
  }
  if (geometry.type === 'MultiLineString' && Array.isArray(geometry.coordinates)) {
    const lines = (geometry.coordinates as unknown[][]).map((part) =>
      part.map(flip).filter((p): p is L.LatLngTuple => p !== null),
    )
    return { lines: lines.filter((l) => l.length > 1), point: null }
  }
  if (geometry.type === 'Point') return { lines: [], point: flip(geometry.coordinates) }
  return { lines: [], point: null }
}

/** Plain-text tooltip content: provider text is never parsed as HTML. */
function tooltip(lines: string[]): HTMLElement {
  const root = document.createElement('div')
  root.className = 'map-tip'
  lines.forEach((text, i) => {
    const row = document.createElement(i === 0 ? 'strong' : 'div')
    row.textContent = text
    root.appendChild(row)
  })
  return root
}

function sinceText(iso: unknown): string | null {
  if (typeof iso !== 'string') return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return null
  return d.toLocaleDateString([], { day: 'numeric', month: 'short' })
}

export function ClosureMap({ evidence }: { evidence: EvidenceBundle }) {
  const el = useRef<HTMLDivElement>(null)
  const map = useRef<L.Map | null>(null)
  const layers = useRef<L.LayerGroup | null>(null)
  const byId = useRef(new Map<string, L.Polyline | L.CircleMarker>())
  const [focused, setFocused] = useState<string | null>(null)

  const home = evidence.restaurant.location
  const graph = evidence.access_graph
  const labels = useMemo(() => Object.fromEntries((graph?.approaches ?? []).map((a) => [a.id, a.label])), [graph])

  const closures: Closure[] = useMemo(
    () =>
      evidence.incidents
        .filter((i) => i.status === 'active' && (i.incident_type === 'road_closure' || i.incident_type === 'lane_closure'))
        .map((i) => {
          const { lines, point } = toLines(i.geometry)
          return {
            incident: i,
            kind: i.incident_type as Kind,
            lines,
            point: point ?? (lines.length ? null : i.location ? [i.location.lat, i.location.lon] : null),
            distanceM: i.spatial.distance_m ?? (i.location ? distanceM(home, i.location) : null),
          }
        })
        .sort((a, b) => (a.distanceM ?? Infinity) - (b.distanceM ?? Infinity)),
    [evidence.incidents, home],
  )

  // The second source for closures: TomTom flow flags a probed road as closed.
  const flowClosed: Observation[] = evidence.observations.filter(
    (o) => o.category === 'traffic.flow' && o.value.road_closed === true,
  )
  const flowProbes = evidence.observations.filter((o) => o.category === 'traffic.flow').length
  const notCovered = (evidence.coverage.incidents?.categories_not_covered ?? []).includes('road_closure')

  const relation = (i: IncidentEvidence): { text: string; cls: string } => {
    if (i.spatial.on_approach && i.spatial.approach_ids.length)
      return { text: `On ${i.spatial.approach_ids.map((a) => labels[a] ?? a).join(', ')}`, cls: 'is-on' }
    const near = i.spatial.near_approach_ids ?? []
    if (near.length) {
      const d = i.spatial.near_distance_m ?? null
      return d !== null && d <= 20
        ? { text: `Meets ${labels[near[0]] ?? near[0]}`, cls: 'is-near' }
        : { text: `Near ${labels[near[0]] ?? near[0]} (${distance(d)}), not on it`, cls: 'is-near' }
    }
    return { text: 'Not on your access roads', cls: '' }
  }

  // Create the map once.
  useEffect(() => {
    if (!el.current || map.current) return
    const m = L.map(el.current, { scrollWheelZoom: false, zoomControl: true })
    L.tileLayer(TILE_URL, { maxZoom: 19, attribution: ATTRIBUTION, className: 'map-tiles' }).addTo(m)
    layers.current = L.layerGroup().addTo(m)
    map.current = m
    return () => {
      m.remove()
      map.current = null
      layers.current = null
    }
  }, [])

  // Redraw whenever the evidence changes.
  useEffect(() => {
    const m = map.current
    const group = layers.current
    if (!m || !group) return
    group.clearLayers()
    byId.current.clear()
    const view = L.latLngBounds([[home.lat, home.lon]])
    const levels = corridorLevels(evidence)

    for (const s of graph?.segments ?? []) {
      if (!s.approach_id || s.geometry.length < 2) continue
      const latlngs = s.geometry.map(([lon, lat]) => [lat, lon] as L.LatLngTuple)
      const level = levels[s.approach_id]
      L.polyline(latlngs, {
        className: `map-road ${level ? `sev-${level}` : 'map-road--unknown'}`,
        weight: 5,
        lineCap: 'round',
      })
        .bindTooltip(tooltip([labels[s.approach_id] ?? 'Access road', level ? `Traffic: ${level}` : 'No traffic reading: unknown, not clear']), { sticky: true })
        .addTo(group)
      latlngs.forEach((p) => view.extend(p))
    }

    for (const c of closures) {
      const i = c.incident
      const span = incidentSpan(i.attributes)
      const facts = [
        `${KIND_WORD[c.kind]}${span ? `, ${span}` : ''}`,
        [
          typeof i.attributes.length_m === 'number' ? `${Math.round(i.attributes.length_m)} m` : null,
          c.distanceM !== null ? `${distance(c.distanceM)} ${i.spatial.bearing ?? ''} of the kitchen`.trim() : null,
        ].filter(Boolean).join(' · '),
        relation(i).text,
        [sinceText(i.attributes.reported_at) && `reported since ${sinceText(i.attributes.reported_at)}`, `${i.source_name}${typeof i.attributes.probabilityOfOccurrence === 'string' ? ` (${i.attributes.probabilityOfOccurrence})` : ''}`]
          .filter(Boolean).join(' · '),
      ]
      const tip = tooltip(facts)
      const kindCls = c.kind === 'road_closure' ? 'map-closure' : 'map-lane'
      for (const line of c.lines) {
        const base = L.polyline(line, { className: `${kindCls} ${kindCls}--base`, weight: c.kind === 'road_closure' ? 9 : 7, lineCap: 'butt' })
        base.bindTooltip(tip, { sticky: true }).addTo(group)
        L.polyline(line, { className: `${kindCls} ${kindCls}--dash`, weight: 3, lineCap: 'butt', dashArray: '5 6', interactive: false }).addTo(group)
        base.on('click', () => setFocused(i.id))
        if (!byId.current.has(i.id)) byId.current.set(i.id, base)
        if ((c.distanceM ?? Infinity) <= FIRST_VIEW_M) line.forEach((p) => view.extend(p))
      }
      if (c.point) {
        const dot = L.circleMarker(c.point, { className: `${kindCls}-pt`, radius: 7, weight: 3 })
        dot.bindTooltip(tip).addTo(group)
        dot.on('click', () => setFocused(i.id))
        byId.current.set(i.id, dot)
        if ((c.distanceM ?? Infinity) <= FIRST_VIEW_M) view.extend(c.point)
      }
    }

    L.marker([home.lat, home.lon], {
      icon: L.divIcon({ className: 'map-kitchen', iconSize: [16, 16] }),
      keyboard: false,
    })
      .bindTooltip(tooltip([evidence.restaurant.name, 'Kitchen']))
      .addTo(group)

    if (!focused) m.fitBounds(view.pad(0.08), { maxZoom: 16 })
    // Refit only when the data changes, not when the focus changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [evidence, closures, graph, home, labels])

  const focus = (id: string) => {
    const layer = byId.current.get(id)
    const m = map.current
    if (!layer || !m) return
    setFocused(id)
    const bounds = 'getBounds' in layer && typeof layer.getBounds === 'function' ? layer.getBounds() : L.latLngBounds([(layer as L.CircleMarker).getLatLng()])
    m.flyToBounds(bounds.pad(1.2), { maxZoom: 17, duration: 0.6 })
    layer.openTooltip()
    el.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }

  const roadCount = closures.filter((c) => c.kind === 'road_closure').length
  const laneCount = closures.length - roadCount
  const onRoads = closures.filter((c) => c.incident.spatial.on_approach).length
  const nearRoads = closures.filter((c) => !c.incident.spatial.on_approach && (c.incident.spatial.near_approach_ids ?? []).length).length

  return (
    <section className="panel panel--closures">
      <header className="panel__head">
        <h2 className="panel__title">Road closures</h2>
        <span className="panel__meta">
          {roadCount} road closed · {laneCount} lane closed · {onRoads} on your access roads · {nearRoads} near one
        </span>
      </header>

      <div className="closures">
        <div className="closures__mapwrap">
          <div ref={el} className="closures__map" role="region" aria-label="Street map of road closures around the kitchen" />
          <p className="closures__legend">
            <span><i className="lgm lgm--closure" />Road closed</span>
            <span><i className="lgm lgm--lane" />Lane closed</span>
            <span><i className="lgm lgm--road" />Your access roads, coloured by traffic</span>
            <span><i className="lgm lgm--kitchen" />Kitchen</span>
          </p>
        </div>

        <div className="closures__list">
          <p className="closures__flow">
            {flowProbes === 0
              ? 'No traffic-flow reading on the access roads, so no closure flag from that feed.'
              : flowClosed.length
                ? `Traffic flow flags ${flowClosed.length} of ${flowProbes} sampled access road${flowProbes === 1 ? '' : 's'} as closed.`
                : `Traffic flow reports all ${flowProbes} sampled access road${flowProbes === 1 ? '' : 's'} open.`}
          </p>
          {closures.length === 0 ? (
            <p className="empty">
              {notCovered
                ? 'No source here reports road closures; silence is not an all-clear.'
                : 'No closures reported around the kitchen.'}
            </p>
          ) : (
            <ol className="closure-list">
              {closures.map((c) => {
                const i = c.incident
                const rel = relation(i)
                const span = incidentSpan(i.attributes)
                const since = sinceText(i.attributes.reported_at)
                return (
                  <li key={i.id} className={`closure-item ${focused === i.id ? 'is-focused' : ''}`}>
                    <button type="button" className="closure-item__btn" onClick={() => focus(i.id)}>
                      <span className={`closure-item__kind closure-item__kind--${c.kind}`}>{KIND_WORD[c.kind]}</span>
                      <span className="closure-item__span">{span ? span.replace(/^from /, 'From ') : i.description}</span>
                      <span className="closure-item__meta">
                        {c.distanceM !== null && `${distance(c.distanceM)} ${i.spatial.bearing ?? ''}`}
                        {typeof i.attributes.length_m === 'number' && ` · ${Math.round(i.attributes.length_m)} m long`}
                        {since && ` · since ${since}`}
                      </span>
                      <span className={`closure-item__rel ${rel.cls}`}>{rel.text}</span>
                    </button>
                  </li>
                )
              })}
            </ol>
          )}
          <p className="attribution">
            Closures: TomTom Traffic Incidents. Map: © OpenStreetMap contributors. TomTom names the streets at
            either end of a closure, not the closed road; the map shows which street it is.
          </p>
        </div>
      </div>
    </section>
  )
}
