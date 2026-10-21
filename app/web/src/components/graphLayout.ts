import type { Edge, Node } from '@xyflow/react'
import { OUTCOME_STEPS, type EscapePoint, type Graph, type GraphNode, type PinchPoint, type Step } from '../api'

// Node geometry. Each node is a fixed-width box: badge zone, square, label zone.
// The badge and label zones have the same height so the square sits on the box's
// vertical centre, which is where dagre routes edges to.
export const NODE_W = 116
export const ZONE_H = 40
const S_MIN = 24
const S_MAX = 58
const RANK_SEP = 36
const NODE_SEP = 12
/** Share of stories an edge needs to be drawn by default. */
const MINOR_SHARE = 0.05

export type NodeState = 'normal' | 'dim' | 'path' | 'next'
export type EdgeState = 'normal' | 'dim' | 'path' | 'next' | 'focus'
/** What an edge leads into: an outcome gets the outcome colour. */
export type EdgeKind = 'plain' | 'lost' | 'out'

export type StepNodeData = GraphNode & {
  size: number
  isOutcome: boolean
  pinch?: PinchPoint
  escape?: EscapePoint
  // Interaction state, filled in by decorate().
  selected: boolean
  state: NodeState
  seqIndex?: number
}
export type StepFlowNode = Node<StepNodeData, 'step'>

export type FlowEdgeData = {
  count: number
  lossRate?: number | null
  nKnown?: number
  width: number
  kind: EdgeKind
  /** Interior bend points from dagre (absolute coordinates), empty for short edges. */
  points: { x: number; y: number }[]
  /** Goes against the avg_position order (drawn as a dashed loop). */
  back: boolean
  /** Both ends sit in the same column: drawn as a bulge between the box centres. */
  sameCol: boolean
  sourceSize: number
  targetSize: number
  /** Rare transition: hidden unless it is part of the focus or a highlight. */
  minor: boolean
  state: EdgeState
}
export type FlowEdge = Edge<FlowEdgeData, 'flow'>

export interface Layout {
  nodes: StepFlowNode[]
  edges: FlowEdge[]
  /** Number of rare transitions hidden by default. */
  minorCount: number
}

/** What to emphasise on the graph: a variant's typical path or a matched message. */
export interface Highlight {
  kind: 'cluster' | 'match'
  sequence: Step[]
  next?: Step | null
  /** Extra transitions to emphasise (a variant's strong edges). */
  edges?: { from: Step; to: Step }[]
}

const edgeId = (from: string, to: string) => `${from}->${to}`

/**
 * Column layout, left to right.
 *
 * Steps are ordered by avg_position (outcomes last) and dealt into a handful of
 * columns, about three steps each, so the script reads left to right and the
 * graph fills a wide, short canvas. Within a column, steps are ordered by the
 * weighted mean height of the steps that feed them (a barycentre pass), which
 * keeps most edges short and uncrossed. Outcomes get a final column of their own.
 *
 * Most transitions are rare and would turn the graph into a hairball, so edges
 * below ~5% of stories are "minor": only drawn when they touch the selected step
 * or a highlighted path. Every step keeps its strongest incoming and outgoing
 * edge. Back-edges (against the avg_position order) are always minor.
 */
export function layoutGraph(graph: Graph, showAll = false): Layout {
  const pinch = new Map(graph.pinch_points.map((p) => [p.step, p]))
  const escape = new Map(graph.escape_points.map((p) => [p.step, p]))
  const maxCount = Math.max(1, ...graph.nodes.map((n) => n.count))
  const size = (count: number) => Math.round(S_MIN + (S_MAX - S_MIN) * Math.sqrt(count / maxCount))
  const height = (count: number) => size(count) + 2 * ZONE_H

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
  const minCount = Math.max(1, Math.ceil(graph.n_stories * MINOR_SHARE))
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
    if (!isForward(e.from, e.to)) return true
    return e.count < minCount && !strongest.has(edgeId(e.from, e.to))
  }

  const steps = order.filter((n) => !OUTCOME_STEPS.has(n.step))
  const outcomes = order
    .filter((n) => OUTCOME_STEPS.has(n.step))
    .sort((a, b) => Number(b.step === 'money_lost') - Number(a.step === 'money_lost'))

  // Deal steps into columns: about three per column, 3 to 6 columns.
  const nCols = Math.max(1, Math.min(6, Math.max(3, Math.round(steps.length / 2.6)), steps.length))
  const columns: GraphNode[][] = Array.from({ length: nCols }, () => [])
  steps.forEach((n, i) => columns[Math.floor((i * nCols) / steps.length)].push(n))
  if (outcomes.length) columns.push(outcomes)

  const colOf = new Map<string, number>()
  columns.forEach((col, i) => col.forEach((n) => colOf.set(n.step, i)))
  const countOf = new Map(graph.nodes.map((n) => [n.step as string, n.count]))
  const centre = new Map<string, { x: number; y: number }>()
  const place = (col: GraphNode[], x: number) => {
    const total = col.reduce((s, n) => s + height(n.count), 0) + NODE_SEP * (col.length - 1)
    let y = -total / 2
    for (const n of col) {
      const h = height(n.count)
      centre.set(n.step, { x, y: y + h / 2 })
      y += h + NODE_SEP
    }
  }
  const colX = (i: number) => i * (NODE_W + RANK_SEP) + (i === nCols && outcomes.length ? RANK_SEP : 0)
  const kept = edgesIn.filter((e) => isForward(e.from, e.to) && !isMinor(e))
  columns.forEach((col, i) => {
    if (i > 0 && col !== outcomes) {
      // Barycentre of incoming kept edges; nodes without any keep their slot.
      const bary = new Map<string, number>()
      col.forEach((n, j) => {
        let w = 0
        let sum = 0
        for (const e of kept) {
          const c = e.to === n.step ? centre.get(e.from) : undefined
          if (c) {
            w += e.count
            sum += e.count * c.y
          }
        }
        bary.set(n.step, w ? sum / w : j * 1e-3)
      })
      col.sort((a, b) => bary.get(a.step)! - bary.get(b.step)!)
    }
    place(col, colX(i))
  })

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
        size: size(n.count),
        isOutcome: OUTCOME_STEPS.has(n.step),
        pinch: pinch.get(n.step),
        escape: escape.get(n.step),
        selected: false,
        state: 'normal',
      },
    }
  })

  const maxEdge = Math.max(1, ...graph.edges.map((e) => e.count))
  let minorCount = 0
  const edges: FlowEdge[] = edgesIn
    .map((e) => {
      const forward = isForward(e.from, e.to)
      const minor = isMinor(e)
      if (minor) minorCount++
      const t = e.count / maxEdge
      return {
        id: edgeId(e.from, e.to),
        type: 'flow',
        source: e.from,
        target: e.to,
        data: {
          count: e.count,
          lossRate: e.loss_rate,
          nKnown: e.n_known,
          width: 1 + 2.5 * Math.pow(t, 0.8),
          kind: (e.to === 'money_lost' ? 'lost' : e.to === 'victim_exits' ? 'out' : 'plain') as EdgeKind,
          points: [],
          back: !forward && colOf.get(e.from) !== colOf.get(e.to),
          sameCol: colOf.get(e.from) === colOf.get(e.to),
          sourceSize: size(countOf.get(e.from) ?? 1),
          targetSize: size(countOf.get(e.to) ?? 1),
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
    for (const e of highlight.edges ?? []) {
      if (!present.has(e.from) || !present.has(e.to)) continue
      pathEdges.add(edgeId(e.from, e.to))
      for (const s of [e.from, e.to]) if (!pathSteps.has(s)) pathSteps.set(s, 0)
    }
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

  const nodes = layout.nodes.map((n) => {
    let state: NodeState = 'normal'
    if (n.id === nextStep) state = 'next'
    else if (pathSteps.has(n.id)) state = highlight ? 'path' : 'normal'
    else if (active) state = 'dim'
    const seqIndex = highlight ? pathSteps.get(n.id) : undefined
    return { ...n, data: { ...n.data, selected: n.id === selected, state, seqIndex } }
  })

  const edges = layout.edges.map((e) => {
    let state: EdgeState = 'normal'
    if (e.id === nextEdge) state = 'next'
    else if (pathEdges.has(e.id)) state = highlight ? 'path' : 'focus'
    else if (active) state = 'dim'
    return { ...e, data: { ...e.data!, state } }
  })
  // Emphasised edges last, so they draw on top of the grey ones.
  const rank = { dim: 0, normal: 1, focus: 2, path: 3, next: 3 } as const
  edges.sort((a, b) => rank[a.data!.state] - rank[b.data!.state])

  return { nodes, edges, minorCount: layout.minorCount }
}
