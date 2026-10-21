import dagre from '@dagrejs/dagre'
import type { Edge, Node } from '@xyflow/react'
import { OUTCOME_STEPS, type EscapePoint, type Graph, type GraphNode, type PinchPoint, type Step } from '../api'
import { lossColor, LOST, SAFE } from '../colors'

// Node geometry. Each node is a fixed-width box: badge zone, circle, label zone.
// The badge and label zones have the same height so the circle sits on the box's
// vertical centre, which is where dagre routes edges to.
export const NODE_W = 94
export const ZONE_H = 36
const D_MIN = 30
const D_MAX = 70
const RANK_SEP = 20
const NODE_SEP = 40

export type NodeState = 'normal' | 'dim' | 'path' | 'next'
export type EdgeState = 'normal' | 'dim' | 'path' | 'next' | 'focus'

export type StepNodeData = GraphNode & {
  diameter: number
  color: string
  isOutcome: boolean
  pinch?: PinchPoint
  escape?: EscapePoint
  // Interaction state, filled in by decorate().
  selected: boolean
  state: NodeState
  seqIndex?: number
  hlColor?: string
}
export type StepFlowNode = Node<StepNodeData, 'step'>

export type FlowEdgeData = {
  count: number
  width: number
  opacity: number
  sourceColor: string
  targetColor: string
  /** Interior bend points from dagre (absolute coordinates), empty for short edges. */
  points: { x: number; y: number }[]
  /** Goes against the avg_position order (drawn as a dashed loop). */
  back: boolean
  /** Rare transition: hidden unless it is part of the focus or a highlight. */
  minor: boolean
  state: EdgeState
  hlColor?: string
}
export type FlowEdge = Edge<FlowEdgeData, 'flow'>

export interface Layout {
  nodes: StepFlowNode[]
  edges: FlowEdge[]
  /** Number of rare transitions hidden by default. */
  minorCount: number
}

/** What to emphasise on the graph: a cluster's typical path or a matched message. */
export interface Highlight {
  kind: 'cluster' | 'match'
  color: string
  sequence: Step[]
  next?: Step | null
}

const nodeColor = (n: GraphNode) =>
  n.step === 'money_lost' ? LOST : n.step === 'victim_exits' ? SAFE : lossColor(n.loss_rate)

const edgeId = (from: string, to: string) => `${from}->${to}`

/**
 * Layered left-to-right layout.
 *
 * Steps are ordered by avg_position (outcomes last). Edges that go against that
 * order are dropped for layering, which leaves a DAG; dagre then assigns ranks
 * (network simplex, weighted by edge count), orders nodes within ranks to reduce
 * crossings and routes long edges around nodes. Outcome nodes are moved to a
 * final column of their own.
 *
 * On bigger datasets most transitions are rare and turn the graph into a
 * hairball, so edges below ~4% of stories are "minor": left out of the layout and
 * only drawn when they touch the selected step or a highlighted path. Every step
 * keeps its strongest incoming and outgoing edge. Back-edges need twice the count.
 */
export function layoutGraph(graph: Graph, showAll = false): Layout {
  const pinch = new Map(graph.pinch_points.map((p) => [p.step, p]))
  const escape = new Map(graph.escape_points.map((p) => [p.step, p]))
  const maxCount = Math.max(1, ...graph.nodes.map((n) => n.count))
  const diameter = (count: number) => Math.round(D_MIN + (D_MAX - D_MIN) * Math.sqrt(count / maxCount))
  const height = (count: number) => diameter(count) + 2 * ZONE_H

  const order = [...graph.nodes].sort(
    (a, b) =>
      Number(OUTCOME_STEPS.has(a.step)) - Number(OUTCOME_STEPS.has(b.step)) ||
      a.avg_position - b.avg_position ||
      b.count - a.count ||
      a.step.localeCompare(b.step),
  )
  const rankOf = new Map(order.map((n, i) => [n.step, i]))
  const isForward = (from: Step, to: Step) => (rankOf.get(from) ?? 0) < (rankOf.get(to) ?? 0)

  const edgesIn = graph.edges.filter((e) => e.from !== e.to && rankOf.has(e.from) && rankOf.has(e.to))
  const minCount = Math.max(1, Math.ceil(graph.n_stories * 0.04))
  const strongest = new Set<string>()
  for (const key of ['from', 'to'] as const) {
    const best = new Map<string, (typeof edgesIn)[number]>()
    for (const e of edgesIn) {
      if (!isForward(e.from, e.to)) continue
      const cur = best.get(e[key])
      if (!cur || e.count > cur.count) best.set(e[key], e)
    }
    for (const e of best.values()) strongest.add(edgeId(e.from, e.to))
  }
  const isMinor = (e: (typeof edgesIn)[number]) => {
    if (showAll) return false
    if (!isForward(e.from, e.to)) return e.count < Math.max(2, 2 * minCount)
    return e.count < minCount && !strongest.has(edgeId(e.from, e.to))
  }

  const g = new dagre.graphlib.Graph()
  g.setGraph({ rankdir: 'LR', ranksep: RANK_SEP, nodesep: NODE_SEP, edgesep: 8, marginx: 0, marginy: 0 })
  g.setDefaultEdgeLabel(() => ({}))
  for (const n of order) g.setNode(n.step, { width: NODE_W, height: height(n.count) })
  for (const e of edgesIn) {
    if (isForward(e.from, e.to) && !isMinor(e)) g.setEdge(e.from, e.to, { weight: e.count, minlen: 1 })
  }
  dagre.layout(g)

  const centre = new Map<string, { x: number; y: number }>()
  for (const n of order) {
    const p = g.node(n.step)
    centre.set(n.step, { x: p.x, y: p.y })
  }

  // Outcomes get their own last column, money_lost above victim_exits.
  const outcomes = order
    .filter((n) => OUTCOME_STEPS.has(n.step))
    .sort((a, b) => Number(b.step === 'money_lost') - Number(a.step === 'money_lost'))
  const steps = order.filter((n) => !OUTCOME_STEPS.has(n.step))
  if (outcomes.length && steps.length) {
    const lastX = Math.max(...steps.map((n) => centre.get(n.step)!.x))
    const ys = steps.map((n) => centre.get(n.step)!.y)
    const midY = (Math.min(...ys) + Math.max(...ys)) / 2
    const gap = 64
    const total = outcomes.reduce((s, n) => s + height(n.count), 0) + gap * (outcomes.length - 1)
    let y = midY - total / 2
    for (const n of outcomes) {
      const h = height(n.count)
      centre.set(n.step, { x: lastX + NODE_W + RANK_SEP * 2.4, y: y + h / 2 })
      y += h + gap
    }
  }

  const nodes: StepFlowNode[] = order.map((n) => {
    const c = centre.get(n.step)!
    const h = height(n.count)
    return {
      id: n.step,
      type: 'step',
      position: { x: c.x - NODE_W / 2, y: c.y - h / 2 },
      width: NODE_W,
      height: h,
      data: {
        ...n,
        diameter: diameter(n.count),
        color: nodeColor(n),
        isOutcome: OUTCOME_STEPS.has(n.step),
        pinch: pinch.get(n.step),
        escape: escape.get(n.step),
        selected: false,
        state: 'normal',
      },
    }
  })
  const byStep = new Map(nodes.map((n) => [n.id, n.data]))

  const maxEdge = Math.max(1, ...graph.edges.map((e) => e.count))
  let minorCount = 0
  const edges: FlowEdge[] = edgesIn
    .map((e) => {
      const forward = isForward(e.from, e.to)
      const minor = isMinor(e)
      if (minor) minorCount++
      const dEdge = forward && !minor ? g.edge(e.from, e.to) : undefined
      // Outcome nodes were moved after layout, so their routed bends no longer apply.
      const points = dEdge?.points && !OUTCOME_STEPS.has(e.to) ? dEdge.points.slice(1, -1) : []
      const t = e.count / maxEdge
      return {
        id: edgeId(e.from, e.to),
        type: 'flow',
        source: e.from,
        target: e.to,
        // Heavy edges on top of light ones.
        zIndex: Math.round(t * 10),
        data: {
          count: e.count,
          width: 1.25 + 7 * Math.pow(t, 0.85),
          opacity: 0.22 + 0.58 * t,
          sourceColor: byStep.get(e.from)!.color,
          targetColor: byStep.get(e.to)!.color,
          points,
          back: !forward,
          minor,
          state: 'normal',
        },
      }
    })

  return { nodes, edges, minorCount }
}

/** Apply selection and highlight state without re-running the layout. */
export function decorate(layout: Layout, selected: Step | null, highlight: Highlight | null): Layout {
  const present = new Set(layout.nodes.map((n) => n.id))
  const pathSteps = new Map<string, number>()
  const pathEdges = new Set<string>()
  let nextStep: string | null = null
  let nextEdge: string | null = null

  if (highlight) {
    const seq = highlight.sequence.filter((s) => present.has(s))
    seq.forEach((s, i) => !pathSteps.has(s) && pathSteps.set(s, i + 1))
    for (let i = 0; i + 1 < seq.length; i++) pathEdges.add(edgeId(seq[i], seq[i + 1]))
    if (highlight.next && present.has(highlight.next) && !pathSteps.has(highlight.next)) {
      nextStep = highlight.next
      if (seq.length) nextEdge = edgeId(seq[seq.length - 1], highlight.next)
    }
  } else if (selected) {
    // Focus mode: the selected node and its direct neighbours.
    pathSteps.set(selected, 0)
    for (const e of layout.edges) {
      if (e.source === selected || e.target === selected) {
        pathEdges.add(e.id)
        pathSteps.set(e.source === selected ? e.target : e.source, 0)
      }
    }
  }

  const active = highlight != null || selected != null
  const hlColor = highlight?.color

  const nodes = layout.nodes.map((n) => {
    let state: NodeState = 'normal'
    if (n.id === nextStep) state = 'next'
    else if (pathSteps.has(n.id)) state = highlight ? 'path' : 'normal'
    else if (active) state = 'dim'
    const seqIndex = highlight ? pathSteps.get(n.id) : undefined
    return { ...n, data: { ...n.data, selected: n.id === selected, state, seqIndex, hlColor } }
  })

  const edges = layout.edges.map((e) => {
    let state: EdgeState = 'normal'
    if (e.id === nextEdge) state = 'next'
    else if (pathEdges.has(e.id)) state = highlight ? 'path' : 'focus'
    else if (active) state = 'dim'
    return { ...e, data: { ...e.data!, state, hlColor } }
  })

  return { nodes, edges, minorCount: layout.minorCount }
}
