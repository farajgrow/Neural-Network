import { useState } from 'react'
import { api, lower, pct, type MatchResult } from '../api'
import { Window } from './Window'

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
        />
        <button type="submit" className="btn-primary" disabled={loading || !text.trim()}>
          {loading ? '[ matching… ]' : '[ find the script ]'}
        </button>
        {loading && (
          <div className="result" aria-live="polite">
            <p className="muted blink">matching… reading the message against known scripts</p>
          </div>
        )}
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
        {r.source && (
          <span className="source-tag" title={r.source_detail ?? undefined}>
            {r.source}
          </span>
        )}
      </div>
      <div className="result__type">{lower(r.scam_type)}</div>
      {(r.steps_so_far.length > 0 || next) && (
        <ol className="result__steps">
          {r.steps_so_far.map((s, i) => (
            <li key={`${s}-${i}`} className={s === next ? 'is-next' : undefined}>
              {lower(s)}
              {r.current_step === s && i === r.steps_so_far.lastIndexOf(s) && <span className="muted"> ← now</span>}
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
      <p className="result__advice">{r.advice}</p>
    </div>
  )
}
