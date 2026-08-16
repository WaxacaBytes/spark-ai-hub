import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useStore } from '../store'
import { useThemedLogo } from '../hooks/useThemedLogo'
import { formatParams } from './RecipeCard'
import { buildLabel, displayName } from '../models'

// Model builds, as rows of aligned numbers.
//
// Cover art is declared per model+size, so fifteen recipes share qwen-27b.jpg.
// On a poster that meant the picture said only which lab made it — which the
// logo and the shelf heading already said — while the facts that separate one
// build from another were squeezed into chips over a scrim. Rows put those
// facts in columns, where comparing them is just reading downwards.
//
// Two shelves need this list and they differ in one column and one heading,
// so they are two variants of one component rather than two components:
//
//   build      grouped under a model heading, which names the model, so a row
//              only has to name its build
//   ranked     one flat leaderboard per band; leads with a rank number
//
// "Jump back in" is deliberately not one of them -- see InstalledStrip.
//
const VARIANTS = {
  build: {
    columns: 'minmax(0,1fr) 56px 48px 72px 50px 34px 48px',
    headers: ['Engine'],
    first: 'Build',
    lead: null,
    wrap: false,
  },
  ranked: {
    columns: '26px minmax(0,1fr) 70px 70px 56px 80px 62px 42px 62px',
    headers: ['Engine', 'Quant'],
    first: 'Model · build',
    lead: 'rank',
    wrap: true,
    maxWidth: '730px',
  },
}

// Left to fill the window, the name column absorbs every spare pixel and
// pushes the numbers ~900px from the name they describe; past that width a
// row stops reading as one thing. Each variant caps itself (see maxWidth).

// Splitting a band in two only pays once it is taller than the heading stack
// above it.
const SPLIT_THRESHOLD = 8

function formatContext(tokens) {
  if (!tokens) return null
  if (tokens >= 1024 * 1024) return `${Math.round(tokens / (1024 * 1024))}M`
  return `${Math.round(tokens / 1024)}K`
}

function runState(recipe, busy) {
  if (busy) return { label: 'Building', dot: 'bg-secondary animate-pulse' }
  if (recipe.running && recipe.ready) return { label: 'Running', dot: 'bg-primary' }
  if (recipe.running || recipe.starting) return { label: 'Starting', dot: 'bg-warning animate-pulse' }
  if (recipe.installed) return { label: 'Installed', dot: 'bg-text-dim' }
  return null
}

function Cell({ children, className = '' }) {
  return (
    <span className={`truncate font-label text-[11px] tabular-nums ${className}`}>
      {children ?? <span className="text-text-dim">—</span>}
    </span>
  )
}

function ColumnHeader({ variant }) {
  const v = VARIANTS[variant]
  return (
    <div
      className="grid gap-x-1.5 border-b border-outline-dim px-2 pb-1"
      style={{ gridTemplateColumns: v.columns }}
    >
      {v.lead && <span />}
      {[v.first, ...v.headers].map((h) => (
        <span key={h} className="font-label text-[9px] font-semibold uppercase tracking-wider text-text-dim">
          {h}
        </span>
      ))}
      {['tok/s', 'Params', 'On disk', 'Ctx'].map((h) => (
        <span
          key={h}
          className="text-right font-label text-[9px] font-semibold uppercase tracking-wider text-text-dim"
        >
          {h}
        </span>
      ))}
      <span />
    </div>
  )
}

function BuildRow({ recipe, variant, groupLabel, rank, highlight }) {
  const v = VARIANTS[variant]
  const installing = useStore((s) => s.installing)
  const updating = useStore((s) => s.updating)
  const installRecipe = useStore((s) => s.installRecipe)
  const requestLaunch = useStore((s) => s.requestLaunch)
  const [logoFailed, setLogoFailed] = useState(false)
  const logoUrl = useThemedLogo(recipe.logo)

  const isBusy = !!installing[recipe.slug] || !!updating[recipe.slug]
  const openUrl = `http://${location.hostname}:${recipe.ui?.port ?? 8080}${recipe.ui?.path ?? '/'}`
  const running = recipe.running && recipe.ready
  const state = runState(recipe, isBusy)

  // Grouped rows sit under a heading that names the model, so they name only
  // the build. The other two stand alone and carry the model name and logo.
  const label = variant === 'build' ? buildLabel(recipe, groupLabel) : displayName(recipe)

  const action = isBusy ? (
    <span className="font-label text-[10px] text-secondary">Building…</span>
  ) : running ? (
    <a
      href={openUrl}
      target="_blank"
      rel="noreferrer"
      onClick={(e) => e.stopPropagation()}
      className="btn-primary block w-full py-1 text-center text-[10px] font-bold no-underline"
    >
      Open ↗
    </a>
  ) : recipe.starting ? (
    <span className="font-label text-[10px] text-warning">Starting…</span>
  ) : (
    <button
      onClick={(e) => {
        e.preventDefault()
        e.stopPropagation()
        recipe.installed ? requestLaunch(recipe.slug) : installRecipe(recipe.slug)
      }}
      // Install is the quiet option: on a page showing 73 of them, a filled
      // button per row is noise. Launch stays loud — that one does something
      // to the Spark right now.
      className={`w-full rounded-lg py-1 text-[10px] font-bold transition-colors ${
        recipe.installed
          ? 'btn-primary'
          : 'cursor-pointer border border-outline-dim bg-transparent text-text-muted hover:border-primary hover:text-primary'
      }`}
    >
      {recipe.installed ? 'Launch' : 'Install'}
    </button>
  )

  return (
    <Link
      to={`/app/${recipe.slug}`}
      className="grid items-center gap-x-1.5 rounded-lg px-2 py-1.5 no-underline text-inherit transition-colors hover:bg-surface-high"
      style={{ gridTemplateColumns: v.columns }}
    >
      {v.lead === 'rank' && (
        <span className="text-right font-label text-[10px] tabular-nums text-text-dim">{rank}</span>
      )}
      <span className="flex min-w-0 items-center gap-1.5">
        {state && (
          <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${state.dot}`} title={state.label} />
        )}
        {v.lead !== null && logoUrl && !logoFailed && (
          <img
            src={logoUrl}
            alt=""
            loading="lazy"
            onError={() => setLogoFailed(true)}
            className="h-4 w-4 shrink-0 rounded object-contain"
          />
        )}
        <span
          className={`min-w-0 flex-1 font-label text-[11px] font-bold text-text ${
            // Standalone rows carry a full model name and get a second line.
            // Cutting the tail off "Nemotron-3 Nano Omni 30B-A3B Reasoning" is
            // what makes two builds look like the same one.
            v.wrap ? 'line-clamp-2 leading-tight' : 'truncate'
          }`}
          title={recipe.name}
        >
          {label}
        </span>
      </span>

      <Cell className={`text-text-muted ${v.wrap ? '' : 'text-[10px]'}`}>{recipe.engine}</Cell>
      {v.headers.includes('Quant') && <Cell className="text-text-muted">{recipe.quantization}</Cell>}

      {/* Speed leads: it is the number that decides whether a build is usable. */}
      <span className={`truncate text-right font-display text-[13px] font-extrabold tabular-nums ${
        recipe.tokens_per_second != null ? 'text-primary' : 'text-text-dim'
      }`}>
        {recipe.tokens_per_second ?? '—'}
      </span>
      <Cell className={`text-right ${highlight === 'params' ? 'text-secondary font-bold' : 'text-text-muted'}`}>
        {recipe.params_b != null ? formatParams(recipe) : null}
      </Cell>
      <Cell className={`text-right ${highlight === 'size' ? 'text-secondary font-bold' : 'text-text-muted'}`}>
        {recipe.weights_gb != null ? `${recipe.weights_gb} GB` : null}
      </Cell>
      <Cell className="text-right text-text-muted">{formatContext(recipe.context_tokens)}</Cell>
      <span className="min-w-0">{action}</span>
    </Link>
  )
}

// One or two tables of builds. `ranked` splits a tall band across two tables
// side by side; the rank numbers carry the reading order, and on a narrow
// window the halves stack — still in order, first half first.
export default function ModelList({ items, variant = 'ranked', highlight = null }) {
  const split = variant === 'ranked' && items.length >= SPLIT_THRESHOLD
    ? Math.ceil(items.length / 2)
    : items.length
  const halves = [items.slice(0, split), items.slice(split)].filter((h) => h.length > 0)

  return (
    <div
      className="grid w-full items-start gap-3"
      style={{
        gridTemplateColumns: `repeat(auto-fit, minmax(560px, ${VARIANTS[variant].maxWidth}))`,
        justifyContent: 'start',
      }}
    >
      {halves.map((half, i) => (
        <section key={i} className="rounded-2xl bg-surface p-3 ring-1 ring-glass-border">
          <ColumnHeader variant={variant} />
          <div className="mt-0.5">
            {half.map((r, j) => (
              <BuildRow
                key={r.slug}
                recipe={r}
                variant={variant}
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

// "Jump back in": short access to everything on this Spark.
//
// This shelf is recall, not choosing — you already installed these and want
// back into one. So it is sized for a glance, not for comparison: a wrapping
// grid of one-glance chips, models and apps together, the whole thing about
// two rows tall. Full table rows were legible but turned a strip you skim
// into two sections you scroll past to reach the catalog, and posters before
// them were illegible: three of twelve tiles were the same picture.
//
// Each chip carries only what tells two installed builds apart — the engine,
// the quantization, the speed — plus the one control you came for.
export function InstalledStrip({ items }) {
  return (
    <div className="flex flex-wrap gap-2 px-6">
      {items.map((r) => <InstalledChip key={r.slug} recipe={r} />)}
    </div>
  )
}

function InstalledChip({ recipe }) {
  const installing = useStore((s) => s.installing)
  const updating = useStore((s) => s.updating)
  const requestLaunch = useStore((s) => s.requestLaunch)
  const [logoFailed, setLogoFailed] = useState(false)
  const logoUrl = useThemedLogo(recipe.logo)

  const isBusy = !!installing[recipe.slug] || !!updating[recipe.slug]
  const state = runState(recipe, isBusy)
  const running = recipe.running && recipe.ready
  const openUrl = `http://${location.hostname}:${recipe.ui?.port ?? 8080}${recipe.ui?.path ?? '/'}`

  // Models are told apart by how they were built; apps by their name alone.
  const spec = [recipe.engine, recipe.quantization].filter(Boolean).join(' · ')

  return (
    <Link
      to={`/app/${recipe.slug}`}
      title={recipe.name}
      className={`flex w-[272px] items-center gap-2 rounded-xl bg-surface p-2 no-underline text-inherit ring-1 transition-all hover:ring-text-dim ${
        running ? 'ring-primary/60' : 'ring-glass-border'
      }`}
    >
      {logoUrl && !logoFailed ? (
        <img
          src={logoUrl}
          alt=""
          loading="lazy"
          onError={() => setLogoFailed(true)}
          className="h-7 w-7 shrink-0 rounded-lg bg-surface-high object-contain p-1"
        />
      ) : (
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-surface-high text-sm">
          {recipe.icon || '◻'}
        </span>
      )}

      <span className="flex min-w-0 flex-1 flex-col">
        <span className="flex items-center gap-1">
          {state && state.label !== 'Installed' && (
            <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${state.dot}`} title={state.label} />
          )}
          <span className="truncate font-label text-[11px] font-bold text-text">
            {displayName(recipe)}
          </span>
        </span>
        <span className="truncate font-label text-[9px] text-text-dim">
          {spec || recipe.author}
          {recipe.tokens_per_second != null && (
            <span className="ml-1 font-bold text-primary">{recipe.tokens_per_second} tok/s</span>
          )}
        </span>
      </span>

      {running ? (
        <a
          href={openUrl}
          target="_blank"
          rel="noreferrer"
          onClick={(e) => e.stopPropagation()}
          className="btn-primary shrink-0 px-2 py-1 text-[10px] font-bold no-underline"
        >
          Open ↗
        </a>
      ) : isBusy || recipe.starting ? (
        <span className="shrink-0 font-label text-[9px] text-text-muted">{state?.label}…</span>
      ) : (
        <button
          onClick={(e) => { e.preventDefault(); e.stopPropagation(); requestLaunch(recipe.slug) }}
          className="shrink-0 cursor-pointer rounded-lg border border-outline-dim bg-transparent px-2 py-1 font-label text-[10px] font-bold text-text-muted transition-colors hover:border-primary hover:text-primary"
        >
          Launch
        </button>
      )}
    </Link>
  )
}

// One model with every build it has, all on screen.
//
// Nothing here collapses. An earlier version showed only the best build and
// put the rest behind a "+5 more builds" link; that hid two thirds of the
// catalog behind a control readers never found, and expanding it reflowed the
// page so you lost your place.
export function ModelBlock({ group }) {
  const [logoFailed, setLogoFailed] = useState(false)
  const logoUrl = useThemedLogo(group.lead.logo)

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
          {group.items.length} build{group.items.length > 1 ? 's' : ''}
        </span>
      </header>

      {/* Every block gets the column key, single-build ones included: without
          it those rows were a line of unlabelled numbers. */}
      <ColumnHeader variant="build" />

      <div className="mt-0.5">
        {group.items.map((r) => (
          <BuildRow key={r.slug} recipe={r} variant="build" groupLabel={group.label} />
        ))}
      </div>
    </section>
  )
}
