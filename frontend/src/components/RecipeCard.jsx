import { useStore } from '../store'

const STATUS_STYLES = {
  official: 'bg-emerald-500/10 text-emerald-400',
  'community-verified': 'bg-amber-500/10 text-amber-400',
  experimental: 'bg-red-500/10 text-red-400',
}

const STATUS_LABELS = {
  official: 'Official',
  'community-verified': 'Verified',
  experimental: 'Experimental',
}

export default function RecipeCard({ recipe }) {
  const installing = useStore((s) => s.installing)
  const installRecipe = useStore((s) => s.installRecipe)
  const launchRecipe = useStore((s) => s.launchRecipe)
  const stopRecipe = useStore((s) => s.stopRecipe)

  const isInstalling = installing === recipe.slug
  const statusClass = STATUS_STYLES[recipe.status] || STATUS_STYLES.experimental
  const statusLabel = STATUS_LABELS[recipe.status] || 'Experimental'

  return (
    <div className="relative overflow-hidden bg-surface border border-border rounded-xl p-5 flex flex-col gap-3 hover:border-border-hover hover:bg-surface-hover transition-all">
      {recipe.running && (
        <div className="absolute top-0 left-0 right-0 h-0.5 bg-gradient-to-r from-spark to-lime-400" />
      )}

      <div className="flex justify-between items-start">
        <div className="flex items-center gap-2.5">
          <span className="text-2xl">{recipe.icon}</span>
          <div>
            <div className="font-bold text-[15px] text-text leading-tight">{recipe.name}</div>
            <div className="text-[11px] text-text-dim mt-0.5">by {recipe.author}</div>
          </div>
        </div>
        <span className={`px-2 py-0.5 rounded text-[11px] font-semibold uppercase tracking-wide ${statusClass}`}>
          {statusLabel}
        </span>
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

      <div className="flex gap-3 text-[11px] text-text-dim border-t border-border pt-2.5">
        <span>🧠 {recipe.requirements?.min_memory_gb ?? 8}GB RAM</span>
        <span>💾 {recipe.requirements?.disk_gb ?? 10}GB disk</span>
        <span>⏱ ~{recipe.docker?.build_time_minutes ?? 5}min</span>
      </div>

      <div className="flex gap-2">
        {!recipe.installed && !isInstalling && (
          <button
            onClick={() => installRecipe(recipe.slug)}
            className="flex-1 py-2 bg-gradient-to-br from-spark to-spark-dark text-white border-none rounded-lg text-[13px] font-semibold cursor-pointer hover:-translate-y-px transition-transform"
          >
            Install
          </button>
        )}
        {isInstalling && (
          <div className="flex-1 py-2 bg-spark/10 border border-spark/30 rounded-lg text-center text-[13px] text-spark">
            <span className="inline-block animate-spin">⟳</span> Building...
          </div>
        )}
        {recipe.installed && !recipe.running && !isInstalling && (
          <>
            <button
              onClick={() => launchRecipe(recipe.slug)}
              className="flex-1 py-2 bg-gradient-to-br from-spark to-spark-dark text-white border-none rounded-lg text-[13px] font-semibold cursor-pointer"
            >
              ▶ Launch
            </button>
          </>
        )}
        {recipe.running && (
          <>
            <a
              href={`http://${location.hostname}:${recipe.ui?.port ?? 8080}${recipe.ui?.path ?? '/'}`}
              target="_blank"
              rel="noreferrer"
              className="flex-1 py-2 bg-spark/10 border border-spark/30 rounded-lg text-center text-[13px] text-spark font-semibold no-underline cursor-pointer"
            >
              Open UI ↗ :{recipe.ui?.port ?? 8080}
            </a>
            <button
              onClick={() => stopRecipe(recipe.slug)}
              className="px-3.5 py-2 bg-red-500/10 text-red-400 border border-red-500/20 rounded-lg text-[13px] cursor-pointer"
            >
              ■ Stop
            </button>
          </>
        )}
      </div>
    </div>
  )
}
