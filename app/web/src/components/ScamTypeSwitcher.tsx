import { label, pct, type GraphKey, type ScamTypeSummary } from '../api'

interface Props {
  types: ScamTypeSummary[]
  value: GraphKey
  onChange: (key: GraphKey) => void
}

export function ScamTypeSwitcher({ types, value, onChange }: Props) {
  const total = types.reduce((n, t) => n + t.n_stories, 0)
  const options: { key: GraphKey; n: number; loss: number | null }[] = [
    { key: 'all', n: total, loss: null },
    ...types.map((t) => ({ key: t.scam_type, n: t.n_stories, loss: t.loss_rate })),
  ]
  return (
    <nav className="switcher" aria-label="Scam type">
      {options.map((o) => (
        <button
          key={o.key}
          className={o.key === value ? 'chip active' : 'chip'}
          onClick={() => onChange(o.key)}
          title={o.loss == null ? undefined : `${pct(o.loss)} lost money`}
        >
          {label(o.key)} <span className="chip__count">{o.n}</span>
        </button>
      ))}
    </nav>
  )
}
