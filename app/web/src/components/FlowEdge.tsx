import type { EdgeProps } from '@xyflow/react'
import { knownPct, label } from '../api'
import type { FlowEdge as FlowEdgeType } from './graphLayout'

type Pt = { x: number; y: number }

/**
 * Smooth curve through the points. A plain horizontal S-curve when there are no
 * bends; otherwise Catmull-Rom through the dagre bend points, with horizontal
 * tangents at both ends so edges leave and enter nodes left-to-right.
 */
function curveThrough(pts: Pt[]): string {
  const [a, b] = [pts[0], pts[pts.length - 1]]
  if (pts.length === 2) {
    const mx = (a.x + b.x) / 2
    return `M ${a.x},${a.y} C ${mx},${a.y} ${mx},${b.y} ${b.x},${b.y}`
  }
  const k = 1 / 6
  const clampX = (x: number, lo: number, hi: number) => Math.min(Math.max(x, Math.min(lo, hi)), Math.max(lo, hi))
  let d = `M ${a.x},${a.y}`
  for (let i = 0; i + 1 < pts.length; i++) {
    const p1 = pts[i]
    const p2 = pts[i + 1]
    const dx = Math.abs(p2.x - p1.x)
    // Virtual neighbours at the ends keep the tangents horizontal.
    const p0 = pts[i - 1] ?? { x: p1.x - dx, y: p1.y }
    const p3 = pts[i + 2] ?? { x: p2.x + dx, y: p2.y }
    const c1x = clampX(p1.x + (p2.x - p0.x) * k, p1.x, p2.x)
    const c2x = clampX(p2.x - (p3.x - p1.x) * k, p1.x, p2.x)
    d += ` C ${c1x},${p1.y + (p2.y - p0.y) * k} ${c2x},${p2.y - (p3.y - p1.y) * k} ${p2.x},${p2.y}`
  }
  return d
}

/** A back-edge leaves rightwards and loops above both nodes to re-enter from the left. */
function loopBack(s: Pt, t: Pt): string {
  const lift = 46 + Math.abs(s.x - t.x) * 0.12
  const top = Math.min(s.y, t.y) - lift
  return `M ${s.x},${s.y} C ${s.x + 40},${s.y} ${s.x + 40},${top} ${s.x},${top} L ${t.x},${top} C ${t.x - 40},${top} ${t.x - 40},${t.y} ${t.x},${t.y}`
}

const KIND_STROKE = { plain: 'var(--edge)', lost: 'var(--orange)', out: 'var(--blue)' } as const

export function FlowEdge({ source, target, sourceX, sourceY, targetX, targetY, data }: EdgeProps<FlowEdgeType>) {
  if (!data) return null
  if (data.minor && (data.state === 'normal' || data.state === 'dim')) return null
  const s = { x: sourceX, y: sourceY }
  const t = { x: targetX, y: targetY }
  let path: string
  if (data.sameCol) {
    // Centre to centre, bulging right; the boxes hide the ends.
    const a = { x: sourceX - data.sourceSize / 2, y: sourceY }
    const b = { x: targetX + data.targetSize / 2, y: targetY }
    const bulge = 26 + Math.abs(b.y - a.y) * 0.08
    const dy = (b.y - a.y) / 3
    path = `M ${a.x},${a.y} C ${a.x + bulge},${a.y + dy} ${b.x + bulge},${b.y - dy} ${b.x},${b.y}`
  } else path = data.back ? loopBack(s, t) : curveThrough([s, ...data.points, t])
  const baseWidth = data.back ? Math.min(1.5, data.width) : data.width

  let stroke: string = KIND_STROKE[data.kind]
  let width = baseWidth
  let opacity = data.back ? 0.6 : 1
  let dash = data.back ? '3 4' : undefined
  if (data.state === 'path') {
    stroke = 'var(--ink)'
    width = 3
  } else if (data.state === 'next') {
    stroke = 'var(--ink)'
    width = 2.5
    dash = '4 5'
  } else if (data.state === 'focus') {
    if (data.kind === 'plain') stroke = 'var(--ink)'
    width = Math.max(baseWidth, 2)
  } else if (data.state === 'dim') {
    opacity = 0.22
  }

  return (
    <g className={`flow-edge flow-edge--${data.state}`}>
      <path
        d={path}
        fill="none"
        style={{ stroke, strokeOpacity: opacity, strokeWidth: width }}
        strokeLinecap="butt"
        strokeDasharray={dash}
      />
      {/* Wide invisible stroke for hover. */}
      <path d={path} fill="none" stroke="transparent" strokeWidth={Math.max(14, width + 8)}>
        <title>
          {label(source)} → {label(target)}: {data.count} {data.count === 1 ? 'story' : 'stories'}
          {data.nKnown != null && `, ${knownPct(data.lossRate, data.nKnown)} lost money`}
        </title>
      </path>
    </g>
  )
}
