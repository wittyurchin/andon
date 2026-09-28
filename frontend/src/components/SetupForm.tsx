import { useState, type FormEvent } from 'react'
import locations from '../data/locations.json'
import type { RestaurantConfig } from '../types'
import { REFRESH_OPTIONS } from './RefreshSelect'

interface Props {
  initial: RestaurantConfig | null
  onSubmit: (config: RestaurantConfig) => void
  onCancel?: () => void
}

// Edit src/data/locations.json to change the "Or start from" list.
const PRESETS: { label: string; lat: number; lon: number; name: string }[] = locations

export function SetupForm({ initial, onSubmit, onCancel }: Props) {
  const [name, setName] = useState(initial?.name ?? '')
  const [lat, setLat] = useState(initial ? String(initial.lat) : '')
  const [lon, setLon] = useState(initial ? String(initial.lon) : '')
  const [refreshMs, setRefreshMs] = useState(initial?.refreshMs ?? 5 * 60_000)
  const [error, setError] = useState<string | null>(null)

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    const latValue = Number(lat)
    const lonValue = Number(lon)

    if (!name.trim()) return setError('Give the restaurant a name.')
    if (!lat.trim() || Number.isNaN(latValue) || latValue < -90 || latValue > 90)
      return setError('Latitude must be a number between -90 and 90.')
    if (!lon.trim() || Number.isNaN(lonValue) || lonValue < -180 || lonValue > 180)
      return setError('Longitude must be a number between -180 and 180.')

    setError(null)
    onSubmit({ name: name.trim(), lat: latValue, lon: lonValue, refreshMs })
  }

  return (
    <form className="setup" onSubmit={handleSubmit}>
      <h1 className="setup__title">Restaurant Situation</h1>
      <p className="setup__lead">
        Enter a kitchen location to start monitoring the conditions around it.
      </p>

      <label className="field">
        <span className="field__label">Restaurant name</span>
        <input
          className="field__input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Indiranagar Kitchen"
          maxLength={120}
          autoFocus
        />
      </label>

      <div className="field-row">
        <label className="field">
          <span className="field__label">Latitude</span>
          <input
            className="field__input"
            value={lat}
            onChange={(e) => setLat(e.target.value)}
            placeholder="12.97840"
            inputMode="decimal"
          />
        </label>
        <label className="field">
          <span className="field__label">Longitude</span>
          <input
            className="field__input"
            value={lon}
            onChange={(e) => setLon(e.target.value)}
            placeholder="77.64080"
            inputMode="decimal"
          />
        </label>
      </div>

      <label className="field">
        <span className="field__label">Auto-refresh</span>
        <select
          className="field__input"
          value={refreshMs}
          onChange={(e) => setRefreshMs(Number(e.target.value))}
        >
          {REFRESH_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>

      <div className="setup__presets">
        <span className="field__label">Or start from</span>
        <div className="setup__preset-list">
          {PRESETS.map((preset) => (
            <button
              key={preset.label}
              type="button"
              className="chip"
              onClick={() => {
                setName(preset.name)
                setLat(String(preset.lat))
                setLon(String(preset.lon))
              }}
            >
              {preset.label}
            </button>
          ))}
        </div>
      </div>

      {error && <p className="setup__error">{error}</p>}

      <div className="setup__actions">
        <button type="submit" className="button button--primary">
          Start monitoring
        </button>
        {onCancel && (
          <button type="button" className="button" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </form>
  )
}
