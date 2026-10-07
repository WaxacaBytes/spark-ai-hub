import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useStore } from '../store'
import RecipeCard from '../components/RecipeCard'
import PosterCard from '../components/PosterCard'
import CardRow from '../components/CardRow'
import { byLab, byRelease } from '../components/ModelShelves'
import { isModel } from '../models'
import Hero from '../components/Hero'

const CATEGORIES = [
  { id: 'all', label: 'All' },
  { id: 'llm', label: 'LLM Apps' },
  { id: 'image-gen', label: 'Image Gen' },
  { id: 'video-gen', label: 'Video Gen' },
  { id: '3d-gen', label: '3D Gen' },
  { id: 'multi-modal', label: 'Multi-Modal' },
]

// The Store is apps. Every model -- the LLMs agents run on and the media
// models they call as tools -- lives on the Agents page.
function getSectionId(recipe) {
  if ((recipe.source || 'community') === 'spark-ai-hub') return 'spark-ai-hub'
  return 'official'
}

export default function Catalog({ search = '' }) {
  const recipes = useStore((s) => s.recipes)
  const [category, setCategory] = useState('all')

  const matches = useMemo(() => (r) => {
    const recipeCategories = Array.isArray(r.categories) && r.categories.length > 0
      ? r.categories
      : [r.category]
    if (category !== 'all' && !recipeCategories.includes(category)) return false
    if (search) {
      const q = search.toLowerCase()
      if (!r.name.toLowerCase().includes(q) && !r.tags.some((t) => t.includes(q))) return false
    }
    return true
  }, [category, search])

  const filtered = useMemo(
    () => recipes.filter((r) => !isModel(r) && matches(r)),
    [recipes, matches],
  )
  // A search for "qwen" here should not come back empty just because the
  // Qwen models moved: say how many are waiting on the Agents page. The search
  // box is shared, so following the link keeps the query.
  const agentHits = useMemo(
    () => (search ? recipes.filter((r) => isModel(r) && matches(r)).length : 0),
    [recipes, matches, search],
  )

  const shelves = useMemo(() => {
    const pick = (id) => filtered.filter((r) => getSectionId(r) === id)
    return {
      spark: pick('spark-ai-hub').sort(byRelease),
      official: pick('official').sort(byRelease),
    }
  }, [filtered])

  // Hero picks: whatever is running, then the freshest Spark-optimized apps,
  // capped at five so the dots stay meaningful.
  const heroPicks = useMemo(() => {
    const seen = new Set()
    const out = []
    const push = (r) => {
      if (r && !seen.has(r.slug)) { seen.add(r.slug); out.push(r) }
    }
    recipes.filter((r) => r.running || r.starting).sort(byRelease).forEach(push)
    recipes.filter((r) => !isModel(r) && getSectionId(r) === 'spark-ai-hub').sort(byRelease).forEach(push)
    return out.slice(0, 5)
  }, [recipes])

  const isBrowsing = !search && category === 'all'

  return (
    <div className="pb-14">
      {isBrowsing && heroPicks.length > 0 && <Hero picks={heroPicks} />}

      {/* ─── Category filters ─── */}
      <div className={`flex gap-2 overflow-x-auto px-6 pb-2 ${isBrowsing ? 'pt-2' : 'pt-6'}`}>
        {CATEGORIES.map((c) => (
          <button
            key={c.id}
            onClick={() => setCategory(c.id)}
            className={`shrink-0 cursor-pointer rounded-full border px-4 py-2 text-sm font-medium transition-all duration-200 ${
              category === c.id
                ? 'border-primary bg-primary text-primary-on shadow-md shadow-primary/15'
                : 'border-outline bg-transparent text-text-muted hover:border-text-dim hover:text-text'
            }`}
          >
            {c.label}
          </button>
        ))}
      </div>

      {agentHits > 0 && (
        <div className="px-6 pt-3">
          <Link
            to="/agents"
            className="inline-flex items-center gap-2 rounded-xl border border-primary/30 bg-primary/10 px-3.5 py-2 text-xs font-semibold text-primary no-underline transition-colors hover:bg-primary/15"
          >
            {agentHits} matching model{agentHits > 1 ? 's' : ''} for agents
            <span aria-hidden>→</span>
          </Link>
        </div>
      )}

      {/* Search and category filters collapse the shelves into a plain grid —
          scanning results sideways is worse than scanning them down. */}
      {!isBrowsing ? (
        <ResultsGrid recipes={filtered} search={search} />
      ) : (
        <div className="space-y-9 pt-4">
          {shelves.spark.length > 0 && (
            <CardRow title="Spark-Optimized" subtitle="Built & tested for DGX Spark" wrap>
              {shelves.spark.map((r) => <PosterCard key={r.slug} recipe={r} />)}
            </CardRow>
          )}

          {shelves.official.length > 0 && (
            <CardRow title="Official Apps" subtitle="Published by the original developers" wrap>
              {shelves.official.map((r) => <PosterCard key={r.slug} recipe={r} />)}
            </CardRow>
          )}

          {shelves.spark.length === 0 && shelves.official.length === 0 && <Empty />}
        </div>
      )}
    </div>
  )
}

function ResultsGrid({ recipes, search }) {
  const sorted = useMemo(() => [...recipes].sort(byLab), [recipes])
  if (sorted.length === 0) return <Empty />
  return (
    <div className="px-6 pt-4">
      <h2 className="m-0 mb-4 font-display text-base font-bold tracking-tight text-text">
        {sorted.length} {sorted.length === 1 ? 'result' : 'results'}
        {search && <span className="text-text-dim font-normal"> for “{search}”</span>}
      </h2>
      <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))' }}>
        {sorted.map((r) => <RecipeCard key={r.slug} recipe={r} />)}
      </div>
    </div>
  )
}

function Empty() {
  return (
    <div className="animate-fadeIn py-20 text-center text-text-dim">
      <div className="mb-3 text-4xl">🔍</div>
      <div className="font-display text-base font-semibold">No apps found</div>
      <div className="mt-1 text-sm">Try a different search or category</div>
    </div>
  )
}
