import { label, pct, type Graph, type Step, type StorySummary } from '../api'

interface Props {
  graph: Graph | null
  step: Step | null
  stories: StorySummary[]
  loading: boolean
  error: string | null
}

const money = (n: number | null) => (n == null ? null : `$${n.toLocaleString()}`)

export function SidePanel({ graph, step, stories, loading, error }: Props) {
  if (!step) {
    return (
      <aside className="panel side-panel">
        <h2>Stories</h2>
        <p className="muted">Click a step in the graph to read the stories that pass through it.</p>
        {graph && (
          <dl className="stats">
            <div><dt>Stories</dt><dd>{graph.n_stories}</dd></div>
            <div><dt>Lost money</dt><dd>{pct(graph.loss_rate)}</dd></div>
          </dl>
        )}
      </aside>
    )
  }
  const node = graph?.nodes.find((n) => n.step === step)
  return (
    <aside className="panel side-panel">
      <h2>{label(step)}</h2>
      {node && (
        <p className="muted">
          {node.count} stories · {pct(node.loss_rate)} lost money
        </p>
      )}
      {error && <p className="error">{error}</p>}
      {loading && <p className="muted">Loading stories…</p>}
      {!loading && !error && stories.length === 0 && <p className="muted">No stories for this step.</p>}
      <ul className="story-list">
        {stories.map((s) => (
          <li key={s.id} className="story">
            <div className="story__head">
              <span className={`badge outcome-${s.outcome}`}>{label(s.outcome)}</span>
              {money(s.amount_lost_usd) && <span className="story__amount">{money(s.amount_lost_usd)}</span>}
              <span className="story__type">{label(s.scam_type)}</span>
            </div>
            <p>{s.snippet}</p>
          </li>
        ))}
      </ul>
    </aside>
  )
}
