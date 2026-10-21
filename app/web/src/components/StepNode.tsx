import { Handle, Position, type NodeProps } from '@xyflow/react'
import type { CSSProperties } from 'react'
import { label, pct, pts } from '../api'
import { NODE_W, ZONE_H, type StepFlowNode, type StepNodeData } from './graphLayout'

function tooltip(d: StepNodeData): string {
  const lines = [`${label(d.step)}: ${d.count} ${d.count === 1 ? 'story' : 'stories'}, ${pct(d.loss_rate)} lost money`]
  if (d.pinch) {
    lines.push(
      `Pinch point: ${pts(d.pinch.jump)} loss rate (${pct(d.pinch.prev_loss_rate)} before → ${pct(d.pinch.loss_rate)} here)`,
    )
  }
  if (d.escape) lines.push(`Escape point: ${d.escape.exit_count} got out right after this step`)
  return lines.join('\n')
}

/** One step in the scam script: a circle sized by count and coloured by loss rate. */
export function StepNode({ data: d }: NodeProps<StepFlowNode>) {
  const cls = [
    'step-node',
    d.isOutcome && 'is-outcome',
    d.selected && 'is-selected',
    `state-${d.state}`,
  ]
    .filter(Boolean)
    .join(' ')
  const style = {
    '--c': d.color,
    '--hl': d.hlColor ?? 'var(--accent)',
    '--d': `${d.diameter}px`,
    width: NODE_W,
  } as CSSProperties
  const handleTop = ZONE_H + d.diameter / 2
  const inset = (NODE_W - d.diameter) / 2

  return (
    <div className={cls} style={style} title={tooltip(d)}>
      <div className="step-node__badges">
        {d.state === 'next' && <span className="node-badge node-badge--next">next?</span>}
        {d.pinch && <span className="node-badge node-badge--pinch">▲ {pts(d.pinch.jump)}</span>}
        {d.escape && <span className="node-badge node-badge--escape">↗ {d.escape.exit_count} out</span>}
      </div>
      <div className="step-node__dot">
        <span className="step-node__count">{d.count}</span>
        {d.seqIndex != null && d.seqIndex > 0 && <span className="step-node__seq">{d.seqIndex}</span>}
      </div>
      <div className="step-node__label">
        <div className="step-node__name">{label(d.step)}</div>
        {!d.isOutcome && <div className="step-node__meta">{d.loss_rate == null ? 'outcome unknown' : `${pct(d.loss_rate)} lost`}</div>}
        {d.isOutcome && <div className="step-node__meta">outcome</div>}
      </div>
      <Handle type="target" position={Position.Left} style={{ top: handleTop, left: inset }} />
      <Handle type="source" position={Position.Right} style={{ top: handleTop, right: inset }} />
    </div>
  )
}
