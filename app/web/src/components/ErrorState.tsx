import { ApiError } from '../api'

/** Friendly explanation for the API failures we expect during setup. */
function describeError(err: Error): { title: string; hint: string; command?: string } {
  const status = err instanceof ApiError ? err.status : -1
  if (status === 503 && /graphs/i.test(err.message)) {
    return {
      title: 'No graphs built yet',
      hint: 'The API is running but analysis/graphs.json is missing. Build it from the repo root, then refresh.',
      command: 'python -m analysis.build',
    }
  }
  if (status === 503) {
    return { title: 'Data not available', hint: err.message }
  }
  if (status === 0 || status === 500 || status === 502 || status === 504) {
    return {
      title: "Can't reach the API",
      hint: 'Start it from the repo root, then refresh.',
      command: '.venv/bin/uvicorn app.api.main:app --port 8000',
    }
  }
  return { title: 'Something went wrong', hint: err.message }
}

export function ErrorState({ error }: { error: Error }) {
  const { title, hint, command } = describeError(error)
  return (
    <div className="state">
      <div className="state__icon state__icon--error" aria-hidden>!</div>
      <h3>{title}</h3>
      <p className="muted">{hint}</p>
      {command && <code className="state__cmd">{command}</code>}
    </div>
  )
}
