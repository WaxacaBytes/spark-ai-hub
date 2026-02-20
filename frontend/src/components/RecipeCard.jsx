import { useState } from 'react'
import { useStore } from '../store'

export default function RecipeCard({ recipe }) {
  const selectRecipe = useStore((s) => s.selectRecipe)
  const installing = useStore((s) => s.installing)
  const [logoFailed, setLogoFailed] = useState(false)

  const logoUrl = recipe.logo || ''
  const officialUrl = recipe.website || ''
  const isBuilding = installing === recipe.slug

  return (
    <div
      onClick={() => selectRecipe(recipe.slug)}
      className="relative overflow-hidden bg-surface border border-border rounded-xl p-5 flex flex-col gap-3 hover:border-border-hover hover:bg-surface-hover transition-all cursor-pointer active:scale-[0.98]"
    >
      {recipe.running && (
        <div className={`absolute top-0 left-0 right-0 h-0.5 ${
          recipe.ready
            ? 'bg-gradient-to-r from-spark to-lime-400'
            : 'bg-gradient-to-r from-amber-500 to-amber-400 animate-pulse'
        }`} />
      )}

      <div className="flex justify-between items-start">
        <div className="flex items-center gap-2.5">
          {logoUrl && !logoFailed ? (
            <img
              src={logoUrl}
              alt={`${recipe.name} logo`}
              className="w-8 h-8 rounded-md object-contain bg-white/95 p-1"
              onError={() => setLogoFailed(true)}
            />
          ) : (
            <span className="text-2xl">{recipe.icon || '◻'}</span>
          )}
          <div>
            <div className="font-bold text-[15px] text-text leading-tight">{recipe.name}</div>
            <div className="text-[11px] text-text-dim mt-0.5">by {recipe.author}</div>
          </div>
        </div>
        {isBuilding && (
          <span className="text-spark text-xs"><span className="inline-block animate-spin mr-1">⟳</span>Building...</span>
        )}
        {!isBuilding && recipe.running && recipe.ready && (
          <span className="text-emerald-400 text-xs">● Ready</span>
        )}
        {!isBuilding && recipe.running && !recipe.ready && (
          <span className="text-amber-400 text-xs animate-pulse">● Starting...</span>
        )}
        {!isBuilding && !recipe.running && recipe.installed && (
          <span className="text-text-dim text-xs">● Stopped</span>
        )}
      </div>

      <p className="text-[13px] text-text-muted leading-relaxed m-0 flex-1">
        {recipe.description}
      </p>

      <div className="flex flex-wrap gap-1">
        {recipe.tags.slice(0, 4).map((t) => (
          <span key={t} className="bg-white/[0.06] text-text-dim px-1.5 py-0.5 rounded text-[10px] font-mono">
            {t}
          </span>
        ))}
      </div>

      <div className="flex items-center justify-between text-[11px] text-text-dim border-t border-border pt-2.5">
        <div className="flex gap-3">
          <span>🧠 {recipe.requirements?.min_memory_gb ?? 8}GB</span>
          <span>💾 {recipe.requirements?.disk_gb ?? 10}GB</span>
        </div>
        <div className="flex gap-3 items-center">
          {officialUrl && (
            <a
              href={officialUrl}
              target="_blank"
              rel="noreferrer"
              onClick={(e) => e.stopPropagation()}
              className="text-text-dim hover:text-spark no-underline transition-colors"
            >
              Website ↗
            </a>
          )}
          {recipe.running && recipe.ready && (
            <a
              href={`http://${location.hostname}:${recipe.ui?.port ?? 8080}${recipe.ui?.path ?? '/'}`}
              target="_blank"
              rel="noreferrer"
              onClick={(e) => e.stopPropagation()}
              className="text-spark font-semibold no-underline hover:text-spark-light active:scale-95 transition-all"
            >
              Open UI ↗
            </a>
          )}
        </div>
      </div>
    </div>
  )
}
