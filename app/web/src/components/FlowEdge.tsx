import type { EdgeProps } from '@xyflow/react'
import { label } from '../api'
import type { FlowEdge as FlowEdgeType } from './graphLayout'

type Pt = { x: number; y: number }

/** Smooth horizontal S-curves through each consecutive pair of points. */
function curveThrough(pts: Pt[]): string {
  let d = `M ${pts[0].x},${pts[0].y}`
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1]
    const b = pts[i]
    const mx = (a.x + b.x) / 2
    d += ` C ${mx},${a.y} ${mx},${b.y} ${b.x},${b.y}`
  }
  return d
}

/** A back-edge leaves rightwards and loops above both nodes to re-enter from the left. */
function loopBack(s: Pt, t: Pt): string {
  const lift = 46 + Math.abs(s.x - t.x) * 0.12
  const top = Math.min(s.y, t.y) - lift
  return `M ${s.x},${s.y} C ${s.x + 40},${s.y} ${s.x + 40},${top} ${s.x},${top} L ${t.x},${top} C ${t.x - 40},${top} ${t.x - 40},${t.y} ${t.x},${t.y}`
}

const STATE_OPACITY = { dim: 0.06, focus: 0.95, path: 0.95, next: 0.95 } as const

export function FlowEdge({ id, source, target, sourceX, sourceY, targetX, targetY, data }: EdgeProps<FlowEdgeType>) {
  if (!data) return null
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
