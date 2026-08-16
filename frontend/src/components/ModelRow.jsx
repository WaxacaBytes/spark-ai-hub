import { Link } from 'react-router-dom'
import { useStore } from '../store'
import { formatParams } from './RecipeCard'

// One build, as a row of aligned numbers. Shared by the two ways the catalog
// lists builds: grouped under their model (ModelGroup) and ranked flat against
// each other (ModelTable). Both use the same column template so a row means
// the same thing wherever you meet it.

// Engine has to hold "llama.cpp" and Params "25.2B-A3.8B" — the two widest
// values in the fixed columns. The action button gives up the room.
export const COLUMNS = 'minmax(0,1fr) 56px 48px 72px 50px 34px 48px'
export const HEADERS = ['Engine']

// The flat ranked table has room for one more fact. Grouped, the build label
// already names the quantization ("NVFP4 DFlash"); a ranked row has no group
// heading above it, so it spells everything out.
//
// It leads with a rank number, because a band is split across two tables
// side by side and the number is what makes their reading order obvious.
export const WIDE_COLUMNS = '26px minmax(0,1fr) 70px 70px 56px 80px 62px 42px 62px'
export const WIDE_HEADERS = ['Engine', 'Quant']

function formatContext(tokens) {
  if (!tokens) return null
  if (tokens >= 1024 * 1024) return `${Math.round(tokens / (1024 * 1024))}M`
  return `${Math.round(tokens / 1024)}K`
}

export function ColumnHeader({ first = 'Build', extra = HEADERS, columns = COLUMNS, ranked = false }) {
  return (
    <div
      className="grid gap-x-1.5 border-b border-outline-dim px-2 pb-1"
      style={{ gridTemplateColumns: columns }}
    >
      {ranked && <span />}
      <span className="font-label text-[9px] font-semibold uppercase tracking-wider text-text-dim">
        {first}
      </span>
      {extra.map((h) => (
        <span
          key={h}
          className="font-label text-[9px] font-semibold uppercase tracking-wider text-text-dim"
        >
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

function Cell({ children, className = '' }) {
  return (
    <span className={`truncate font-label text-[11px] tabular-nums ${className}`}>
      {children ?? <span className="text-text-dim">—</span>}
    </span>
  )
}

// `label` may be a node (the ranked list puts a logo beside the name), so the
// tooltip text comes in separately rather than being read off it.
export function BuildRow({ recipe, label, title, highlight = null, wide = false, rank = null, wrapLabel = false }) {
  const installing = useStore((s) => s.installing)
  const updating = useStore((s) => s.updating)
  const installRecipe = useStore((s) => s.installRecipe)
  const requestLaunch = useStore((s) => s.requestLaunch)

  const isBusy = !!installing[recipe.slug] || !!updating[recipe.slug]
  const openUrl = `http://${location.hostname}:${recipe.ui?.port ?? 8080}${recipe.ui?.path ?? '/'}`
  const running = recipe.running && recipe.ready

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
      style={{ gridTemplateColumns: wide ? WIDE_COLUMNS : COLUMNS }}
    >
      {rank != null && (
        <span className="text-right font-label text-[10px] tabular-nums text-text-dim">{rank}</span>
      )}

      <span className="flex min-w-0 items-center gap-1.5">
        {(running || recipe.starting || recipe.installed) && (
          <span
            className={`h-1.5 w-1.5 shrink-0 rounded-full ${
              running ? 'bg-primary' : recipe.starting ? 'bg-warning animate-pulse' : 'bg-text-dim'
            }`}
            title={running ? 'Running' : recipe.starting ? 'Starting' : 'Installed'}
          />
        )}
        <span
          className={`min-w-0 flex-1 font-label text-[11px] font-bold text-text ${
            // Ranked rows carry a full model name and are allowed a second
            // line. Cutting the tail off "Nemotron-3 Nano Omni 30B-A3B
            // Reasoning" is what makes two builds look like the same one.
            wrapLabel ? 'line-clamp-2 leading-tight' : 'truncate'
          }`}
          title={title ?? (typeof label === 'string' ? label : undefined)}
        >
          {label}
        </span>
      </span>

      <Cell className={`text-text-muted ${wide ? '' : 'text-[10px]'}`}>{recipe.engine}</Cell>
      {wide && <Cell className="text-text-muted">{recipe.quantization}</Cell>}

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
