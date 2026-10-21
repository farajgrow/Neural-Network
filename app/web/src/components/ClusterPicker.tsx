import { humanize, knownPct, pct, SCAM_TYPES, type Clusters } from '../api'

/** Drop a leading "fake_job · " when every variant shares the type anyway. */
const short = (l: string) => {
  const [head, ...rest] = l.split(' · ')
  return rest.length && (SCAM_TYPES as readonly string[]).includes(head) ? rest.join(' · ') : l
}

interface Props {
  clusters: Clusters
  value: string | null
  onChange: (id: string | null) => void
  disabled?: boolean
}

/** Picker for highlighting one script variant (sequence cluster) on the graph. */
export function ClusterPicker({ clusters, value, onChange, disabled }: Props) {
  const a = clusters.agreement
  const agree = a
    ? `Sequence clusters vs. the LLM's scam_type labels: purity ${pct(a.purity)}, ARI ${a.adjusted_rand.toFixed(2)} (0 ≈ chance, 1 = identical).`
    : undefined
  return (
    <div className="variant" title={disabled ? 'Clear the matched message to pick a variant' : agree}>
      <label htmlFor="variant" className="muted">variant</label>
      <select
        id="variant"
        value={value ?? ''}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value || null)}
      >
        <option value="">all stories</option>
        {clusters.clusters.map((c, i) => (
          <option key={c.id} value={c.id}>
            {c.outlier ? 'outliers' : `${i + 1}: ${humanize(short(c.label)).toLowerCase()}`} ({c.size}) · {knownPct(c.loss_rate, c.n_known)} lost
          </option>
        ))}
      </select>
    </div>
  )
}
