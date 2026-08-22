import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useStore } from '../store'
import { useThemedLogo } from '../hooks/useThemedLogo'
import { speedLabel } from '../models'

// Where "Open" points. Proxied apps are served by the Hub itself at
// /app/{slug}/, so the link is root-relative and works unchanged over the LAN,
// through a Cloudflare Tunnel and over Tailscale -- no host, no port. Recipes
// that still publish a port of their own keep the old direct link.
export function openUrl(recipe) {
  if (recipe.app_url) return recipe.app_url
  return `http://${location.hostname}:${recipe.ui?.port ?? 8080}${recipe.ui?.path ?? '/'}`
}

// "35B-A3B" for MoE (total + active), plain "27B" for dense.
export function formatParams(recipe) {
  const b = (n) => (Number.isInteger(n) ? n : n.toFixed(1))
  const total = `${b(recipe.params_b)}B`
  return recipe.active_params_b != null ? `${total}-A${b(recipe.active_params_b)}B` : total
}

// The chip holding the value the list is sorted by is filled in, so the fact
// you are ranking on stands out from the specs around it.
function chipClass(active) {
  return active
    ? 'text-[10px] font-label font-bold text-primary-on bg-primary px-2 py-0.5 rounded-full'
    : 'text-[10px] font-label text-text-muted bg-surface-high px-2 py-0.5 rounded-full'
}

// `hideCategories` is set for the Ready-to-Serve Models section, where every
// card is an LLM and the category chip carries no information.
export default function RecipeCard({ recipe, hideCategories = false, highlight = null }) {
  const installing = useStore((s) => s.installing)
  const updating = useStore((s) => s.updating)
  const installRecipe = useStore((s) => s.installRecipe)
  const [logoFailed, setLogoFailed] = useState(false)

  const logoUrl = useThemedLogo(recipe.logo)
  const isBuilding = !!installing[recipe.slug]
  const isUpdating = !!updating[recipe.slug]
  const isBusy = isBuilding || isUpdating

  const handleInstall = (e) => {
    e.preventDefault()
    e.stopPropagation()
    installRecipe(recipe.slug)
  }

  const recipeCategories = Array.isArray(recipe.categories) && recipe.categories.length > 0
    ? recipe.categories
    : [recipe.category]

  return (
    <Link
      to={`/app/${recipe.slug}`}
      className="relative block no-underline text-inherit overflow-hidden bg-surface rounded-2xl p-4 card-hover cursor-pointer group"
    >
      {/* Running indicator - top border */}
      {recipe.running && (
        <div className={`absolute top-0 left-0 right-0 h-[2px] ${
          recipe.ready
            ? 'bg-primary'
            : 'bg-warning animate-pulse'
        }`} />
      )}

      <div className="flex items-start gap-3.5">
        {/* Icon */}
        {logoUrl && !logoFailed ? (
          <img
            src={logoUrl}
            alt={recipe.name}
            className="w-14 h-14 rounded-xl object-contain bg-surface-high p-2 shrink-0 transition-transform group-hover:scale-105"
            onError={() => setLogoFailed(true)}
          />
        ) : (
          <div className="w-14 h-14 rounded-xl bg-surface-high flex items-center justify-center text-2xl shrink-0 transition-transform group-hover:scale-105">
            {recipe.icon || '◻'}
          </div>
        )}

        {/* Content */}
        <div className="flex-1 min-w-0">
          {/* Wraps to two lines rather than truncating — names like
              "Nemotron-3.5 Lightning 30B-A3B NVFP4 + DSpark" lose their
              meaning the moment the tail is cut off. */}
          <h3 className="font-semibold text-sm text-text leading-tight line-clamp-2 break-words m-0 font-display" title={recipe.name}>
            {recipe.name}
          </h3>
          <p className="text-[11px] text-text-dim mt-0.5 m-0">{recipe.author}</p>
          <p className="text-xs text-text-muted mt-1.5 m-0 line-clamp-1">{recipe.description}</p>

          {/* Tags */}
          <div className="flex flex-wrap items-center gap-1.5 mt-2">
            {!hideCategories && recipeCategories.slice(0, 2).map((cat) => (
              <span key={cat} className="text-[10px] font-label text-secondary bg-secondary/10 px-2 py-0.5 rounded-full">
                {cat}
              </span>
            ))}
            {!recipe.docker?.gpu && (
              <span className="text-[10px] font-label text-text-dim bg-surface-high px-2 py-0.5 rounded-full">CPU</span>
            )}
            {recipe.engine && (
              <span className="text-[10px] font-label text-text-muted bg-surface-high px-2 py-0.5 rounded-full">{recipe.engine}</span>
            )}
            {recipe.arch && (
              <span className="text-[10px] font-label text-text-muted bg-surface-high px-2 py-0.5 rounded-full uppercase">
                {recipe.arch === 'moe' ? 'MoE' : 'Dense'}
              </span>
            )}
            {recipe.params_b != null && (
              <span className={chipClass(highlight === 'params')}>
                {formatParams(recipe)}
              </span>
            )}
            {recipe.quantization && (
              <span className={chipClass(false)}>{recipe.quantization}</span>
            )}
            {recipe.weights_gb != null && (
              <span className={chipClass(highlight === 'size')}>{recipe.weights_gb} GB</span>
            )}
            {recipe.tokens_per_second != null && (
              <span className={highlight === 'speed'
                ? chipClass(true)
                : 'text-[10px] font-label text-primary bg-primary/10 px-2 py-0.5 rounded-full'}
              >
                {speedLabel(recipe)}
              </span>
            )}
          </div>
        </div>

        {/* Action */}
        <div className="shrink-0 flex flex-col items-end gap-1.5 mt-1">
          {isBuilding && (
            <span className="text-primary text-xs font-medium font-label animate-pulse">
              <span className="inline-block animate-spin">⟳</span> Building
            </span>
          )}
          {isUpdating && (
            <span className="text-primary text-xs font-medium font-label animate-pulse">
              <span className="inline-block animate-spin">⟳</span> Updating
            </span>
          )}
          {!isBusy && recipe.running && recipe.ready && (
            <a
              href={openUrl(recipe)}
              target="_blank"
              rel="noreferrer"
              onClick={(e) => e.stopPropagation()}
              className="btn-secondary px-3.5 py-1.5 text-[11px] font-semibold no-underline"
            >
              Open
            </a>
          )}
          {!isBusy && recipe.starting && (
            <span className="text-warning text-[11px] font-medium font-label animate-pulse">Starting...</span>
          )}
          {!isBusy && !recipe.running && !recipe.starting && !recipe.installed && (
            <button onClick={handleInstall} className="btn-primary px-3.5 py-1.5 text-[11px] font-semibold">
              Install
            </button>
          )}
          {!isBusy && !recipe.running && !recipe.starting && recipe.installed && (
            <span className="text-text-dim text-[11px] font-label bg-surface-high px-2.5 py-1 rounded-lg">Stopped</span>
          )}
        </div>
      </div>
    </Link>
  )
}
