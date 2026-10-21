import { label, pct, type GraphKey, type ScamTypeSummary } from '../api'

interface Props {
  types: ScamTypeSummary[]
  /** Loss rate of the combined "all" graph (not derivable from the per-type rates). */
  allLossRate: number | null
  value: GraphKey
  onChange: (key: GraphKey) => void
}

export function ScamTypeSwitcher({ types, allLossRate, value, onChange }: Props) {
  if (types.length === 0) return null
  const total = types.reduce((n, t) => n + t.n_stories, 0)
  const options: { key: GraphKey; n: number; loss: number | null }[] = [
    ...types.map((t) => ({ key: t.scam_type, n: t.n_stories, loss: t.loss_rate })),
    { key: 'all', n: total, loss: allLossRate },
  ]
  return (
    <nav className="tabs" aria-label="Scam type">
      {options.map((o) => (
        <button
          key={o.key}
          className={o.key === value ? 'tab active' : 'tab'}
          aria-pressed={o.key === value}
          onClick={() => onChange(o.key)}
          title={`${o.n} stories · ${pct(o.loss)} lost money`}
        >
          {o.key === 'all' ? 'All types' : label(o.key)} · {pct(o.loss)}
        </button>
      ))}
    </nav>
  )
}
