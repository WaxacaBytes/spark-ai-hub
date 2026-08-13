import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useStore } from '../store'
import { useThemedLogo } from '../hooks/useThemedLogo'
import { posterFor } from '../covers'
import { formatParams } from './RecipeCard'

// A poster tile for the catalog rows. The name is allowed to wrap to two
// lines and is never truncated mid-word — long builds like
// "Nemotron-3.5 Lightning 30B-A3B NVFP4 + DSpark" have to stay readable.
export default function PosterCard({ recipe }) {
  const installing = useStore((s) => s.installing)
  const updating = useStore((s) => s.updating)
  const installRecipe = useStore((s) => s.installRecipe)
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

  // A short spec line: the two or three facts that actually differentiate
  // one build of a model from another.
  const specs = [
    recipe.params_b != null && formatParams(recipe),
    recipe.quantization,
    recipe.weights_gb != null && `${recipe.weights_gb} GB`,
  ].filter(Boolean)

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

        {recipe.tokens_per_second != null && (
          <span className="absolute right-2.5 top-2.5 rounded-full bg-primary/90 px-2 py-1 text-[10px] font-bold font-label text-primary-on">
            {recipe.tokens_per_second} tok/s
          </span>
        )}

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
          {specs.length > 0 && (
            <p className="m-0 mt-1 truncate font-label text-[10px] text-white/65">
              {specs.join(' · ')}
            </p>
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
