import {
  Background,
  BackgroundVariant,
  Controls,
  ReactFlow,
  useNodesInitialized,
  useReactFlow,
  useStore,
  type EdgeTypes,
  type NodeTypes,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useEffect, useMemo } from 'react'
import { label, type Clusters, type Graph, type MatchResult, type Step } from '../api'
import { ClusterPicker } from './ClusterPicker'
import { ErrorState } from './ErrorState'
import { FlowEdge } from './FlowEdge'
import { decorate, layoutGraph, type Highlight } from './graphLayout'
import { Legend } from './Legend'
import { StepNode } from './StepNode'

const nodeTypes: NodeTypes = { step: StepNode }
const edgeTypes: EdgeTypes = { flow: FlowEdge }

/** Re-fit when a new graph's nodes have been measured or the pane is resized. */
function AutoFit({ graphKey }: { graphKey: string }) {
  const { fitView } = useReactFlow()
  const initialized = useNodesInitialized()
  const width = useStore((s) => s.width)
  const height = useStore((s) => s.height)
  useEffect(() => {
    if (!initialized) return
    const id = requestAnimationFrame(() => fitView({ padding: { top: 0.16, bottom: 0.2, left: 0.04, right: 0.04 }, maxZoom: 1.15, duration: 250 }))
    return () => cancelAnimationFrame(id)
  }, [initialized, graphKey, width, height, fitView])
  return null
}

interface Props {
  graph: Graph | null
  loading: boolean
  error: Error | null
  selectedStep: Step | null
  onSelectStep: (step: Step | null) => void
  clusters: Clusters | null
  clusterId: string | null
  onClusterChange: (id: string | null) => void
  /** Highlight to draw (cluster path or matched message), computed by the parent. */
  highlight: Highlight | null
  match: MatchResult | null
  onClearMatch: () => void
}

export function GraphView(props: Props) {
  const { graph, loading, error, selectedStep, onSelectStep, highlight, match } = props
  const base = useMemo(() => (graph ? layoutGraph(graph) : null), [graph])
  const { nodes, edges } = useMemo(
    () => (base ? decorate(base, selectedStep, highlight) : { nodes: [], edges: [] }),
    [base, selectedStep, highlight],
  )

  if (error) return <ErrorState error={error} />
  if (!graph) {
    return (
      <div className="state">
        <div className="spinner" aria-hidden />
        <p className="muted">{loading ? 'Loading graph…' : 'No graph selected'}</p>
      </div>
    )
  }
  if (graph.nodes.length === 0) {
    return (
      <div className="state">
        <h3>No stories yet</h3>
        <p className="muted">There are no stories for {label(graph.scam_type)} in the current dataset.</p>
      </div>
    )
  }

  return (
    <>
      <ReactFlow
        key={graph.scam_type}
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodeClick={(_, n) => onSelectStep(n.id === selectedStep ? null : (n.id as Step))}
        onPaneClick={() => onSelectStep(null)}
        nodesConnectable={false}
        nodesDraggable={false}
        elementsSelectable={false}
        fitView
        minZoom={0.25}
        maxZoom={2}
        colorMode="dark"
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} color="#252c38" />
        <Controls showInteractive={false} position="bottom-right" />
        <AutoFit graphKey={graph.scam_type} />
      </ReactFlow>

      <div className="graph-overlay graph-overlay--top">
        {props.clusters && props.clusters.clusters.length > 0 && !match && (
          <ClusterPicker clusters={props.clusters} value={props.clusterId} onChange={props.onClusterChange} />
        )}
        {match && (
          <div className="match-chip">
            <span className="match-chip__pulse" />
            <span>
              Matched message · {match.steps_so_far.length} steps seen
              {match.likely_next_step && (
                <>
                  , next likely <strong>{label(match.likely_next_step)}</strong>
                </>
              )}
            </span>
            <button className="match-chip__close" onClick={props.onClearMatch} aria-label="Clear match highlight">
              ✕
            </button>
          </div>
        )}
        {loading && <span className="loading-pill">Loading…</span>}
      </div>
      <div className="graph-overlay graph-overlay--bottom">
        <Legend />
      </div>
    </>
  )
}
