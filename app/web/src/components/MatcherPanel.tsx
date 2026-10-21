import { useState } from 'react'
import { api, label, pct, type MatchResult } from '../api'

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
    if (!text.trim()) return
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
    <section className="panel matcher">
      <form onSubmit={submit} className="matcher__form">
        <h2>Check a message</h2>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Paste a suspicious message or conversation…"
          rows={5}
        />
        <button type="submit" className="primary" disabled={loading || !text.trim()}>
          {loading ? 'Checking…' : 'Check'}
        </button>
        {error && <p className="error">{error}</p>}
      </form>
      {result && <ResultCard result={result} />}
    </section>
  )
}

function ResultCard({ result }: { result: MatchResult }) {
  return (
    <div className="result-card">
      <div className="result-card__head">
        <span className="result-card__type">{label(result.scam_type)}</span>
        <span className="muted">{pct(result.confidence)} confidence</span>
        {result.source === 'mock' && <span className="badge">mock</span>}
      </div>
      <dl className="result-card__grid">
        <div>
          <dt>Where you are</dt>
          <dd>{result.steps_so_far.length ? label(result.steps_so_far[result.steps_so_far.length - 1]) : '—'}</dd>
        </div>
        <div>
          <dt>What usually comes next</dt>
          <dd>{label(result.likely_next_step)}</dd>
        </div>
        <div>
          <dt>Lost money if it continues</dt>
          <dd className="result-card__loss">{pct(result.loss_rate_if_continues)}</dd>
        </div>
      </dl>
      <p className="result-card__advice">{result.advice}</p>
      {result.steps_so_far.length > 0 && (
        <p className="muted small">Steps seen: {result.steps_so_far.map(label).join(' → ')}</p>
      )}
    </div>
  )
}
