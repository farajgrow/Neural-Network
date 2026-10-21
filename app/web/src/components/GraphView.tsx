import { Background, Controls, ReactFlow, useNodesInitialized, useReactFlow, useStore, type NodeTypes } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useEffect, useMemo } from 'react'
import type { Graph, Step } from '../api'
import { layoutGraph } from './graphLayout'
import { StepNode } from './StepNode'

const nodeTypes: NodeTypes = { step: StepNode }

/** Re-fit when a new graph's nodes have been measured or the pane is resized. */
function AutoFit({ graphKey }: { graphKey: string }) {
  const { fitView } = useReactFlow()
  const initialized = useNodesInitialized()
  const width = useStore((s) => s.width)
  const height = useStore((s) => s.height)
  useEffect(() => {
    if (!initialized) return
    const id = requestAnimationFrame(() => fitView({ padding: 0.15, duration: 200 }))
    return () => cancelAnimationFrame(id)
  }, [initialized, graphKey, width, height, fitView])
  return null
}

interface Props {
  graph: Graph | null
  loading: boolean
  error: string | null
  selectedStep: Step | null
  onSelectStep: (step: Step | null) => void
}

export function GraphView({ graph, loading, error, selectedStep, onSelectStep }: Props) {
  const { nodes, edges } = useMemo(
    () => (graph ? layoutGraph(graph, selectedStep) : { nodes: [], edges: [] }),
    [graph, selectedStep],
  )

  if (error) return <div className="placeholder error">{error}</div>
  if (!graph) return <div className="placeholder">{loading ? 'Loading graph…' : 'No graph'}</div>

  return (
    <ReactFlow
      key={graph.scam_type}
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodeClick={(_, n) => onSelectStep(n.id as Step)}
      onPaneClick={() => onSelectStep(null)}
      nodesConnectable={false}
      fitView
      minZoom={0.2}
      colorMode="dark"
      proOptions={{ hideAttribution: true }}
    >
      <Background gap={24} />
      <Controls showInteractive={false} />
      <AutoFit graphKey={graph.scam_type} />
    </ReactFlow>
  )
}
