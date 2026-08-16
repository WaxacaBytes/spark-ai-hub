import { useState } from 'react'
import { useThemedLogo } from '../hooks/useThemedLogo'
import { BuildRow, ColumnHeader } from './ModelRow'
import { buildLabel } from '../models'

// One model, with every build it has, all on screen.
//
// Nothing here collapses. An earlier version showed only the best build and
// put the rest behind a "+5 more builds" link; that hid two thirds of the
// catalog behind a control people never found, and expanding it reflowed the
// page so you lost your place. Builds of one model differ by a handful of
// numbers, so they belong in aligned rows where the numbers line up and the
// comparison is just reading down a column.
export default function ModelGroup({ group, highlight = null }) {
  const [logoFailed, setLogoFailed] = useState(false)
  const logoUrl = useThemedLogo(group.lead.logo)
  const multi = group.items.length > 1

  return (
    <section className="rounded-2xl bg-surface p-3 ring-1 ring-glass-border">
      <header className="mb-1.5 flex items-center gap-2 px-2">
        {logoUrl && !logoFailed && (
          <img
            src={logoUrl}
            alt=""
            loading="lazy"
            onError={() => setLogoFailed(true)}
            className="h-6 w-6 shrink-0 rounded-md bg-surface-high object-contain p-0.5"
          />
        )}
        <h3
          className="m-0 min-w-0 flex-1 truncate font-display text-[13px] font-bold tracking-tight text-text"
          title={group.label}
        >
          {group.label}
        </h3>
        <span className="shrink-0 font-label text-[10px] text-text-dim">
          {group.items.length} build{multi ? 's' : ''}
        </span>
      </header>

      {/* Every block gets the column key, single-build ones included: without
          it those rows were a line of unlabelled numbers, and the reader had
          to find a neighbouring block to work out what they meant. */}
      <ColumnHeader />

      <div className="mt-0.5">
        {group.items.map((r) => (
          <BuildRow
            key={r.slug}
            recipe={r}
            label={buildLabel(r, group.label)}
            highlight={highlight}
          />
        ))}
      </div>
    </section>
  )
}
