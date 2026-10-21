import { LOSS_GRADIENT, NEUTRAL } from '../colors'

interface Props {
  minorCount: number
  showAll: boolean
  onToggleAll: () => void
}

export function Legend({ minorCount, showAll, onToggleAll }: Props) {
  return (
    <div className="legend" aria-label="Legend">
      <div className="legend__group">
        <span className="legend__title">Loss rate</span>
        <span className="legend__scale">
          <span className="legend__bar" style={{ background: LOSS_GRADIENT }} />
          <span className="legend__ticks">
            <span>0%</span>
            <span>50%</span>
            <span>100%</span>
          </span>
        </span>
        <span className="legend__item">
          <span className="legend__swatch" style={{ background: NEUTRAL }} /> no known outcome
        </span>
      </div>
      <div className="legend__group">
        <span className="legend__item">
          <span className="node-badge node-badge--pinch">▲</span> pinch point
        </span>
        <span className="legend__item">
          <span className="node-badge node-badge--escape">↗</span> escape point
        </span>
        <span className="legend__item legend__muted">size &amp; line width = stories</span>
        {(minorCount > 0 || showAll) && (
          <button className="legend__toggle" onClick={onToggleAll} title="Rare transitions still show when you click a step">
            {showAll ? 'Hide rare transitions' : `Show ${minorCount} rare transitions`}
          </button>
        )}
      </div>
    </div>
  )
}
