import { useEffect, useState } from 'react'
import { useStore } from '../store'
import { buildLabel, displayName, formatDuration, isAgentModel, isLora, mediaKind, publishers } from '../models'
import FavStar from './FavStar'
import KindTag from './KindTag'
import RecipeLogo from './RecipeLogo'

// The models an admin starred, as big tiles: what each is, its one figure,
// and whether it can start right now -- so "which one should I use?" is
// answered before anyone reads a table. Starred apps keep the Store's
// poster wherever they appear, so an app always looks like an app.
export default function FavoriteTiles({ recipes }) {
  return (
    <div className="grid w-full gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(min(100%, 260px), 1fr))' }}>
      {[...recipes].sort(byIndexThenParams).map((r) => <Tile key={r.slug} recipe={r} />)}
    </div>
  )
}

// Highest Artificial Analysis Intelligence Index first; models without a
// published score follow, largest first.
function byIndexThenParams(a, b) {
  const ai = a.artificial_analysis_index
  const bi = b.artificial_analysis_index
  if ((ai != null) !== (bi != null)) return ai != null ? -1 : 1
  return (bi ?? 0) - (ai ?? 0) || (b.params_b ?? 0) - (a.params_b ?? 0) || a.name.localeCompare(b.name)
}

function Tile({ recipe }) {
  const selectRecipe = useStore((s) => s.selectRecipe)
  const sub = [buildLabel(recipe), recipe.engine, publishers(recipe)].filter(Boolean).join(' · ')

  return (
    <div
      onClick={() => selectRecipe(recipe.slug)}
      className="flex min-w-0 cursor-pointer flex-col gap-3 rounded-2xl border border-fav/30 bg-surface p-4 transition-colors hover:border-fav/60"
    >
      <div className="flex min-w-0 items-start gap-3">
        <RecipeLogo recipe={recipe} />
        <div className="min-w-0 flex-1">
          <div className="font-display text-[15px] font-bold leading-snug text-text">
            {displayName(recipe)}
          </div>
          <div className="mt-1 flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1 font-label text-[11px] text-text-dim">
            <KindTag recipe={recipe} specific />
            <span className="min-w-0">{sub}</span>
          </div>
        </div>
        <FavStar recipe={recipe} className="text-[16px]" />
      </div>
      <Figure recipe={recipe} />
      <div className="mt-auto flex flex-wrap items-center justify-between gap-2">
        <Action recipe={recipe} />
      </div>
    </div>
  )
}

function Figure({ recipe }) {
  if (isAgentModel(recipe) && recipe.tokens_per_second != null) {
    return (
      <div className="font-label text-[12px] tabular-nums text-text-muted">
        <b className="font-display text-[18px] font-extrabold text-primary">{recipe.tokens_per_second}</b> writing
        {recipe.tokens_per_second_editing != null && recipe.tokens_per_second_editing !== recipe.tokens_per_second && (
          <> · <b className="font-display text-[18px] font-extrabold text-primary">{recipe.tokens_per_second_editing}</b> editing</>
        )} tok/s
      </div>
    )
  }
  if (recipe.render_seconds != null) {
    return (
      <div className="font-label text-[12px] tabular-nums text-text-muted">
        <b className="font-display text-[18px] font-extrabold text-tertiary">{formatDuration(recipe.render_seconds)}</b>
        {mediaKind(recipe) && ` per ${mediaKind(recipe)}`}
      </div>
    )
  }
  return null
}

// Whether it can start now. A language model that does not fit names what
// would stop for it (the launch plan); anything else says what it needs.
function useFit(recipe) {
  const metrics = useStore((s) => s.metrics)
  const recipes = useStore((s) => s.recipes)
  const [plan, setPlan] = useState({ key: null, stop: null })
  const free = metrics?.ram_total_gb ? Math.max(0, metrics.ram_total_gb - metrics.ram_used_gb) : null
  const fits = free == null || (recipe.memory_gb || 0) <= free
  const up = recipes.filter((r) => r.running || r.starting).map((r) => r.slug).join()
  // Asked again whenever what runs changes; an answer for another state is stale.
  const key = !fits && recipe.installed && isAgentModel(recipe) ? `${recipe.slug}|${up}` : null

  useEffect(() => {
    if (!key) return undefined
    let live = true
    fetch(`/api/recipes/${key.split('|')[0]}/launch-plan`)
      .then((res) => (res.ok ? res.json() : null))
      .then((answer) => { if (live) setPlan({ key, stop: answer?.stop || null }) })
      .catch(() => {})
    return () => { live = false }
  }, [key])
  const stops = plan.key === key ? plan.stop : null

  if (fits) return { ok: true, text: '✓ Fits now' }
  if (stops?.length) {
    const names = stops.map((s) => recipes.find((r) => r.slug === s)).filter(Boolean).map(displayName)
    return { ok: false, text: names.length === 1 ? `Stops ${names[0]}` : `Stops ${names.length} models` }
  }
  return { ok: false, text: `Needs ${Math.round(recipe.memory_gb)} GB · ${Math.round(free)} free` }
}

function Action({ recipe }) {
  const installing = useStore((s) => s.installing)
  const updating = useStore((s) => s.updating)
  const installRecipe = useStore((s) => s.installRecipe)
  const requestLaunch = useStore((s) => s.requestLaunch)
  const fit = useFit(recipe)
  const stop = (e) => e.stopPropagation()

  if (installing[recipe.slug] || updating[recipe.slug]) {
    return <span className="font-label text-[12px] font-semibold text-secondary">Building…</span>
  }
  if (recipe.running && recipe.ready) {
    return (
      <span className="flex items-center gap-1.5 font-label text-[12px] font-bold text-primary">
        <span className="h-1.5 w-1.5 rounded-full bg-primary" />Running
      </span>
    )
  }
  if (isLora(recipe) && recipe.installed) {
    // Nothing of a LoRA runs: a render that asks for it loads it into its model.
    return <span className="font-label text-[12px] text-text-dim">Installed · loads into its model</span>
  }
  if (recipe.running || recipe.starting) {
    return <span className="font-label text-[12px] font-semibold text-warning">Starting…</span>
  }
  if (!recipe.installed) {
    return (
      <>
        {recipe.error ? (
          <span className="font-label text-[12px] font-bold text-error">Install failed</span>
        ) : (
          <span className="font-label text-[12px] text-text-dim">
            {recipe.weights_gb != null ? `${recipe.weights_gb} GB download` : 'Not installed'}
          </span>
        )}
        <button type="button" onClick={(e) => { stop(e); installRecipe(recipe.slug) }}
          className="cursor-pointer rounded-xl border border-outline bg-transparent px-3.5 py-1.5 font-label text-[12px] font-bold text-text-muted hover:border-primary hover:text-primary">
          Install
        </button>
      </>
    )
  }
  return (
    <>
      <span className={`font-label text-[12px] font-semibold ${fit.ok ? 'text-success' : 'text-warning'}`}>{fit.text}</span>
      <button type="button" onClick={(e) => { stop(e); requestLaunch(recipe.slug) }}
        className="btn-primary px-3.5 py-1.5 text-[12px] font-bold">
        Launch
      </button>
    </>
  )
}
