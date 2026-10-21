import type { CSSProperties } from 'react'
import { humanize, label, money, pct, pts, type Cluster, type Graph, type Step, type StorySummary } from '../api'
import { lossColor } from '../colors'

interface Props {
  graph: Graph | null
  step: Step | null
  onSelectStep: (step: Step | null) => void
  stories: StorySummary[]
  loading: boolean
  error: Error | null
  cluster: (Cluster & { color: string; index: number }) | null
  onClearCluster: () => void
}

const lossStyle = (r: number | null | undefined) => ({ color: lossColor(r) }) as CSSProperties

export function SidePanel(props: Props) {
  const { graph, step } = props
  let body
  if (!graph) body = <p className="muted">Nothing to show until a graph loads.</p>
  else if (step) body = <StepDetail {...props} graph={graph} step={step} />
  else body = (
    <>
      {props.cluster && <ClusterCard cluster={props.cluster} onClear={props.onClearCluster} />}
      <Overview graph={graph} onSelectStep={props.onSelectStep} />
    </>
  )
  return <aside className="panel side-panel">{body}</aside>
}

function OutcomeBar({ graph }: { graph: Graph }) {
  const { lost_money = 0, escaped = 0, unknown = 0 } = graph.outcome_counts
  const total = Math.max(1, lost_money + escaped + unknown)
  return (
    <div className="outcome-bar">
      <div className="outcome-bar__track">
        <span className="seg seg--lost" style={{ flexGrow: lost_money / total }} />
        <span className="seg seg--escaped" style={{ flexGrow: escaped / total }} />
        <span className="seg seg--unknown" style={{ flexGrow: unknown / total }} />
      </div>
      <div className="outcome-bar__legend small">
        <span><i className="dot dot--lost" />{lost_money} lost money</span>
        <span><i className="dot dot--escaped" />{escaped} escaped</span>
        {unknown > 0 && <span><i className="dot dot--unknown" />{unknown} unknown</span>}
      </div>
    </div>
  )
}

function Overview({ graph, onSelectStep }: { graph: Graph; onSelectStep: (s: Step) => void }) {
  return (
    <div className="overview">
      <div className="kicker">{graph.scam_type === 'all' ? 'All scam types' : label(graph.scam_type)}</div>
      <p className="headline">
        <strong>{graph.n_stories}</strong> stories,{' '}
        <strong style={lossStyle(graph.loss_rate)}>{pct(graph.loss_rate)}</strong> lost money
      </p>
      <OutcomeBar graph={graph} />

      <section className="section">
        <h3 className="section__title">
          <span className="node-badge node-badge--pinch">▲</span> Pinch points
        </h3>
        <p className="section__hint">Where the loss rate jumps compared with the steps that lead in.</p>
        {graph.pinch_points.length === 0 && <p className="muted small">None found for this type.</p>}
        <ol className="rank-list">
          {graph.pinch_points.map((p) => (
            <li key={p.step}>
              <button className="rank-row" onClick={() => onSelectStep(p.step)}>
                <span className="rank-row__name">{label(p.step)}</span>
                <span className="rank-row__detail">
                  {pct(p.prev_loss_rate)} → <span style={lossStyle(p.loss_rate)}>{pct(p.loss_rate)}</span>
                </span>
                <span className="pill pill--pinch">{pts(p.jump)}</span>
              </button>
            </li>
          ))}
        </ol>
      </section>

      <section className="section">
        <h3 className="section__title">
          <span className="node-badge node-badge--escape">↗</span> Escape points
        </h3>
        <p className="section__hint">Steps most often followed directly by the victim getting out.</p>
        {graph.escape_points.length === 0 && <p className="muted small">None found for this type.</p>}
        <ol className="rank-list">
          {graph.escape_points.map((p) => (
            <li key={p.step}>
              <button className="rank-row" onClick={() => onSelectStep(p.step)}>
                <span className="rank-row__name">{label(p.step)}</span>
                <span className="rank-row__detail">{pct(p.exit_rate)} of passes</span>
                <span className="pill pill--escape">
                  {p.exit_count} {p.exit_count === 1 ? 'exit' : 'exits'}
                </span>
              </button>
            </li>
          ))}
        </ol>
      </section>

      <p className="footnote">
        Associations in self-reported stories, not causes. People who lost money are more likely to post.
      </p>
    </div>
  )
}

function ClusterCard({ cluster, onClear }: { cluster: Cluster & { color: string; index: number }; onClear: () => void }) {
  const mix = Object.entries(cluster.scam_type_counts).sort((a, b) => (b[1] ?? 0) - (a[1] ?? 0))
  return (
    <div className="cluster-card" style={{ '--vc': cluster.color } as CSSProperties}>
      <div className="cluster-card__head">
        <span className="kicker" style={{ color: cluster.color }}>
          {cluster.outlier ? 'Outlier stories' : `Script variant ${cluster.index + 1}`}
        </span>
        <button className="link" onClick={onClear}>Clear</button>
      </div>
      <p className="cluster-card__label">{humanize(cluster.label)}</p>
      <dl className="stats stats--3">
        <div><dt>Stories</dt><dd>{cluster.size}</dd></div>
        <div><dt>Lost money</dt><dd style={lossStyle(cluster.loss_rate)}>{pct(cluster.loss_rate)}</dd></div>
        <div><dt>Steps</dt><dd>{cluster.typical_sequence.length}</dd></div>
      </dl>
      <div className="mix">
        {mix.map(([t, n]) => (
          <div key={t} className="mix__row">
            <span className="mix__name">{label(t)}</span>
            <span className="mix__bar"><span style={{ width: `${((n ?? 0) / cluster.size) * 100}%` }} /></span>
            <span className="mix__n">{n}</span>
          </div>
        ))}
      </div>
      <div className="chain">
        {cluster.typical_sequence.map((s, i) => (
          <span key={`${s}-${i}`} className="chain__step">
            <span className="chain__i">{i + 1}</span>
            {label(s)}
          </span>
        ))}
      </div>
    </div>
  )
}

function StepDetail({ graph, step, stories, loading, error, onSelectStep }: Props & { graph: Graph; step: Step }) {
  const node = graph.nodes.find((n) => n.step === step)
  const pinch = graph.pinch_points.find((p) => p.step === step)
  const escape = graph.escape_points.find((p) => p.step === step)
  const next = graph.edges
    .filter((e) => e.from === step)
    .sort((a, b) => b.count - a.count)
    .slice(0, 4)
  const nextTotal = next.reduce((s, e) => s + e.count, 0)
  const isOutcome = step === 'money_lost' || step === 'victim_exits'

  return (
    <div className="step-detail">
      <button className="link back" onClick={() => onSelectStep(null)}>← Overview</button>
      <div className="kicker">{isOutcome ? 'Outcome' : 'Step'} · {label(graph.scam_type)}</div>
      <h2 className="step-detail__name">{label(step)}</h2>
      {node && (
        <dl className="stats">
          <div><dt>Stories</dt><dd>{node.count}</dd></div>
          <div><dt>Lost money</dt><dd style={lossStyle(node.loss_rate)}>{pct(node.loss_rate)}</dd></div>
        </dl>
      )}
      {pinch && (
        <div className="callout callout--pinch">
          <strong>Pinch point · {pts(pinch.jump)} loss rate</strong>
          <span>
            {pct(pinch.prev_loss_rate)} of victims at the steps before lost money; {pct(pinch.loss_rate)} of those who
            reached this step did.
          </span>
        </div>
      )}
      {escape && (
        <div className="callout callout--escape">
          <strong>Escape point · {escape.exit_count} got out here</strong>
          <span>{pct(escape.exit_rate)} of stories through this step end with the victim exiting right after.</span>
        </div>
      )}
      {next.length > 0 && (
        <section className="section">
          <h3 className="section__title">Usually leads to</h3>
          <ul className="next-list">
            {next.map((e) => (
              <li key={e.to}>
                <button className="next-row" onClick={() => onSelectStep(e.to)}>
                  <span>{label(e.to)}</span>
                  <span className="next-row__bar"><span style={{ width: `${(e.count / nextTotal) * 100}%` }} /></span>
                  <span className="muted small">{e.count}</span>
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="section">
        <h3 className="section__title">
          Stories {!loading && !error && <span className="muted">({stories.length})</span>}
        </h3>
        {error && <p className="error small">Couldn't load stories: {error.message}</p>}
        {loading && (
          <ul className="story-list">
            {[0, 1, 2].map((i) => <li key={i} className="story story--skeleton" />)}
          </ul>
        )}
        {!loading && !error && stories.length === 0 && <p className="muted small">No stories for this step.</p>}
        {!loading && (
          <ul className="story-list">
            {stories.map((s) => (
              <li key={s.id} className="story">
                <div className="story__head">
                  <span className={`badge outcome-${s.outcome}`}>{label(s.outcome)}</span>
                  {money(s.amount_lost_usd) && <span className="story__amount">{money(s.amount_lost_usd)} lost</span>}
                  {graph.scam_type === 'all' && <span className="story__type">{label(s.scam_type)}</span>}
                </div>
                <p>{s.snippet}</p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
