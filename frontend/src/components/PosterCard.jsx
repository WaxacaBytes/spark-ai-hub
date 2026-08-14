import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useStore } from '../store'
import { useThemedLogo } from '../hooks/useThemedLogo'
import { posterFor } from '../covers'
import { formatParams } from './RecipeCard'

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function formatMonth(date) {
  if (!date) return null
  const [year, month] = date.split('-')
  return month ? `${MONTHS[Number(month) - 1]} ${year}` : year
}

// The fact the catalog is currently sorted by. It is promoted out of the spec
// chips into the corner badge, so the value you are ranking on is readable
// without having to read the tile.
const HIGHLIGHTS = {
  release: { label: 'Released', value: (r) => formatMonth(r.release_date) },
  params: { label: 'Params', value: (r) => (r.params_b != null ? formatParams(r) : null) },
  size: { label: 'On disk', value: (r) => (r.weights_gb != null ? `${r.weights_gb} GB` : null) },
  speed: { label: 'Speed', value: (r) => (r.tokens_per_second != null ? `${r.tokens_per_second} tok/s` : null) },
}

// Tokens/sec keeps its own corner slot whatever the catalog is sorted by —
// it is the one number that decides whether a build is usable day to day.

// A poster tile for the catalog rows. The name is allowed to wrap to two
// lines and is never truncated mid-word — long builds like
// "Nemotron-3.5 Lightning 30B-A3B NVFP4 + DSpark" have to stay readable.
export default function PosterCard({ recipe, highlight = null }) {
  const installing = useStore((s) => s.installing)
  const updating = useStore((s) => s.updating)
  const installRecipe = useStore((s) => s.installRecipe)
  const requestLaunch = useStore((s) => s.requestLaunch)
  const [artFailed, setArtFailed] = useState(false)
  const [logoFailed, setLogoFailed] = useState(false)

  const logoUrl = useThemedLogo(recipe.logo)
  const isBusy = !!installing[recipe.slug] || !!updating[recipe.slug]
  const openUrl = `http://${location.hostname}:${recipe.ui?.port ?? 8080}${recipe.ui?.path ?? '/'}`

  const status = isBusy
    ? { label: 'Building', dot: 'bg-secondary animate-pulse' }
    : recipe.running && recipe.ready
      ? { label: 'Running', dot: 'bg-primary' }
      : recipe.running || recipe.starting
        ? { label: 'Starting', dot: 'bg-warning animate-pulse' }
        : recipe.installed
          ? { label: 'Installed', dot: 'bg-text-dim' }
          : null

  // The facts that actually differentiate one build of a model from another.
  // They are chips rather than a dim run-on line: at 196px a dot-joined line
  // truncates, and truncating is what buried them.
  const specs = [
    recipe.params_b != null && { id: 'params', text: formatParams(recipe) },
    recipe.quantization && { id: 'quant', text: recipe.quantization },
    recipe.weights_gb != null && { id: 'size', text: `${recipe.weights_gb} GB` },
    recipe.tokens_per_second != null && { id: 'speed', text: `${recipe.tokens_per_second} tok/s` },
    highlight === 'release' && { id: 'release', text: formatMonth(recipe.release_date) },
  ].filter((s) => s && s.text)

  const badge = HIGHLIGHTS[highlight]
  const badgeValue = badge?.value(recipe)
  const speed = recipe.tokens_per_second
  const speedLeads = highlight === 'speed'
  // Whatever the corner already states is dropped from the chips — printing
  // tok/s twice on one tile is noise, not emphasis.
  const chips = specs.filter((s) => s.id !== highlight && !(s.id === 'speed' && speed != null))

  return (
    <Link
      to={`/app/${recipe.slug}`}
      className="poster-card group relative block w-[196px] shrink-0 no-underline text-inherit"
      title={recipe.name}
    >
      <div className="relative aspect-[2/3] w-full overflow-hidden rounded-xl bg-surface-high ring-1 ring-glass-border">
        {!artFailed && (
          <img
            src={posterFor(recipe)}
            alt=""
            loading="lazy"
            onError={() => setArtFailed(true)}
            className="absolute inset-0 h-full w-full object-cover transition-transform duration-500 group-hover:scale-[1.06]"
          />
        )}

        {/* Legibility scrim for the title block. */}
        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-3/5 bg-gradient-to-t from-black/92 via-black/55 to-transparent" />

        {/* Vendor logo, always legible against the art. */}
        {logoUrl && !logoFailed && (
          <span className="absolute left-2.5 top-2.5 flex h-9 w-9 items-center justify-center rounded-lg bg-black/45 p-1.5 ring-1 ring-white/15 backdrop-blur-md">
            <img
              src={logoUrl}
              alt=""
              loading="lazy"
              onError={() => setLogoFailed(true)}
              className="h-full w-full object-contain"
            />
          </span>
        )}

        <div className="absolute right-2.5 top-2.5 flex flex-col items-end gap-1">
          {/* The sorted metric, unless that metric is speed — the pill below
              already carries speed, and louder. */}
          {badgeValue && !speedLeads && (
            <span className="flex flex-col items-end rounded-lg bg-primary px-2 py-1 shadow-lg shadow-black/25 ring-1 ring-black/10">
              <span className="font-label text-[8px] font-bold uppercase tracking-wider text-primary-on/75">
                {badge.label}
              </span>
              <span className="font-display text-[14px] font-extrabold leading-tight text-primary-on">
                {badgeValue}
              </span>
            </span>
          )}

          {speed != null && (
            <span
              className={`flex flex-col items-end gap-1 rounded-lg px-2 py-1 shadow-lg shadow-black/25 ${
                speedLeads
                  ? 'bg-primary ring-1 ring-black/10'
                  : 'bg-black/55 ring-1 ring-white/15 backdrop-blur-md'
              }`}
            >
              {speedLeads && (
                <span className="font-label text-[8px] font-bold uppercase tracking-wider text-primary-on/75">
                  Speed
                </span>
              )}
              <span
                className={speedLeads
                  ? 'font-display text-[14px] font-extrabold leading-tight text-primary-on'
                  : 'font-label text-[10px] font-bold leading-none text-primary'}
              >
                {speed} tok/s
              </span>
            </span>
          )}
        </div>

        {/* Title + hover action share one bottom-anchored stack, so the
            button slides in *under* the name instead of covering it. */}
        <div className="absolute inset-x-0 bottom-0 p-3">
          {status && (
            <p className="m-0 mb-1 flex items-center gap-1.5 font-label text-[10px] font-semibold text-white/85">
              <span className={`h-1.5 w-1.5 rounded-full ${status.dot}`} />
              {status.label}
            </p>
          )}
          <h3 className="m-0 font-display text-[13px] font-bold leading-snug text-white line-clamp-2 break-words drop-shadow">
            {recipe.name}
          </h3>
          {chips.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1">
              {chips.map((s) => (
                <span
                  key={s.id}
                  className="rounded-md bg-white/20 px-1.5 py-0.5 font-label text-[10px] font-bold text-white backdrop-blur-sm"
                >
                  {s.text}
                </span>
              ))}
            </div>
          )}

          <div className="grid grid-rows-[0fr] opacity-0 transition-all duration-200 group-hover:mt-2 group-hover:grid-rows-[1fr] group-hover:opacity-100">
            <div className="overflow-hidden">
              {recipe.running && recipe.ready ? (
                <a
                  href={openUrl}
                  target="_blank"
                  rel="noreferrer"
                  onClick={(e) => e.stopPropagation()}
                  className="btn-primary block w-full py-1.5 text-center text-[11px] font-bold no-underline"
                >
                  Open ↗
                </a>
              ) : !recipe.installed && !recipe.starting && !isBusy ? (
                <button
                  onClick={(e) => { e.preventDefault(); e.stopPropagation(); installRecipe(recipe.slug) }}
                  className="btn-primary w-full py-1.5 text-[11px] font-bold"
                >
                  Install
                </button>
              ) : recipe.installed && !recipe.running && !recipe.starting && !isBusy ? (
                <button
                  onClick={(e) => { e.preventDefault(); e.stopPropagation(); requestLaunch(recipe.slug) }}
                  className="btn-primary w-full py-1.5 text-[11px] font-bold"
                >
                  Launch
                </button>
              ) : (
                <span className="block w-full rounded-xl bg-white/15 py-1.5 text-center text-[11px] font-semibold text-white backdrop-blur">
                  Details
                </span>
              )}
            </div>
          </div>
        </div>
      </div>

      <p className="m-0 mt-2 truncate font-label text-[11px] text-text-dim">{recipe.author}</p>
    </Link>
  )
}
