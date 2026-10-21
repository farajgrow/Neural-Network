import { label, pct, type GraphKey, type ScamTypeSummary } from '../api'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'

interface Props {
  types: ScamTypeSummary[]
  /** Loss rate of the combined "all" graph (not derivable from the per-type rates). */
  allLossRate: number | null
  value: GraphKey
  onChange: (key: GraphKey) => void
}

export function ScamTypeSwitcher({ types, allLossRate, value, onChange }: Props) {
  if (types.length === 0) return null
  const total = types.reduce((n, t) => n + t.n_stories, 0)
  const options: { key: GraphKey; n: number; loss: number | null }[] = [
    ...types.map((t) => ({ key: t.scam_type, n: t.n_stories, loss: t.loss_rate })),
    { key: 'all', n: total, loss: allLossRate },
  ]
  return (
    // Manual activation: arrow keys move focus, Enter/Space switches (each switch refetches the graph).
    <Tabs value={value} onValueChange={(v) => onChange(v as GraphKey)} activationMode="manual" className="min-w-0 flex-row gap-0">
      <TabsList aria-label="Scam type" className="tabs h-auto! w-auto justify-start rounded-none border-0 bg-transparent p-0 shadow-none">
        {options.map((o) => (
          <TabsTrigger
            key={o.key}
            value={o.key}
            title={`${o.n} stories · ${pct(o.loss)} lost money`}
            className="h-11 flex-none rounded-none border-0! px-[11px] py-0 text-xs font-normal text-foreground shadow-none! transition-none hover:bg-secondary data-[state=active]:bg-primary data-[state=active]:text-primary-foreground"
          >
            {o.key === 'all' ? 'All types' : label(o.key)} · {pct(o.loss)}
          </TabsTrigger>
        ))}
      </TabsList>
    </Tabs>
  )
}
