import { Button } from '@/components/ui/button'

interface Props {
  minorCount: number
  showAll: boolean
  onToggleAll: () => void
}

export function Legend({ minorCount, showAll, onToggleAll }: Props) {
  return (
    <div className="legend" aria-label="Legend">
      <span>% = known cases that lost money</span>
      <span>━ your message</span>
      <span>┅ likely next</span>
      <span><b className="c-orange">▲</b> pinch</span>
      <span><b className="c-blue">↗</b> escape</span>
      <span>size = stories</span>
      {(minorCount > 0 || showAll) && (
        <Button
          variant="text"
          size="bare"
          className="ml-auto text-[11px] font-normal"
          onClick={onToggleAll}
          title="Rare transitions still show when you click a step"
        >
          {showAll ? '[ hide rare links ]' : `[ +${minorCount} rare links ]`}
        </Button>
      )}
    </div>
  )
}
