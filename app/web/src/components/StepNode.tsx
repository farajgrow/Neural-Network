import { Handle, Position, type NodeProps } from '@xyflow/react'
import type { CSSProperties } from 'react'
import { label, lower, pct, pts } from '../api'
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

const OUTCOME_NAME: Record<string, string> = { money_lost: 'MONEY LOST', victim_exits: 'GOT OUT' }

/** One step in the scam script: a square sized by story count. */
export function StepNode({ data: d }: NodeProps<StepFlowNode>) {
  const kind = d.step === 'money_lost' ? 'lost' : d.step === 'victim_exits' ? 'out' : 'step'
  const cls = [
    'node',
    `node--${kind}`,
    `state-${d.state}`,
    d.loss_rate == null && !d.isOutcome && 'is-unknown',
    d.selected && 'is-selected',
  ]
    .filter(Boolean)
    .join(' ')
  const style = { '--s': `${d.size}px`, width: NODE_W } as CSSProperties
  const handleTop = ZONE_H + d.size / 2
  const inset = (NODE_W - d.size) / 2
  const next = d.state === 'next'

  return (
    <div className={cls} style={style} title={tooltip(d)}>
      <div className="node__badges">
        {d.pinch && <span className="tag tag--orange">▲ {pts(d.pinch.jump)}{next && ' · next?'}</span>}
        {next && !d.pinch && <span className="tag tag--orange">next?</span>}
        {d.escape && <span className="tag tag--blue">↗ {d.escape.exit_count} out</span>}
      </div>
      <div className="node__box">
        {d.count}
        {d.seqIndex != null && d.seqIndex > 0 && <span className="node__seq">{d.seqIndex}</span>}
      </div>
      <div className="node__label">
        <div className="node__name">{d.isOutcome ? OUTCOME_NAME[d.step] : lower(d.step)}</div>
        {!d.isOutcome && <div className="node__meta">{d.loss_rate == null ? 'unknown' : `${pct(d.loss_rate)} lost`}</div>}
      </div>
      <Handle type="target" position={Position.Left} style={{ top: handleTop, left: inset }} />
      <Handle type="source" position={Position.Right} style={{ top: handleTop, right: inset }} />
    </div>
  )
}
