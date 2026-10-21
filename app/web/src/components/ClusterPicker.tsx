import { humanize, pct, type Clusters } from '../api'

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
            {c.outlier ? 'outliers' : `${i + 1}: ${humanize(c.label).toLowerCase()}`} ({c.size}) · {pct(c.loss_rate)} lost
          </option>
        ))}
      </select>
    </div>
  )
}
