import { useState } from 'react'
import { useStore } from '../store'

export default function RecipeCard({ recipe }) {
  const selectRecipe = useStore((s) => s.selectRecipe)
  const installing = useStore((s) => s.installing)
  const installRecipe = useStore((s) => s.installRecipe)
  const [logoFailed, setLogoFailed] = useState(false)

  const logoUrl = recipe.logo || ''
  const isBuilding = installing === recipe.slug

  const handleInstall = (e) => {
    e.stopPropagation()
    installRecipe(recipe.slug)
  }

  return (
    <div
      onClick={() => selectRecipe(recipe.slug)}
      className="relative overflow-hidden bg-surface rounded-2xl p-5 card-hover cursor-pointer group"
    >
      {/* Running glow bar */}
      {recipe.running && (
        <div className={`absolute top-0 left-0 right-0 h-[2px] ${
          recipe.ready
            ? 'bg-gradient-to-r from-primary via-primary-dim to-primary'
            : 'bg-gradient-to-r from-warning to-amber-400 animate-pulse'
        }`} />
      )}

      <div className="flex items-center gap-4">
        {/* Icon */}
        {logoUrl && !logoFailed ? (
          <img
            src={logoUrl}
            alt={recipe.name}
            className="w-16 h-16 rounded-2xl object-contain bg-surface-high p-2.5 shrink-0 transition-transform group-hover:scale-105"
            onError={() => setLogoFailed(true)}
          />
        ) : (
          <div className="w-16 h-16 rounded-2xl bg-surface-high flex items-center justify-center text-3xl shrink-0 transition-transform group-hover:scale-105">
            {recipe.icon || '◻'}
          </div>
        )}

        {/* Name + author + description */}
        <div className="flex-1 min-w-0">
          <h3 className="font-bold text-[15px] text-text leading-tight truncate m-0 font-[Manrope]">
            {recipe.name}
          </h3>
          <p className="text-xs text-text-dim mt-0.5 m-0">{recipe.author}</p>
          <p className="text-xs text-text-muted mt-1.5 m-0 line-clamp-1">{recipe.description}</p>
        </div>

        {/* Right: status or action */}
        <div className="shrink-0 flex flex-col items-end gap-1.5">
          {isBuilding && (
            <span className="text-primary text-xs font-medium animate-pulse">
              <span className="inline-block animate-spin">⟳</span> Building
            </span>
          )}
          {!isBuilding && recipe.running && recipe.ready && (
            <a
              href={`http://${location.hostname}:${recipe.ui?.port ?? 8080}${recipe.ui?.path ?? '/'}`}
              target="_blank"
              rel="noreferrer"
              onClick={(e) => e.stopPropagation()}
              className="btn-primary px-4 py-1.5 text-xs font-semibold no-underline"
            >
              Open
            </a>
          )}
          {!isBuilding && recipe.running && !recipe.ready && (
            <span className="text-warning text-xs font-medium animate-pulse">Starting...</span>
          )}
          {!isBuilding && !recipe.running && !recipe.installed && (
            <button onClick={handleInstall} className="btn-primary px-4 py-1.5 text-xs font-semibold">
              Get
            </button>
          )}
          {!isBuilding && !recipe.running && recipe.installed && (
            <span className="text-text-dim text-xs bg-surface-high px-3 py-1 rounded-lg">Stopped</span>
          )}
        </div>
      </div>
    </div>
  )
}
