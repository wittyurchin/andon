import { useCallback, useEffect, useState } from 'react'
import { AccessPanel } from './components/AccessPanel'
import { ChangesPanel } from './components/ChangesPanel'
import { EvidenceInspector, type InspectorFilter } from './components/EvidenceInspector'
import { EvidencePanel } from './components/EvidencePanel'
import { Header } from './components/Header'
import { KeyProblemsAlert } from './components/KeyProblemsAlert'
import { HistoryStrip } from './components/HistoryStrip'
import { OutlookPanel } from './components/OutlookPanel'
import { OverallBanner } from './components/OverallBanner'
import { ReportPanel } from './components/ReportPanel'
import { SetupForm } from './components/SetupForm'
import { SignalCards } from './components/SignalCards'
import { SourceCards } from './components/SourceCards'
import { SourcesPanel } from './components/SourcesPanel'
import { TrendPanel } from './components/TrendPanel'
import { useSituation } from './hooks/useSituation'
import type { RestaurantConfig } from './types'

const STORAGE_KEY = 'andon.restaurant'

function loadConfig(): RestaurantConfig | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<RestaurantConfig>
    if (typeof parsed.lat !== 'number' || typeof parsed.lon !== 'number') return null
    return {
      name: parsed.name || 'Restaurant',
      lat: parsed.lat,
      lon: parsed.lon,
      refreshMs: typeof parsed.refreshMs === 'number' ? parsed.refreshMs : 5 * 60_000,
    }
  } catch {
    return null
  }
}

export default function App() {
  const [config, setConfig] = useState<RestaurantConfig | null>(loadConfig)
  const [editing, setEditing] = useState(false)
  const [inspect, setInspect] = useState<InspectorFilter>('all')

  const openInspector = useCallback((filter: InspectorFilter) => {
    setInspect(filter)
    document.getElementById('evidence')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [])

  useEffect(() => {
    if (config) window.localStorage.setItem(STORAGE_KEY, JSON.stringify(config))
  }, [config])

  const state = useSituation(config)

  const handleRefreshMsChange = useCallback((refreshMs: number) => {
    setConfig((previous) => (previous ? { ...previous, refreshMs } : previous))
  }, [])

  if (!config || editing) {
    return (
      <main className="shell shell--setup">
        <SetupForm
          initial={config}
          onSubmit={(next) => {
            setConfig(next)
            setEditing(false)
          }}
          onCancel={config ? () => setEditing(false) : undefined}
        />
      </main>
    )
  }

  const { data, error, loading, now } = state

  return (
    <main className="shell">
      <Header
        config={config}
        result={data}
        loading={loading}
        now={now}
        lastFetchedAt={state.lastFetchedAt}
        nextRefreshAt={state.nextRefreshAt}
        deferred={state.deferred}
        onRefresh={() => state.refresh(true)}
        onRefreshMsChange={handleRefreshMsChange}
        onEdit={() => setEditing(true)}
      />

      {error && (
        <div className="alert" role="alert">
          <strong>Situation data unavailable.</strong> {error}
          {data && ' Showing the last successful assessment below.'}
        </div>
      )}

      {!data ? (
        <section className="placeholder">
          {loading ? 'Gathering external conditions…' : 'No situation data yet.'}
        </section>
      ) : (
        <>
          <KeyProblemsAlert evidence={data.evidence} />
          <SourceCards evidence={data.evidence} now={now} />
          <OverallBanner situation={data.situation} />
          <SignalCards situation={data.situation} now={now} onInspect={openInspector} />
          <AccessPanel evidence={data.evidence} />
          <ReportPanel report={data.report} now={now} />

          <div className="grid grid--two">
            <EvidencePanel situation={data.situation} now={now} />
            <div className="grid__stack">
              <TrendPanel trends={data.trends} report={data.report} />
              <OutlookPanel report={data.report} />
            </div>
          </div>

          <ChangesPanel changes={data.evidence.changes} />
          <HistoryStrip history={data.history} note={data.history_note} />
          <EvidenceInspector evidence={data.evidence} filter={inspect} onFilter={setInspect} />
          <SourcesPanel evidence={data.evidence} now={now} />

          <footer className="footer">
            <span>
              Assessment generated {new Date(data.situation.generated_at).toLocaleString()} ·
              signature {data.situation.fingerprint}
            </span>
            <span>
              Rule-based classification · LLM reasoning · no signal is invented when a source is
              unavailable
            </span>
          </footer>
        </>
      )}
    </main>
  )
}
