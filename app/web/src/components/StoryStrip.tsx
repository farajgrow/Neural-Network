import { useEffect, useState } from 'react'
import { knownPct, lower, money, pct, pts, type Graph, type Step, type StorySummary } from '../api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { TAG } from '@/lib/retro'

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
<Button variant="text" size="bare" className="ml-2" onClick={() => onSelectStep(null)} aria-label="Close stories">
          [×]
        </Button>
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
            <article key={i} className="story story--skeleton" aria-busy>
              <div className="story__head">
                <Skeleton className="h-3 w-2/5 border-0" />
                <Skeleton className="h-3 w-1/5 border-0" />
              </div>
              <Skeleton className="h-2.5 w-full border-0" />
              <Skeleton className="h-2.5 w-11/12 border-0" />
              <Skeleton className="h-2.5 w-4/5 border-0" />
              <Skeleton className="h-2.5 w-3/5 border-0" />
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
        <Button variant="text" size="bare" className="self-start" onClick={() => setLimit((l) => l + PAGE * 2)}>
          [ show more · {stories.length - limit} left ]
        </Button>
      )}
    </section>
  )
}

function Outcome({ story: s }: { story: StorySummary }) {
  if (s.outcome === 'lost_money') {
    const m = money(s.amount_lost_usd)
    return <Badge variant="destructive" className={TAG}>{m ? `−${m}` : 'lost money'}</Badge>
  }
  if (s.outcome === 'escaped') return <Badge className={`${TAG} bg-escape text-primary-foreground`}>got out</Badge>
  return (
    <Badge variant="outline" className={`${TAG} border border-rule py-0 font-normal text-muted-foreground`}>
      unknown
    </Badge>
  )
}
