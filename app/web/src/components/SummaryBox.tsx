import { Button } from '@/components/ui/button'
import { humanize, knownPct, lower, pct, type Cluster, type Graph, type Step } from '../api'

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
  const { lost_money = 0, escaped = 0 } = graph.outcome_counts
  const known = lost_money + escaped
  return (
    <section className="summary" aria-label="Script summary">
      {cluster && (
        <div className="summary__variant">
          <div className="summary__variant-head">
            <span className="muted small">{cluster.outlier ? 'outlier stories' : `variant ${cluster.index + 1}`}</span>
            <Button variant="text" size="bare" onClick={onClearCluster} aria-label="Clear variant">[×]</Button>
          </div>
          <div className="b">{humanize(cluster.label).toLowerCase()}</div>
          <div className="small">
            {cluster.size} stories · <span className="c-orange-text">{knownPct(cluster.loss_rate, cluster.n_known)}</span> lost
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
          <div className="stat__label">lost money · {known} known</div>
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
              {p.n_known != null && <span className="muted">({p.n_known} known) </span>}
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
      <p className="footnote">
        % lost counts only stories with a known outcome
        {graph.n_stories > known && ` (${graph.n_stories - known} of ${graph.n_stories} are unknown)`}. Self-reported
        stories skew toward people who lost money. Associations, not causes.
      </p>
    </section>
  )
}
