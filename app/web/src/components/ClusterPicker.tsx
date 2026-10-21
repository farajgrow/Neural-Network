import type { CSSProperties } from 'react'
import { humanize, pct, type Clusters } from '../api'
import { clusterColor } from '../colors'

interface Props {
  clusters: Clusters
  value: string | null
  onChange: (id: string | null) => void
}

/** Toggle for highlighting one script variant (sequence cluster) on the graph. */
export function ClusterPicker({ clusters, value, onChange }: Props) {
  const a = clusters.agreement
  return (
    <div className="cluster-picker">
      <div className="cluster-picker__head">
        <span className="overlay-title">Script variants</span>
        {a && (
          <span
            className="cluster-picker__agree"
            title="How well the sequence clusters line up with the LLM's scam_type labels. Purity: share of stories matching their cluster's majority type. ARI: 0 ≈ chance, 1 = identical."
          >
            vs. labels: purity {pct(a.purity)} · ARI {a.adjusted_rand.toFixed(2)}
          </span>
        )}
      </div>
      <div className="cluster-picker__chips">
        <button className={value == null ? 'vchip active' : 'vchip'} onClick={() => onChange(null)}>
          Off
        </button>
        {clusters.clusters.map((c, i) => (
          <button
            key={c.id}
            className={value === c.id ? 'vchip active' : 'vchip'}
            style={{ '--vc': clusterColor(i, c.outlier) } as CSSProperties}
            onClick={() => onChange(value === c.id ? null : c.id)}
            title={`${humanize(c.label)} · ${c.size} stories · ${pct(c.loss_rate)} lost money`}
          >
            <span className="vchip__dot" />
            {c.outlier ? 'Outliers' : `Variant ${i + 1}`}
            <span className="vchip__n">{c.size}</span>
          </button>
        ))}
      </div>
    </div>
  )
}
