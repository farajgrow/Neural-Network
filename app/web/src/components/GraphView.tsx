import {
  ReactFlow,
  useReactFlow,
  useStore,
  type EdgeTypes,
  type NodeTypes,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useEffect, useMemo, useState } from 'react'
import { label, lower, type Clusters, type Graph, type GraphKey, type MatchResult, type Step } from '../api'
import { ClusterPicker } from './ClusterPicker'
import { ErrorState } from './ErrorState'
import { FlowEdge } from './FlowEdge'
import { decorate, layoutGraph, type Highlight, type Layout } from './graphLayout'
import { Legend } from './Legend'
import { StepNode } from './StepNode'
import { Window } from './Window'
import { Button } from '@/components/ui/button'

const nodeTypes: NodeTypes = { step: StepNode }
const edgeTypes: EdgeTypes = { flow: FlowEdge }

/** Re-fit when the graph changes or the pane is resized. */
function AutoFit({ graphKey, onFitted }: { graphKey: string; onFitted: (key: string) => void }) {
  const { fitView } = useReactFlow()
  const width = useStore((s) => s.width)
  const height = useStore((s) => s.height)
  useEffect(() => {
    // Node sizes are set up front, so the pane size is all fitView needs.
    if (!width || !height) return
    const id = setTimeout(() => {
      fitView({ padding: { top: 0.04, bottom: 0.04, left: 0.02, right: 0.02 }, maxZoom: 1.1 })
      onFitted(graphKey)
    }, 0)
    return () => clearTimeout(id)
  }, [graphKey, width, height, fitView, onFitted])
  return null
}

interface Props {
  graphKey: GraphKey
  graph: Graph | null
  loading: boolean
  error: Error | null
  selectedStep: Step | null
  onSelectStep: (step: Step | null) => void
  clusters: Clusters | null
  clusterId: string | null
  onClusterChange: (id: string | null) => void
  /** Highlight to draw (variant path or matched message), computed by the parent. */
  highlight: Highlight | null
  match: MatchResult | null
  onClearMatch: () => void
}

export function GraphView(props: Props) {
  const { graphKey, match } = props
  const fileName = graphKey === 'all' ? 'all_types' : graphKey
  return (
    <Window title={`fig_1 · ${fileName}.script`} label="Script graph" className="graph-window">
      <div className="window__body graph-body">
        <div className="graph-toolbar">
          {props.clusters && props.clusters.clusters.length > 0 ? (
            <ClusterPicker
              clusters={props.clusters}
              value={props.clusterId}
              onChange={props.onClusterChange}
              disabled={match != null}
            />
          ) : (
            <span />
          )}
          <div className="graph-status" aria-live="polite">
            {props.loading && <span className="muted">loading…</span>}
            {!props.loading && match && (
              <>
                <span className="muted">matched: </span>
                <b>{match.steps_so_far.length} steps</b>
                {match.likely_next_step && (
                  <>
                    <span className="muted"> → next likely </span>
                    <b className="c-orange-text">{lower(match.likely_next_step)}</b>
                  </>
                )}
                <Button variant="text" size="bare" className="ml-2" onClick={props.onClearMatch} aria-label="Clear match highlight">
                  [×]
                </Button>
              </>
            )}
            {!props.loading && !match && props.graph && (
              <span className="muted">click a step to read its stories</span>
            )}
          </div>
        </div>
        <GraphCanvas {...props} />
      </div>
    </Window>
  )
}

function GraphCanvas({ graph, loading, error, selectedStep, onSelectStep, highlight }: Props) {
  const [showAll, setShowAll] = useState(false)
  // Hide the canvas until the first fit, so the unfitted frame never flashes.
  const [fittedKey, setFittedKey] = useState<string | null>(null)
  const fitKey = graph ? `${graph.scam_type}:${showAll}` : ''
  const base = useMemo(() => (graph ? layoutGraph(graph, showAll) : null), [graph, showAll])
  const { nodes, edges } = useMemo<Layout>(
    () => (base ? decorate(base, selectedStep, highlight) : { nodes: [], edges: [], minorCount: 0 }),
    [base, selectedStep, highlight],
  )

  let content
  if (error) content = <ErrorState error={error} />
  else if (!graph)
    content = (
      <div className="state">
        <p className="muted blink">{loading ? 'loading graph…' : 'no graph selected'}</p>
      </div>
    )
  else if (graph.nodes.length === 0)
    content = (
      <div className="state">
        <h3>No stories yet</h3>
        <p className="muted">There are no stories for {label(graph.scam_type)} in the current dataset.</p>
      </div>
    )
  else
    content = (
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
        panOnDrag={false}
        zoomOnScroll={false}
        zoomOnPinch={false}
        zoomOnDoubleClick={false}
        preventScrolling={false}
        fitView
        minZoom={0.2}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
      >
        <AutoFit graphKey={fitKey} onFitted={setFittedKey} />
      </ReactFlow>
    )

  return (
    <>
      <div className="graph-scroll">
        <div className={graph && fittedKey !== fitKey ? 'graph-canvas is-fitting' : 'graph-canvas'}>{content}</div>
      </div>
      <Legend minorCount={base?.minorCount ?? 0} showAll={showAll} onToggleAll={() => setShowAll((v) => !v)} />
    </>
  )
}
