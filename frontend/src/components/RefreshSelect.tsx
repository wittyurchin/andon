export const REFRESH_OPTIONS = [
  { value: 0, label: 'Off' },
  { value: 60_000, label: '1 min' },
  { value: 5 * 60_000, label: '5 min' },
  { value: 10 * 60_000, label: '10 min' },
  { value: 15 * 60_000, label: '15 min' },
  { value: 30 * 60_000, label: '30 min' },
] as const

interface Props {
  value: number
  onChange: (value: number) => void
}

export function RefreshSelect({ value, onChange }: Props) {
  return (
    <label className="refresh-select">
      <span>Auto-refresh</span>
      <select value={value} onChange={(e) => onChange(Number(e.target.value))}>
        {REFRESH_OPTIONS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  )
}
