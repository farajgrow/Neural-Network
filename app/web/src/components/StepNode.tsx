import { Handle, Position, type NodeProps } from '@xyflow/react'
import type { CSSProperties } from 'react'
import { knownPct, label, lower, pct, pts } from '../api'
import { NODE_W, ZONE_H, type StepFlowNode, type StepNodeData } from './graphLayout'
import { Badge } from '@/components/ui/badge'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { TAG } from '@/lib/retro'

function tooltip(d: StepNodeData): string[] {
  const lines = [`${label(d.step)}: ${d.count} ${d.count === 1 ? 'story' : 'stories'}, ${knownPct(d.loss_rate, d.n_known)} lost money`]
  if (d.pinch) {
    lines.push(
      `Pinch point: ${pts(d.pinch.jump)} loss rate (${pct(d.pinch.prev_loss_rate)} before → ${knownPct(d.pinch.loss_rate, d.pinch.n_known)} here)`,
    )
  }
  if (d.escape) lines.push(`Escape point: ${d.escape.exit_count} got out right after this step`)
  return lines
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
    <div className={cls} style={style}>
      <div className="node__badges">
        {d.pinch && <Badge variant="destructive" className={TAG}>▲ {pts(d.pinch.jump)}{next && ' · next?'}</Badge>}
        {next && !d.pinch && <Badge variant="destructive" className={TAG}>next?</Badge>}
        {d.escape && <Badge className={`${TAG} bg-escape text-primary-foreground`}>↗ {d.escape.exit_count} out</Badge>}
      </div>
      <Tooltip>
        <TooltipTrigger asChild>
          <div className="node__box">
            {d.count}
            {d.seqIndex != null && d.seqIndex > 0 && <span className="node__seq">{d.seqIndex}</span>}
          </div>
        </TooltipTrigger>
        <TooltipContent side="top" sideOffset={8} className="flex-col items-start gap-0 font-normal">
          {tooltip(d).map((line) => (
            <span key={line}>{line}</span>
          ))}
        </TooltipContent>
      </Tooltip>
      <div className="node__label">
        <div className="node__name">{d.isOutcome ? OUTCOME_NAME[d.step] : lower(d.step)}</div>
        {!d.isOutcome && <div className="node__meta">{d.loss_rate == null ? 'unknown' : `${pct(d.loss_rate)} lost`}</div>}
      </div>
      {d.vertical ? (
        <>
          <Handle type="target" position={Position.Top} />
          <Handle type="source" position={Position.Bottom} />
        </>
      ) : (
        <>
          <Handle type="target" position={Position.Left} style={{ top: handleTop, left: inset }} />
          <Handle type="source" position={Position.Right} style={{ top: handleTop, right: inset }} />
        </>
      )}
    </div>
  )
}
