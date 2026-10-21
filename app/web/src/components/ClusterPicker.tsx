import { useRef, useState } from 'react'
import { humanize, knownPct, pct, SCAM_TYPES, type Clusters } from '../api'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'

/** Drop a leading "fake_job · " when every variant shares the type anyway. */
const short = (l: string) => {
  const [head, ...rest] = l.split(' · ')
  return rest.length && (SCAM_TYPES as readonly string[]).includes(head) ? rest.join(' · ') : l
}

/** Radix Select items can't use "" as a value, so "all stories" gets a sentinel. */
const ALL = '__all__'

interface Props {
  clusters: Clusters
  value: string | null
  onChange: (id: string | null) => void
  disabled?: boolean
}

/** Picker for highlighting one script variant (sequence cluster) on the graph. */
export function ClusterPicker({ clusters, value, onChange, disabled }: Props) {
  const a = clusters.agreement
  const agree = a
    ? `Sequence clusters vs. the scam-type labels: purity ${pct(a.purity)}, ARI ${a.adjusted_rand.toFixed(2)} (0 ≈ chance, 1 = identical).`
    : undefined
  const hint = disabled ? 'Clear the matched message to pick a variant' : agree
  // The hint must not sit on top of the open dropdown.
  const [tipOpen, setTipOpen] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  // Closing the menu refocuses the trigger; don't let that pop the hint back up.
  const closedAt = useRef(0)
  function onMenuOpenChange(open: boolean) {
    setMenuOpen(open)
    if (open) setTipOpen(false)
    else closedAt.current = Date.now()
  }
  function onTipOpenChange(open: boolean) {
    if (open && Date.now() - closedAt.current < 400) return
    setTipOpen(open)
  }
  const picker = (
    <div className="variant">
      <label htmlFor="variant" className="muted">variant</label>
      <Select
        open={menuOpen}
        onOpenChange={onMenuOpenChange}
        value={value ?? ALL}
        disabled={disabled}
        onValueChange={(v) => onChange(v === ALL ? null : v)}
      >
        <SelectTrigger
          id="variant"
          className="h-8! w-[320px] max-w-full min-w-0 bg-background px-2 text-xs font-medium shadow-sm data-placeholder:text-foreground [&_svg]:text-foreground!"
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent position="popper" align="start" sideOffset={4} className="max-w-[min(480px,calc(100vw-32px))]">
          <SelectItem value={ALL} className="text-xs">all stories</SelectItem>
          {clusters.clusters.map((c, i) => (
            <SelectItem key={c.id} value={c.id} className="text-xs">
              {c.outlier ? 'outliers' : `${i + 1}: ${humanize(short(c.label)).toLowerCase()}`} ({c.size}) · {knownPct(c.loss_rate, c.n_known)} lost
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
  if (!hint) return picker
  return (
    <Tooltip open={tipOpen && !menuOpen} onOpenChange={onTipOpenChange}>
      <TooltipTrigger asChild>{picker}</TooltipTrigger>
      <TooltipContent side="bottom" align="start" sideOffset={6}>{hint}</TooltipContent>
    </Tooltip>
  )
}
