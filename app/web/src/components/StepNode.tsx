import { Handle, Position, type NodeProps } from '@xyflow/react'
import { label, pct } from '../api'
import type { StepFlowNode } from './graphLayout'

/** One step in the scam script. Size/colour by loss rate is a later pass (F4). */
export function StepNode({ data }: NodeProps<StepFlowNode>) {
  const cls = ['step-node', data.selected && 'is-selected', data.isPinch && 'is-pinch', data.isEscape && 'is-escape']
    .filter(Boolean)
    .join(' ')
  return (
    <div className={cls}>
      <Handle type="target" position={Position.Left} />
      <div className="step-node__name">{label(data.step)}</div>
      <div className="step-node__meta">
        {data.count} stories · {pct(data.loss_rate)} lost
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  )
}
