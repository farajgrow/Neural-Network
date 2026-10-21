import { useState } from 'react'
import { api, lower, pct, type MatchResult, type ModelPrediction } from '../api'
import { Window } from './Window'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

/** Same cap as the API (SCAM_MAX_TEXT). */
const MAX_TEXT = 4000

interface Props {
  onResult?: (result: MatchResult | null) => void
}

export function MatcherPanel({ onResult }: Props) {
  const [text, setText] = useState('')
  const [result, setResult] = useState<MatchResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!text.trim() || loading) return
    setLoading(true)
    setError(null)
    try {
      const r = await api.match(text)
      setResult(r)
      onResult?.(r)
    } catch (err) {
      setError((err as Error).message)
      setResult(null)
      onResult?.(null)
    } finally {
      setLoading(false)
    }
  }

  return (
    <Window title="check_message" label="Check a message" className="matcher">
      <form onSubmit={submit} className="window__body matcher__form">
        <label htmlFor="msg" className="b">Got a message like this?</label>
        <textarea
          id="msg"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Paste a suspicious message or conversation…"
          rows={5}
          maxLength={MAX_TEXT}
          aria-describedby="msg-privacy"
        />
        <p id="msg-privacy" className="matcher__privacy muted">
          Checked by an AI service and not saved by this site. Don’t paste passwords, codes or card numbers.
          {text.length > MAX_TEXT * 0.8 && ` ${text.length}/${MAX_TEXT}`}
        </p>
        <Button
          type="submit"
          disabled={loading || !text.trim()}
          className="h-11 text-[13px] font-bold shadow-rule disabled:opacity-55"
        >
          {loading ? '[ matching… ]' : '[ find the script ]'}
        </Button>
        {loading && <ResultSkeleton />}
        {!loading && error && (
          <div className="result" role="alert">
            <p className="c-orange-text">couldn't check it: {error}</p>
          </div>
        )}
        {!loading && !error && result && <ResultCard result={result} />}
      </form>
    </Window>
  )
}

function ResultCard({ result: r }: { result: MatchResult }) {
  const next = r.likely_next_step
  const nextInSeen = next != null && r.steps_so_far.includes(next)
  return (
    <div className="result" aria-live="polite">
      <div className="result__meta">
        <span>match · {pct(r.confidence)} confident</span>
        {r.source && <SourceTag source={r.source} detail={r.source_detail} />}
      </div>
      {r.rate_limited && (
        <p className="matcher__notice" role="status">
          You’ve used your live checks for now, so this answer comes from our offline keyword matcher. Try again in a minute.
        </p>
      )}
      <div className="result__type">{lower(r.scam_type)}</div>
      {(r.steps_so_far.length > 0 || next) && (
        <ol className="result__steps">
          {r.steps_so_far.map((s, i) => (
            <li key={`${s}-${i}`} className={s === next ? 'is-next' : undefined}>
              {lower(s)}
              {r.current_step === s && i === r.steps_so_far.lastIndexOf(s) && <span className="muted"> ← furthest so far</span>}
              {s === next && ' ← likely next'}
            </li>
          ))}
          {next && !nextInSeen && <li className="is-next">{lower(next)} ← likely next</li>}
        </ol>
      )}
      <div className="loss-box">
        <span className="loss-box__pct">{pct(r.loss_rate_if_continues)}</span>
        <span className="loss-box__text">
          <span className="loss-box__label">loss rate if they continue</span>
          {r.loss_rate_if_continues == null
            ? 'not enough known cases at this step'
            : `of known cases that reached ${next ? lower(next) : 'this step'} lost money`}
        </span>
      </div>
      {r.next_step_story_count != null && r.next_step_story_count > 0 && next && (
        <div className="result__count muted small">
          {r.next_step_story_count} {r.next_step_story_count === 1 ? 'story' : 'stories'} reached {lower(next)}
          {r.context_graph && r.context_graph !== r.scam_type && ` (${lower(r.context_graph === 'all' ? 'all types' : r.context_graph)})`}
        </div>
      )}
      {r.model && <ModelBox model={r.model} />}
      <p className="result__advice">{r.advice}</p>
    </div>
  )
}

/** Our GRU's forecast, shown beside the matcher's answer and clearly labelled as ours. */
function ModelBox({ model: m }: { model: ModelPrediction }) {
  const stepName = (s: string) => lower(s)
  const about = m.eval
    ? `A small neural network (GRU) we trained on 2,054 of the stories. In ${m.eval.folds ?? 5}-fold cross-validation it named the scammer's next move ${pct(m.eval.next_top1)} of the time and ranked loss risk with AUC ${m.eval.loss_auc.toFixed(2)}, about the same as simple frequency counts.`
    : 'A small neural network (GRU) we trained on 2,054 of the stories.'
  return (
    <div className="model-box">
      <div className="model-box__head">
        <span className="loss-box__label">our model · {m.name.replace('-', ' ')}</span>
        <Tooltip>
          <TooltipTrigger asChild>
            <button type="button" className="model-box__info" aria-label="About our model">?</button>
          </TooltipTrigger>
          <TooltipContent side="left" className="max-w-[260px]">{about}</TooltipContent>
        </Tooltip>
      </div>
      <div className="muted">scammer's likely next move</div>
      <ol className="model-box__next">
        {m.next_steps.map((n, i) => (
          <li key={n.step} className={i === 0 ? 'model-box__top' : undefined}>
            <span>{stepName(n.step)}</span>
            <span>{pct(n.p)}</span>
          </li>
        ))}
      </ol>
      <div className="model-box__row">
        <span className="muted">loss risk from these steps</span>
        <span className="model-box__risk">{pct(m.p_loss)}</span>
      </div>
    </div>
  )
}

/** Short labels for where the match came from (see app/README.md). */
const SOURCE_LABELS: Record<string, string> = {
  jev: 'jev · live',
  live: 'sonnet · live',
  cache: 'cached',
  fallback: 'offline rules',
  mock: 'mock',
}

function SourceTag({ source, detail }: { source: string; detail?: string | null }) {
  const tag = (
    <Badge
      variant="outline"
      className="h-auto cursor-default rounded-none border border-rule px-[5px] py-0 text-[10px] leading-[1.6] font-normal text-muted-foreground shadow-none"
    >
      {SOURCE_LABELS[source] ?? source}
    </Badge>
  )
  if (!detail) return tag
  return (
    <Tooltip>
      <TooltipTrigger asChild>{tag}</TooltipTrigger>
      <TooltipContent side="left">{detail}</TooltipContent>
    </Tooltip>
  )
}

/** Placeholder shaped like the result card while the matcher runs. */
function ResultSkeleton() {
  return (
    <div className="result" aria-live="polite" aria-busy>
      <p className="muted small blink">matching… reading the message against known scripts</p>
      <Skeleton className="h-6 w-1/2 border-0" />
      <div className="flex flex-col gap-1.5">
        <Skeleton className="h-3 w-3/4 border-0" />
        <Skeleton className="h-3 w-2/3 border-0" />
        <Skeleton className="h-3 w-1/2 border-0" />
      </div>
      <Skeleton className="h-14 w-full border-destructive/40 bg-orange-tint/70" />
    </div>
  )
}
