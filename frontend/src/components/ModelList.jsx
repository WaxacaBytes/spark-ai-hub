import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useStore } from '../store'
import { useThemedLogo } from '../hooks/useThemedLogo'
import { formatParams } from './RecipeCard'
import { buildLabel, displayName } from '../models'
import { openUrl as openUrlFor } from './RecipeCard'
import Hint from './Hint'

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
const VARIANTS = {
  // The two speed columns are deliberately separate rather than one "19.5–59.4"
  // cell. A range string has no common decimal point, so a column of them
  // cannot be scanned vertically — and comparing builds against each other is
  // the only reason this table exists. Cards and the hero print the range,
  // where it reads as prose and costs nothing.
  build: {
    columns: 'minmax(0,1fr) 56px 44px 44px 66px 50px 34px 48px',
    headers: ['Engine'],
    fixedHeaders: ['Writing', 'Editing', 'Params', 'On disk', 'Ctx'],
    first: 'Build',
    lead: null,
    wrap: false,
    aaIndex: false,
  },
  ranked: {
    columns: '26px minmax(200px,1fr) 56px 46px 50px 50px 74px 62px 42px 44px 62px',
    headers: ['Engine', 'Quant'],
    fixedHeaders: ['Writing', 'Editing', 'Params', 'On disk', 'Ctx', 'AA Index'],
    first: 'Model · build',
    lead: 'rank',
    wrap: true,
    aaIndex: true,
  },
}

// Phones get the same rows with the columns a thumb-width screen can hold:
// the name, both speeds, one more figure and the action. Engine and quant
// move under the name rather than disappearing, so every build stays on
// screen and still tells itself apart. The third figure follows the sort —
// sorted by params you see params — and is the size on disk otherwise.
const COMPACT = {
  build: {
    columns: 'minmax(0,1fr) 42px 42px 48px 54px',
    first: 'Build',
    lead: null,
    wrap: false,
  },
  ranked: {
    columns: '16px minmax(0,1fr) 42px 42px 48px 54px',
    first: 'Model · build',
    lead: 'rank',
    wrap: true,
  },
}
const COMPACT_THIRD = { params: 'Params', 'aa-index': 'AA Index' }

function variantFor(variant, narrow, highlight) {
  if (!narrow) return VARIANTS[variant]
  return {
    ...COMPACT[variant],
    headers: [],
    fixedHeaders: ['Writing', 'Editing', COMPACT_THIRD[highlight] || 'On disk'],
    aaIndex: highlight === 'aa-index',
    sub: true,
  }
}

const NARROW = '(max-width: 639px)'

export function useNarrow() {
  const [narrow, setNarrow] = useState(() => window.matchMedia(NARROW).matches)
  useEffect(() => {
    const mq = window.matchMedia(NARROW)
    const on = () => setNarrow(mq.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])
  return narrow
}

// Left to fill the window, the name column absorbs every spare pixel and
// pushes the numbers ~900px from the name they describe; past that width a
// row stops reading as one thing. So a wide window buys more tables rather
// than wider ones — see COL_FIT below.

// Splitting a band across tables only pays once it is taller than the heading
// stack above it.
const SPLIT_THRESHOLD = 8

// Why two speeds, said where the two numbers are.
const HEADER_HINTS = {
  Writing: 'Sustained tok/s writing new text from a short prompt (thinking on, 512 tokens, '
    + 'temperature 0). Nothing can be copied from the prompt, so this is close to plain decode '
    + 'speed — the figure every recipe carries.',
  Editing: 'Sustained tok/s reproducing a document with a small change applied (thinking off, '
    + '3000 tokens). Most of the output is already in the prompt, so builds that draft '
    + 'speculatively (DFlash, DSpark, MTP) are far faster here than at writing — which is why '
    + 'one number cannot describe them.',
  'AA Index': 'Artificial Analysis Intelligence Index: a published capability score for the '
    + 'base model, the same for every build of it.',
}

export function formatContext(tokens) {
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

function ColumnHeader({ v }) {
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
      {/* Writing and Editing are bare numbers: "27.5 tok/s" in a 44px column
          truncates, and a second header line for the unit costs a row of height
          on every table. What they are and what unit they carry rides on the
          heading itself, for whoever points at it. */}
      {v.fixedHeaders.map((h) => (
        <Hint
          key={h}
          text={HEADER_HINTS[h]}
          className={`font-label text-[9px] font-semibold uppercase tracking-wider text-text-dim ${
            h === 'AA Index' ? 'text-center' : 'text-right'
          } ${HEADER_HINTS[h] ? 'cursor-default underline decoration-dotted decoration-text-dim/60 underline-offset-2 outline-none' : ''}`}
        >
          {h}
        </Hint>
      ))}
      <span />
    </div>
  )
}

function BuildRow({ recipe, variant, v, rank, highlight, onFrontier }) {
  const installing = useStore((s) => s.installing)
  const updating = useStore((s) => s.updating)
  const installRecipe = useStore((s) => s.installRecipe)
  const requestLaunch = useStore((s) => s.requestLaunch)
  const [logoFailed, setLogoFailed] = useState(false)
  const logoUrl = useThemedLogo(recipe.logo)

  const isBusy = !!installing[recipe.slug] || !!updating[recipe.slug]
  const openUrl = openUrlFor(recipe)
  const running = recipe.running && recipe.ready
  const state = runState(recipe, isBusy)

  // Grouped rows sit under a heading that names the model, so they name only
  // the build. The other two stand alone and carry the model name and logo.
  const label = variant === 'build' ? buildLabel(recipe) : displayName(recipe)

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
        <span className="flex min-w-0 flex-1 flex-col">
          <span
            className={`min-w-0 font-label text-[11px] font-bold text-text ${
              // Standalone rows carry a full model name and get a second line.
              // Cutting the tail off "Nemotron-3 Nano Omni 30B-A3B Reasoning" is
              // what makes two builds look like the same one.
              v.wrap ? 'line-clamp-2 leading-tight' : 'truncate'
            }`}
            title={recipe.name}
          >
            {label}
          </span>
          {v.sub && (
            <span className="truncate font-label text-[9px] text-text-dim">
              {[variant === 'ranked' && buildLabel(recipe), recipe.engine].filter(Boolean).join(' · ')}
            </span>
          )}
        </span>
        {/* Marks a build no other one in the catalog beats on both axes that
            actually trade off: nothing writes faster AND scores higher on
            the AA Index. A slower build can still be the right pick for its
            size or its editing speed — this only ever claims the one thing. */}
        {onFrontier && (
          <span
            className="shrink-0 font-label text-[11px] font-bold leading-none text-success"
            title="Pareto frontier: no build in the catalog both writes faster and scores higher on the AA Index"
          >
            ★
          </span>
        )}
      </span>

      {v.headers.includes('Engine') && (
        <Cell className={`text-text-muted ${v.wrap ? '' : 'text-[10px]'}`}>{recipe.engine}</Cell>
      )}
      {v.headers.includes('Quant') && <Cell className="text-text-muted">{recipe.quantization}</Cell>}

      {/* Speed leads: it is the number that decides whether a build is usable. */}
      <span className={`truncate text-right font-display text-[13px] font-extrabold tabular-nums ${
        recipe.tokens_per_second != null ? 'text-primary' : 'text-text-dim'
      }`}>
        {recipe.tokens_per_second ?? '—'}
      </span>
      {/* Same weight as Writing only when the two numbers actually differ —
          when a build has no drafter they're identical, and bolding a repeat
          of the number already in the row to its left says nothing. */}
      <span
        className={`truncate text-right tabular-nums ${
          highlight === 'editing'
            ? 'font-label text-[11px] font-bold text-secondary'
            : recipe.tokens_per_second_editing !== recipe.tokens_per_second
              ? 'font-display text-[13px] font-extrabold text-primary'
              : 'font-label text-[11px] text-text-muted'
        }`}
        title={recipe.tokens_per_second_editing != null ? `${recipe.tokens_per_second_editing} tok/s sustained, reproducing a document with a small change applied (${recipe.editing_workload || 'code-edit'}, thinking off)` : undefined}
      >
        {recipe.tokens_per_second_editing ?? '—'}
      </span>
      {v.fixedHeaders.includes('Params') && (
        <Cell className={`text-right ${highlight === 'params' ? 'text-secondary font-bold' : 'text-text-muted'}`}>
          {recipe.params_b != null ? formatParams(recipe) : null}
        </Cell>
      )}
      {v.fixedHeaders.includes('On disk') && (
        <Cell className={`text-right ${highlight === 'size' ? 'text-secondary font-bold' : 'text-text-muted'}`}>
          {recipe.weights_gb != null ? `${recipe.weights_gb} GB` : null}
        </Cell>
      )}
      {v.fixedHeaders.includes('Ctx') && (
        <Cell className="text-right text-text-muted">{formatContext(recipe.context_tokens)}</Cell>
      )}
      {/* A capability score, not a speed one — published per base model by
          Artificial Analysis, so it does not vary with quantization or
          drafter the way Writing/Editing do. Grouped "build" tables already
          say it once in the model heading, so this column only exists on the
          ranked leaderboards, where every row is an independent build. */}
      {v.aaIndex && (
        <Cell
          className={`text-center ${highlight === 'aa-index' ? 'text-secondary font-bold' : 'text-text-muted'}`}
          title={recipe.artificial_analysis_index != null ? `${recipe.artificial_analysis_index} on the Artificial Analysis Intelligence Index` : undefined}
        >
          {recipe.artificial_analysis_index ?? null}
        </Cell>
      )}
      <span className="min-w-0">{action}</span>
    </Link>
  )
}

// As many tables of builds as the window fits, side by side. The rank numbers
// carry the reading order: it runs down the first table, then down the next.
//
// The column count is measured rather than left to `repeat(auto-fit, minmax(a,
// b))`, which does not do what it looks like it does — when the max is a
// definite length the track count is computed from *that*, so a 1380px shelf of
// minmax(560px, 730px) fits one column, not two, and the second table wrapped
// underneath into the empty half of the screen.
// The ranked row's fixed columns (rank, engine, quant, five numbers, action)
// already total ~560px, so the width that decides whether another table fits is
// not the width at which a table survives — it is that plus enough left over to
// read a model name. Splitting the moment each table could be 650px left ~90px
// for the name and produced rows reading "Gemm / 4 31…". COL_FIT is therefore
// the *comfortable* width: another column is added only when every column can
// still be this wide, which keeps the name at 220px or more at every size.
const COL_FIT = 780
const COL_MAX = 900   // wider and the numbers drift too far from the name
const COL_GAP = 12    // must match the grid's gap-3
const MIN_ROWS_PER_COL = 4

function useColumnCount(ref, enabled) {
  const [cols, setCols] = useState(1)
  useEffect(() => {
    if (!enabled || !ref.current) return undefined
    const measure = (width) => setCols(Math.max(1, Math.floor((width + COL_GAP) / (COL_FIT + COL_GAP))))
    const ro = new ResizeObserver(([entry]) => measure(entry.contentRect.width))
    ro.observe(ref.current)
    measure(ref.current.getBoundingClientRect().width)
    return () => ro.disconnect()
  }, [ref, enabled])
  return enabled ? cols : 1
}

function chunk(items, n) {
  if (n <= 1) return [items]
  const per = Math.ceil(items.length / n)
  const out = []
  for (let i = 0; i < items.length; i += per) out.push(items.slice(i, i + per))
  return out
}

export default function ModelList({ items, variant = 'ranked', highlight = null, frontier = null }) {
  const ref = useRef(null)
  const v = variantFor(variant, useNarrow(), highlight)
  const splittable = variant === 'ranked' && items.length >= SPLIT_THRESHOLD
  const fits = useColumnCount(ref, splittable)
  // Never so many columns that each holds a row or two — a table needs enough
  // rows to be worth its header.
  const cols = splittable ? Math.max(1, Math.min(fits, Math.ceil(items.length / MIN_ROWS_PER_COL))) : 1
  const halves = chunk(items, cols)
  // Rank is continuous across the tables: it runs down the first, then the next.
  const offsets = halves.reduce((acc, h) => [...acc, acc[acc.length - 1] + h.length], [0])

  return (
    <div
      ref={ref}
      className="grid w-full items-start gap-3"
      style={{
        gridTemplateColumns: `repeat(${halves.length}, minmax(0, ${COL_MAX}px))`,
        justifyContent: 'start',
      }}
    >
      {halves.map((half, i) => (
        <section key={i} className="rounded-2xl bg-surface p-3 ring-1 ring-glass-border">
          <ColumnHeader v={v} />
          <div className="mt-0.5">
            {half.map((r, j) => (
              <BuildRow
                key={r.slug}
                recipe={r}
                variant={variant}
                v={v}
                rank={offsets[i] + j + 1}
                highlight={highlight}
                onFrontier={frontier?.has(r.slug)}
              />
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}

// One model with every build it has, all on screen.
//
// Nothing here collapses. An earlier version showed only the best build and
// put the rest behind a "+5 more builds" link; that hid two thirds of the
// catalog behind a control readers never found, and expanding it reflowed the
// page so you lost your place.
export function ModelBlock({ group, frontier = null }) {
  const v = variantFor('build', useNarrow(), null)
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
        {group.lead.artificial_analysis_index != null && (
          <span
            className="shrink-0 font-label text-[10px] font-bold tabular-nums text-text-muted"
            title={`${group.lead.artificial_analysis_index} on the Artificial Analysis Intelligence Index — one score per model, shared by every build below`}
          >
            AA {group.lead.artificial_analysis_index}
          </span>
        )}
        <span className="shrink-0 font-label text-[10px] text-text-dim">
          {group.items.length} build{group.items.length > 1 ? 's' : ''}
        </span>
      </header>

      {/* Every block gets the column key, single-build ones included: without
          it those rows were a line of unlabelled numbers. */}
      <ColumnHeader v={v} />

      <div className="mt-0.5">
        {group.items.map((r) => (
          <BuildRow
            key={r.slug}
            recipe={r}
            variant="build"
            v={v}
            onFrontier={frontier?.has(r.slug)}
          />
        ))}
      </div>
    </section>
  )
}
