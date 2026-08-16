import { useState } from 'react'
import { useThemedLogo } from '../hooks/useThemedLogo'
import { BuildRow, ColumnHeader, WIDE_COLUMNS, WIDE_HEADERS } from './ModelRow'

// Every build in a band, ranked flat against each other.
//
// Grouping by model is right when you are choosing a model and wrong when you
// are ranking builds: a band called "100+ tok/s" that holds a model whose
// slowest build runs at 39 is lying about four of its six rows. So the ranked
// sorts drop the grouping and put one build per row, in rank order, under a
// heading that is true of every row beneath it.
//
// A band splits across two tables side by side. One full-width table left ~900px
// of empty floor between a build's name and its numbers, which is a long way for
// an eye to travel along a single row; halved, the numbers sit beside the name.
// The rank numbers carry the reading order, and on a narrow window the two
// halves stack — still in order, because the first half comes first.

// Splitting only pays once a band is tall enough that two columns are shorter
// than the header stack above them.
const SPLIT_THRESHOLD = 8

// A table never grows past this, split or not. Left to fill the window, the
// name column absorbed every spare pixel and pushed the numbers ~900px away
// from the name they describe.
const MAX_TABLE = '730px'

// The quantization has its own column, so printing it in the name as well
// costs room the name needs. Matched on a whole token, with `-` counting as
// part of the word — otherwise "INT4" would eat the tail of "GPTQ-Int4".
function displayName(recipe) {
  const quant = recipe.quantization
  if (!quant) return recipe.name
  const token = quant.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return (recipe.name || '')
    .replace(new RegExp(`(?<![\\w-])\\(?${token}\\)?(?![\\w-])`, 'i'), '')
    .replace(/\(\s*\)/g, '')
    .replace(/\s{2,}/g, ' ')
    .trim() || recipe.name
}

export default function ModelTable({ items, highlight = null }) {
  const split = items.length >= SPLIT_THRESHOLD ? Math.ceil(items.length / 2) : items.length
  const halves = [items.slice(0, split), items.slice(split)].filter((h) => h.length > 0)

  return (
    <div
      className="grid w-full items-start gap-3"
      style={{ gridTemplateColumns: `repeat(auto-fit, minmax(560px, ${MAX_TABLE}))`, justifyContent: 'start' }}
    >
      {halves.map((half, i) => (
        <section key={i} className="rounded-2xl bg-surface p-3 ring-1 ring-glass-border">
          <ColumnHeader
            first="Model · build"
            extra={WIDE_HEADERS}
            columns={WIDE_COLUMNS}
            ranked
          />
          <div className="mt-0.5">
            {half.map((r, j) => (
              <RankedRow
                key={r.slug}
                recipe={r}
                rank={(i === 0 ? 0 : split) + j + 1}
                highlight={highlight}
              />
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}

// Ranked rows carry the model's own logo and full name — there is no group
// heading above them to say which model they belong to.
function RankedRow({ recipe, rank, highlight }) {
  const [logoFailed, setLogoFailed] = useState(false)
  const logoUrl = useThemedLogo(recipe.logo)

  const label = (
    <span className="flex min-w-0 items-center gap-1.5">
      {logoUrl && !logoFailed && (
        <img
          src={logoUrl}
          alt=""
          loading="lazy"
          onError={() => setLogoFailed(true)}
          className="h-4 w-4 shrink-0 rounded object-contain"
        />
      )}
      <span className="min-w-0">{displayName(recipe)}</span>
    </span>
  )

  return (
    <BuildRow
      recipe={recipe}
      label={label}
      title={recipe.name}
      highlight={highlight}
      rank={rank}
      wide
      wrapLabel
    />
  )
}
