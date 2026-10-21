import { useEffect, useState } from 'react'
import { knownPct, lower, money, pct, pts, type Graph, type Step, type StorySummary } from '../api'

interface Props {
  graph: Graph | null
  step: Step | null
  onSelectStep: (step: Step | null) => void
  stories: StorySummary[]
  loading: boolean
  error: Error | null
}

const PAGE = 6

/** "> cases that reached <step>": the stories behind the selected node. */
export function StoryStrip({ graph, step, onSelectStep, stories, loading, error }: Props) {
  const [limit, setLimit] = useState(PAGE)
  useEffect(() => setLimit(PAGE), [step, graph?.scam_type])

  if (!graph) return null
  if (!step) {
    return (
      <section className="strip" aria-label="Stories at this step">
        <h2 className="strip__title">
          &gt; cases that reached <span className="muted">[click a step]</span>
        </h2>
        <p className="muted small">Pick any square in the graph to read the victim stories that went through it.</p>
      </section>
    )
  }

  const node = graph.nodes.find((n) => n.step === step)
  const pinch = graph.pinch_points.find((p) => p.step === step)
  const escape = graph.escape_points.find((p) => p.step === step)
  const next = graph.edges
    .filter((e) => e.from === step)
    .sort((a, b) => b.count - a.count)
    .slice(0, 3)
  const shown = stories.slice(0, limit)

  return (
    <section className="strip" aria-label="Stories at this step">
      <h2 className="strip__title">
        &gt; cases that reached {lower(step)}{' '}
        <span className="muted">[{loading ? '…' : error ? '!' : stories.length}]</span>
        <button className="link-btn" onClick={() => onSelectStep(null)} aria-label="Close stories">
          [×]
        </button>
      </h2>
      <div className="strip__facts small">
        {node && (
          <span>
            {node.count} stories · <b className={node.loss_rate == null ? undefined : 'c-orange-text'}>{knownPct(node.loss_rate, node.n_known)}</b> lost
          </span>
        )}
        {pinch && (
          <span className="c-orange-text">
            ▲ pinch {pts(pinch.jump)} ({pct(pinch.prev_loss_rate)}→{pct(pinch.loss_rate)}
            {pinch.n_known != null && `, ${pinch.n_known} known`})
          </span>
        )}
        {escape && <span className="c-blue">↗ {escape.exit_count} got out right after</span>}
        {next.length > 0 && (
          <span>
            <span className="muted">usually leads to </span>
            {next.map((e, i) => (
              <span key={e.to}>
                {i > 0 && <span className="muted">, </span>}
                <button className="link-btn link-btn--inline" onClick={() => onSelectStep(e.to)}>
                  {lower(e.to)}
                </button>
                <span className="muted"> ({e.count})</span>
              </span>
            ))}
          </span>
        )}
      </div>
      {error && <p className="c-orange-text small">couldn't load stories: {error.message}</p>}
      {loading && (
        <div className="strip__grid">
          {[0, 1, 2].map((i) => (
            <article key={i} className="story story--skeleton">
              <span className="muted blink">loading…</span>
            </article>
          ))}
        </div>
      )}
      {!loading && !error && stories.length === 0 && <p className="muted small">No stories for this step.</p>}
      {!loading && shown.length > 0 && (
        <div className="strip__grid">
          {shown.map((s) => (
            <article key={s.id} className="story">
              <div className="story__head">
                <span className="story__id" title={s.id}>{s.id}</span>
                <Outcome story={s} />
              </div>
              {graph.scam_type === 'all' && <div className="story__type">{lower(s.scam_type)}</div>}
              <p>{s.snippet}</p>
            </article>
          ))}
        </div>
      )}
      {!loading && stories.length > limit && (
        <button className="btn-ghost" onClick={() => setLimit((l) => l + PAGE * 2)}>
          [ show more · {stories.length - limit} left ]
        </button>
      )}
    </section>
  )
}

function Outcome({ story: s }: { story: StorySummary }) {
  if (s.outcome === 'lost_money') {
    const m = money(s.amount_lost_usd)
    return <span className="c-orange-text">{m ? `−${m}` : 'lost money'}</span>
  }
  if (s.outcome === 'escaped') return <span className="c-blue">got out</span>
  return <span className="muted">unknown</span>
}
