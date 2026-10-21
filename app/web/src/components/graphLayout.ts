import type { Edge, Node } from '@xyflow/react'
import type { Graph, GraphNode } from '../api'

export type StepNodeData = GraphNode & {
  selected: boolean
  isPinch: boolean
  isEscape: boolean
}
export type StepFlowNode = Node<StepNodeData, 'step'>

const COL_WIDTH = 220
const ROW_HEIGHT = 110
const N_COLUMNS = 8

/**
 * Left-to-right layout: avg_position (0..1) picks a column, nodes sharing a
 * column are stacked and vertically centred.
 */
export function layoutGraph(graph: Graph, selected: string | null): { nodes: StepFlowNode[]; edges: Edge[] } {
  const pinch = new Set(graph.pinch_points.map((p) => p.step))
  const escape = new Set(graph.escape_points.map((p) => p.step))

  const columns = new Map<number, GraphNode[]>()
  for (const n of [...graph.nodes].sort((a, b) => a.avg_position - b.avg_position)) {
    const col = Math.round(n.avg_position * (N_COLUMNS - 1))
    if (!columns.has(col)) columns.set(col, [])
    columns.get(col)!.push(n)
  }

  const nodes: StepFlowNode[] = []
  for (const [col, members] of columns) {
    members.forEach((n, i) => {
      nodes.push({
        id: n.step,
        type: 'step',
        position: { x: col * COL_WIDTH, y: (i - (members.length - 1) / 2) * ROW_HEIGHT },
        data: { ...n, selected: n.step === selected, isPinch: pinch.has(n.step), isEscape: escape.has(n.step) },
      })
    })
  }

  const maxEdge = Math.max(1, ...graph.edges.map((e) => e.count))
  const edges: Edge[] = graph.edges.map((e) => ({
    id: `${e.from}->${e.to}`,
    source: e.from,
    target: e.to,
    label: String(e.count),
    style: { strokeWidth: 1 + (4 * e.count) / maxEdge },
    data: { count: e.count },
  }))

  return { nodes, edges }
}
