import { useEffect, useMemo, useState } from 'react'
import {
  api,
  type Clusters,
  type Graph,
  type GraphKey,
  type MatchResult,
  type ScamTypeSummary,
  type Step,
  type StorySummary,
} from './api'
import { GraphView } from './components/GraphView'
import type { Highlight } from './components/graphLayout'
import { MatcherPanel } from './components/MatcherPanel'
import { ScamTypeSwitcher } from './components/ScamTypeSwitcher'
import { StoryStrip } from './components/StoryStrip'
import { SummaryBox } from './components/SummaryBox'

export default function App() {
  const [types, setTypes] = useState<ScamTypeSummary[]>([])
  const [typesError, setTypesError] = useState<Error | null>(null)
  const [allLossRate, setAllLossRate] = useState<number | null>(null)
  const [scamType, setScamType] = useState<GraphKey>('all')

  const [graph, setGraph] = useState<Graph | null>(null)
  const [graphLoading, setGraphLoading] = useState(false)
  const [graphError, setGraphError] = useState<Error | null>(null)

  const [step, setStep] = useState<Step | null>(null)
  const [stories, setStories] = useState<StorySummary[]>([])
  const [storiesLoading, setStoriesLoading] = useState(false)
  const [storiesError, setStoriesError] = useState<Error | null>(null)

  const [clusters, setClusters] = useState<Clusters | null>(null)
  const [clusterId, setClusterId] = useState<string | null>(null)

  const [match, setMatch] = useState<MatchResult | null>(null)

  useEffect(() => {
    api
      .scamTypes()
      .then((t) => {
        setTypes(t)
        if (t.length > 0) setScamType(t[0].scam_type)
      })
      .catch((e: Error) => setTypesError(e))
    api.graph('all').then((g) => setAllLossRate(g.loss_rate)).catch(() => {})
    // Clusters are optional: hide the control if they are missing or fail to load.
    api.clusters().then(setClusters).catch(() => setClusters(null))
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
        setGraphError(e)
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
      .catch((e: Error) => !cancelled && setStoriesError(e))
      .finally(() => !cancelled && setStoriesLoading(false))
    return () => {
      cancelled = true
    }
  }, [scamType, step])

  const cluster = useMemo(() => {
    if (!clusters || !clusterId) return null
    const index = clusters.clusters.findIndex((c) => c.id === clusterId)
    if (index < 0) return null
    return { ...clusters.clusters[index], index }
  }, [clusters, clusterId])

  // A matched message takes precedence over a cluster highlight.
  const highlight = useMemo<Highlight | null>(() => {
    if (match) return { kind: 'match', sequence: match.steps_so_far, next: match.likely_next_step }
    if (cluster) return { kind: 'cluster', sequence: cluster.typical_sequence }
    return null
  }, [match, cluster])

  function onMatch(r: MatchResult | null) {
    setMatch(r)
    if (!r) return
    setClusterId(null)
    setStep(null)
    setScamType(types.some((t) => t.scam_type === r.scam_type) ? r.scam_type : 'all')
  }

  function onClusterChange(id: string | null) {
    setClusterId(id)
    setStep(null)
  }

  const totalStories = types.reduce((n, t) => n + t.n_stories, 0)

  return (
    <div className="app">
      <header className="menubar">
        <div className="brand">
          <span className="brand__mark" aria-hidden />
          SCAM SCRIPTS
          {totalStories > 0 && <span className="brand__sub">/ {totalStories} victim stories mapped</span>}
        </div>
        <ScamTypeSwitcher types={types} allLossRate={allLossRate} value={scamType} onChange={setScamType} />
      </header>
      {typesError && !graphError && <div className="banner">couldn't load scam types: {typesError.message}</div>}
      <div className="page">
        <h1 className="headline">
          Where victims get hooked,
          <br />
          and where they get out.
        </h1>
        <div className="columns">
          <main className="col-main">
            <GraphView
              graphKey={scamType}
              graph={graph}
              loading={graphLoading}
              error={graphError}
              selectedStep={step}
              onSelectStep={setStep}
              clusters={clusters}
              clusterId={clusterId}
              onClusterChange={onClusterChange}
              highlight={highlight}
              match={match}
              onClearMatch={() => setMatch(null)}
            />
            <StoryStrip
              graph={graph}
              step={step}
              onSelectStep={setStep}
              stories={stories}
              loading={storiesLoading}
              error={storiesError}
            />
          </main>
          <aside className="col-side">
            <MatcherPanel onResult={onMatch} />
            <SummaryBox
              graph={graph}
              cluster={match ? null : cluster}
              onClearCluster={() => setClusterId(null)}
              onSelectStep={setStep}
            />
          </aside>
        </div>
      </div>
    </div>
  )
}
