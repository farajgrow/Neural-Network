import {
  ReactFlow,
  useReactFlow,
  useStore,
  type EdgeTypes,
  type NodeTypes,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useEffect, useMemo, useRef, useState } from 'react'
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

/** Phones get the top-to-bottom layout (same breakpoint as the CSS). */
const NARROW = '(max-width: 720px)'

function useNarrow(): boolean {
  const [narrow, setNarrow] = useState(() => window.matchMedia(NARROW).matches)
  useEffect(() => {
    const mq = window.matchMedia(NARROW)
    const on = () => setNarrow(mq.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])
  return narrow
}

/** Width of an element, kept up to date. */
function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null)
  const [width, setWidth] = useState(0)
  useEffect(() => {
    if (!ref.current) return
    const ro = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width))
    ro.observe(ref.current)
    return () => ro.disconnect()
  }, [])
  return [ref, width]
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
  const narrow = useNarrow()
  const [scrollRef, scrollWidth] = useWidth<HTMLDivElement>()
  const fitKey = graph ? `${graph.scam_type}:${showAll}:${narrow}` : ''
  const base = useMemo(() => (graph ? layoutGraph(graph, showAll, narrow) : null), [graph, showAll, narrow])
  const { nodes, edges } = useMemo<Layout>(
    () =>
      base
        ? decorate(base, selectedStep, highlight)
        : { nodes: [], edges: [], minorCount: 0, bounds: { width: 0, height: 0 } },
    [base, selectedStep, highlight],
  )
  // Vertical layout: make the canvas as tall as the graph is at this width, so it fits without scrolling sideways.
  const canvasStyle =
    narrow && base && base.bounds.width > 0 && scrollWidth > 0
      ? { height: Math.round((scrollWidth * 0.96 * base.bounds.height) / base.bounds.width) + 16 }
      : undefined

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
      <div className="graph-scroll" ref={scrollRef}>
        <div className={graph && fittedKey !== fitKey ? 'graph-canvas is-fitting' : 'graph-canvas'} style={canvasStyle}>
          {content}
        </div>
      </div>
      <Legend minorCount={base?.minorCount ?? 0} showAll={showAll} onToggleAll={() => setShowAll((v) => !v)} />
    </>
  )
}
