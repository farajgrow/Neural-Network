import { useEffect, useState } from 'react'
import { api, type Graph, type GraphKey, type MatchResult, type ScamTypeSummary, type Step, type StorySummary } from './api'
import { GraphView } from './components/GraphView'
import { MatcherPanel } from './components/MatcherPanel'
import { ScamTypeSwitcher } from './components/ScamTypeSwitcher'
import { SidePanel } from './components/SidePanel'

export default function App() {
  const [types, setTypes] = useState<ScamTypeSummary[]>([])
  const [typesError, setTypesError] = useState<string | null>(null)
  const [scamType, setScamType] = useState<GraphKey>('all')

  const [graph, setGraph] = useState<Graph | null>(null)
  const [graphLoading, setGraphLoading] = useState(false)
  const [graphError, setGraphError] = useState<string | null>(null)

  const [step, setStep] = useState<Step | null>(null)
  const [stories, setStories] = useState<StorySummary[]>([])
  const [storiesLoading, setStoriesLoading] = useState(false)
  const [storiesError, setStoriesError] = useState<string | null>(null)

  // Kept in state so the graph can highlight matched steps later (F7).
  const [, setMatch] = useState<MatchResult | null>(null)

  useEffect(() => {
    api
      .scamTypes()
      .then((t) => {
        setTypes(t)
        if (t.length > 0) setScamType(t[0].scam_type)
      })
      .catch((e: Error) => setTypesError(e.message))
  }, [])

  useEffect(() => {
    let cancelled = false
    setGraphLoading(true)
    setGraphError(null)
    setStep(null)
    api
      .graph(scamType)
      .then((g) => !cancelled && setGraph(g))
      .catch((e: Error) => {
        if (cancelled) return
        setGraph(null)
        setGraphError(e.message)
      })
      .finally(() => !cancelled && setGraphLoading(false))
    return () => {
      cancelled = true
    }
  }, [scamType])

  useEffect(() => {
    if (!step) {
      setStories([])
      return
    }
    let cancelled = false
    setStoriesLoading(true)
    setStoriesError(null)
    api
      .stories({ scam_type: scamType, step })
      .then((s) => !cancelled && setStories(s))
      .catch((e: Error) => !cancelled && setStoriesError(e.message))
      .finally(() => !cancelled && setStoriesLoading(false))
    return () => {
      cancelled = true
    }
  }, [scamType, step])

  return (
    <div className="app">
      <header className="header">
        <div>
          <h1>Scam Scripts</h1>
          <p className="tagline">Real scam victim stories mapped into scripts: where people get hooked, and where they get out.</p>
        </div>
        <ScamTypeSwitcher types={types} value={scamType} onChange={setScamType} />
      </header>
      {typesError && <div className="banner error">{typesError}</div>}
      <main className="layout">
        <section className="panel graph-area">
          <GraphView
            graph={graph}
            loading={graphLoading}
            error={graphError}
            selectedStep={step}
            onSelectStep={setStep}
          />
        </section>
        <SidePanel graph={graph} step={step} stories={stories} loading={storiesLoading} error={storiesError} />
        <MatcherPanel onResult={setMatch} />
      </main>
    </div>
  )
}
