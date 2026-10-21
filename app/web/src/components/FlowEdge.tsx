import type { EdgeProps } from '@xyflow/react'
import { label } from '../api'
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

const STATE_OPACITY = { dim: 0.05, focus: 0.9, path: 0.95, next: 0.95 } as const

export function FlowEdge({ id, source, target, sourceX, sourceY, targetX, targetY, data }: EdgeProps<FlowEdgeType>) {
  if (!data) return null
  if (data.minor && (data.state === 'normal' || data.state === 'dim')) return null
  const s = { x: sourceX, y: sourceY }
  const t = { x: targetX, y: targetY }
  const path = data.back ? loopBack(s, t) : curveThrough([s, ...data.points, t])
  const gradId = `edge-grad-${id.replace(/[^a-z_]/gi, '-')}`
  const emphasised = data.state === 'path' || data.state === 'next'
  const baseOpacity = data.back ? Math.min(0.35, data.opacity) : data.opacity
  const opacity = data.state === 'normal' ? baseOpacity : STATE_OPACITY[data.state]
  const stroke = emphasised ? (data.hlColor ?? 'var(--accent)') : `url(#${gradId})`
  const baseWidth = data.back ? Math.min(2, data.width) : data.width
  const width = emphasised ? Math.max(baseWidth, 3) : baseWidth

  return (
    <g className={`flow-edge flow-edge--${data.state}${data.back ? ' flow-edge--back' : ''}`}>
      <defs>
        <linearGradient id={gradId} gradientUnits="userSpaceOnUse" x1={s.x} y1={s.y} x2={t.x} y2={t.y}>
          <stop offset="0%" stopColor={data.sourceColor} />
          <stop offset="100%" stopColor={data.targetColor} />
        </linearGradient>
      </defs>
      <path
        d={path}
        fill="none"
        stroke={stroke}
        strokeOpacity={opacity}
        strokeWidth={width}
        strokeLinecap="round"
        strokeDasharray={data.state === 'next' ? '6 7' : data.back ? '4 5' : undefined}
        className="flow-edge__path"
      />
      {/* Wide invisible stroke for hover. */}
      <path d={path} fill="none" stroke="transparent" strokeWidth={Math.max(14, width + 8)}>
        <title>
          {label(source)} → {label(target)}: {data.count} {data.count === 1 ? 'story' : 'stories'}
        </title>
      </path>
    </g>
  )
}
