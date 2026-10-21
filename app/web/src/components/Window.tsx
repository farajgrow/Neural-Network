import type { ReactNode } from 'react'

/** Retro Mac-style window: pinstriped title bar, close box, centred label. */
export function Window({ title, label, className, children }: { title: string; label?: string; className?: string; children: ReactNode }) {
  return (
    <section className={className ? `window ${className}` : 'window'} aria-label={label ?? title}>
      <div className="window__bar">
        <span className="window__close" aria-hidden />
        <span className="window__title">{title}</span>
      </div>
      {children}
    </section>
  )
}
