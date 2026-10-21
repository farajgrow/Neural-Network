import { humanize, lower, pct, type Cluster, type Graph, type Step } from '../api'

interface Props {
  graph: Graph | null
  cluster: (Cluster & { index: number }) | null
  onClearCluster: () => void
  onSelectStep: (step: Step) => void
}

/** Headline numbers, pinch and escape points for the current scam type. */
export function SummaryBox({ graph, cluster, onClearCluster, onSelectStep }: Props) {
  if (!graph) return null
  const countOf = (s: Step) => graph.nodes.find((n) => n.step === s)?.count
  return (
    <section className="summary" aria-label="Script summary">
      {cluster && (
        <div className="summary__variant">
          <div className="summary__variant-head">
            <span className="muted small">{cluster.outlier ? 'outlier stories' : `variant ${cluster.index + 1}`}</span>
            <button className="link-btn" onClick={onClearCluster} aria-label="Clear variant">[×]</button>
          </div>
          <div className="b">{humanize(cluster.label).toLowerCase()}</div>
          <div className="small">
            {cluster.size} stories · <span className="c-orange-text">{pct(cluster.loss_rate)}</span> lost ·{' '}
            {cluster.typical_sequence.length} typical steps
          </div>
        </div>
      )}
      <div className="summary__stats">
        <div>
          <div className="stat">{graph.n_stories}</div>
          <div className="stat__label">stories</div>
        </div>
        <div>
          <div className="stat c-orange-text">{pct(graph.loss_rate)}</div>
          <div className="stat__label">lost money</div>
        </div>
        <div>
          <div className="stat c-blue">{graph.outcome_counts.escaped ?? 0}</div>
          <div className="stat__label">escaped</div>
        </div>
      </div>
      <div className="summary__rows">
        {graph.pinch_points.map((p) => (
          <button key={`p-${p.step}`} className="row" onClick={() => onSelectStep(p.step)} title="Pinch point: the loss rate jumps here">
            <span>▲ {lower(p.step)}</span>
            <span>
              {p.prev_loss_rate == null ? '—' : Math.round(p.prev_loss_rate * 100)}→<b className="c-orange-text">{pct(p.loss_rate)}</b>
            </span>
          </button>
        ))}
        {graph.escape_points.map((p) => (
          <button key={`e-${p.step}`} className="row" onClick={() => onSelectStep(p.step)} title="Escape point: victims often got out right after">
            <span>↗ {lower(p.step)}</span>
            <span className="c-blue">
              {p.exit_count} of {countOf(p.step) ?? '?'}
            </span>
          </button>
        ))}
        {graph.pinch_points.length === 0 && graph.escape_points.length === 0 && (
          <p className="muted small row-empty">No pinch or escape points for this type.</p>
        )}
      </div>
      <p className="footnote">Self-reported stories skew toward people who lost money. Associations, not causes.</p>
    </section>
  )
}
