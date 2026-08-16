import { useMemo, useState } from 'react'
import { useStore } from '../store'
import RecipeCard from '../components/RecipeCard'
import PosterCard from '../components/PosterCard'
import CardRow from '../components/CardRow'
import ModelList, { ModelBlock } from '../components/ModelList'
import Hero from '../components/Hero'
import { vendorKey, vendorLabel } from '../covers'
import { groupModels } from '../models'

const CATEGORIES = [
  { id: 'all', label: 'All' },
  { id: 'llm', label: 'LLMs' },
  { id: 'image-gen', label: 'Image Gen' },
  { id: 'video-gen', label: 'Video Gen' },
  { id: '3d-gen', label: '3D Gen' },
  { id: 'multi-modal', label: 'Multi-Modal' },
]

// Every sort mode draws shelves. "AI Lab" (the default) shelves by vendor;
// the rest rank all models against each other and shelve them into bands of
// the sorted value, so the rows themselves read top-to-bottom in rank order.
// `key` returns a number to sort descending — null/undefined always sinks.

// Builds a band labeller from descending [floor, label] pairs.
function bands(defs, unknown) {
  return (value) => {
    if (value == null) return unknown
    for (const [floor, label] of defs) {
      if (value >= floor) return label
    }
    return defs[defs.length - 1][1]
  }
}

// Quarters for the current year, then whole years — month-by-month shelves
// would leave a dozen rows holding two tiles each.
function releaseBand(value) {
  if (!value) return 'Undated'
  const [year, month] = value.split('-')
  if (year !== String(new Date().getFullYear())) return year
  return `Q${Math.floor((Number(month) - 1) / 3) + 1} ${year}`
}

const MODEL_SORTS = [
  { id: 'lab', label: 'AI Lab', key: null },
  {
    id: 'release',
    label: 'Newest',
    key: null,
    band: (r) => releaseBand(r.release_date),
  },
  {
    id: 'params',
    label: 'Params',
    key: (r) => r.params_b,
    band: (r) => bands([[100, '100B+'], [30, '30–100B'], [10, '10–30B'], [0, 'Under 10B']],
      'Size unlisted')(r.params_b),
  },
  {
    id: 'size',
    label: 'Size on disk',
    key: (r) => r.weights_gb,
    band: (r) => bands([[80, '80 GB+'], [50, '50–80 GB'], [20, '20–50 GB'], [0, 'Under 20 GB']],
      'Unmeasured')(r.weights_gb),
  },
  {
    id: 'speed',
    label: 'Speed',
    key: (r) => r.tokens_per_second,
    band: (r) => bands([[100, '100+ tok/s'], [50, '50–100 tok/s'], [25, '25–50 tok/s'],
      [10, '10–25 tok/s'], [0, 'Under 10 tok/s']], 'Not benchmarked')(r.tokens_per_second),
  },
]

function byRelease(a, b) {
  const dateOrder = (b.release_date || '').localeCompare(a.release_date || '')
  if (dateOrder !== 0) return dateOrder
  return (a.name || '').localeCompare(b.name || '')
}

// Used where the lab shelves cannot be drawn (the search/filter results grid).
function byLab(a, b) {
  return vendorLabel(vendorKey(a)).localeCompare(vendorLabel(vendorKey(b)))
    || byRelease(a, b)
}

function makeComparator(sort) {
  if (sort?.id === 'lab') return byLab
  if (!sort?.key) return byRelease
  return (a, b) => {
    const av = sort.key(a)
    const bv = sort.key(b)
    if (av == null && bv == null) return byRelease(a, b)
    if (av == null) return 1
    if (bv == null) return -1
    return bv - av || byRelease(a, b)
  }
}

function isModel(recipe) {
  return recipe.slug.startsWith('vllm-')
    || recipe.slug.startsWith('sglang-')
    || recipe.slug.startsWith('llamacpp-')
    || recipe.slug.startsWith('atlas-')
}

function getSectionId(recipe) {
  if ((recipe.source || 'community') === 'spark-ai-hub') return 'spark-ai-hub'
  if (isModel(recipe)) return 'models'
  return 'official'
}

export default function Catalog({ search = '' }) {
  const recipes = useStore((s) => s.recipes)
  const openConnect = useStore((s) => s.openConnect)
  const [category, setCategory] = useState('all')
  const [modelSortId, setModelSortId] = useState('lab')

  const filtered = useMemo(() => recipes.filter((r) => {
    const recipeCategories = Array.isArray(r.categories) && r.categories.length > 0
      ? r.categories
      : [r.category]
    if (category !== 'all' && !recipeCategories.includes(category)) return false
    if (search) {
      const q = search.toLowerCase()
      if (!r.name.toLowerCase().includes(q) && !r.tags.some((t) => t.includes(q))) return false
    }
    return true
  }), [recipes, category, search])

  const modelSort = MODEL_SORTS.find((s) => s.id === modelSortId) || MODEL_SORTS[0]
  const comparator = useMemo(() => makeComparator(modelSort), [modelSort])
  const groupByLab = modelSort.id === 'lab'
  // Which build of a model leads its row. A ranking sort answers that itself;
  // "AI Lab" and "Newest" do not, and there the fastest build leads — that is
  // the one you would have picked anyway.
  const buildComparator = useMemo(
    () => (modelSort.key ? comparator : makeComparator(MODEL_SORTS.find((s) => s.id === 'speed'))),
    [modelSort, comparator],
  )

  const shelves = useMemo(() => {
    const pick = (id) => filtered.filter((r) => getSectionId(r) === id)

    // "Jump back in" collects anything already on disk, running first. Model
    // builds and apps split: a shelf of installed models was the worst case
    // for poster art in the whole UI — three of its twelve tiles were the same
    // capybara, differing only by the engine and quantization a poster cannot
    // show — while installed apps do have art of their own worth showing.
    const active = filtered
      .filter((r) => r.running || r.starting || r.installed)
      .sort((a, b) => Number(b.running || b.starting) - Number(a.running || a.starting)
        || byRelease(a, b))
    const activeModels = active.filter(isModel)
    const activeApps = active.filter((r) => !isModel(r))

    // Models are collapsed to one entry per model — Qwen3.6-27B is a single
    // choice with seven builds under it, not seven entries competing for the
    // same slot in your attention. A group ranks and bands by its best build.
    const groups = groupModels(pick('models'), comparator, buildComparator)

    // On the "AI Lab" sort, models are split per vendor. Every other sort
    // ranks the whole catalog in one shelf instead — ordering inside a lab
    // answers the wrong question when you asked for the fastest model.
    const byVendor = new Map()
    for (const g of groups) {
      const key = vendorKey(g.lead)
      if (!byVendor.has(key)) byVendor.set(key, [])
      byVendor.get(key).push(g)
    }
    const vendorShelves = [...byVendor.entries()]
      .map(([key, items]) => ({ key, items: [...items].sort((a, b) => byRelease(a.lead, b.lead)) }))
      .sort((a, b) => b.items.length - a.items.length || a.key.localeCompare(b.key))

    // Ranked modes band individual *builds*, not models. Banding a model by
    // its best build put four sub-40 tok/s rows under a "100+ tok/s" heading;
    // a band heading has to be true of every row beneath it. The catalog is
    // already in rank order and the bands are monotonic, so first-seen order
    // gives the shelves their ranking for free (and sinks unknowns last).
    const bandShelves = []
    if (modelSort.band) {
      const byBand = new Map()
      for (const r of [...pick('models')].sort(comparator)) {
        const key = modelSort.band(r)
        if (!byBand.has(key)) {
          byBand.set(key, { key, items: [] })
          bandShelves.push(byBand.get(key))
        }
        byBand.get(key).items.push(r)
      }
    }

    return {
      activeModels,
      activeApps,
      spark: pick('spark-ai-hub').sort(byRelease),
      official: pick('official').sort(byRelease),
      vendorShelves,
      bandShelves,
    }
  }, [filtered, comparator, buildComparator, modelSort])

  // Hero picks: whatever is running, then the freshest Spark-optimized apps
  // and models, capped at five so the dots stay meaningful.
  const heroPicks = useMemo(() => {
    const seen = new Set()
    const out = []
    const push = (r) => {
      if (r && !seen.has(r.slug)) { seen.add(r.slug); out.push(r) }
    }
    recipes.filter((r) => r.running || r.starting).sort(byRelease).forEach(push)
    recipes.filter((r) => getSectionId(r) === 'spark-ai-hub').sort(byRelease).slice(0, 3).forEach(push)
    recipes.filter(isModel).sort(byRelease).slice(0, 3).forEach(push)
    return out.slice(0, 5)
  }, [recipes])

  const isBrowsing = !search && category === 'all'

  const sortControl = (
    <div className="flex items-center gap-1 rounded-xl border border-outline-dim bg-surface-high p-0.5">
      <span className="px-2 font-label text-[10px] text-text-dim">Sort</span>
      {MODEL_SORTS.map((s) => (
        <button
          key={s.id}
          onClick={() => setModelSortId(s.id)}
          className={`cursor-pointer rounded-lg px-2.5 py-1 text-[11px] font-semibold transition-all ${
            modelSortId === s.id ? 'bg-primary text-primary-on' : 'bg-transparent text-text-muted hover:text-text'
          }`}
        >
          {s.label}
        </button>
      ))}
    </div>
  )

  const connectButton = (
    <button
      onClick={openConnect}
      title="Connect a coding agent to the served model"
      className="flex cursor-pointer items-center gap-2.5 rounded-xl border border-outline-dim bg-surface-high py-1.5 pl-2.5 pr-3.5 text-xs font-semibold text-text-muted transition-all hover:border-text-dim hover:text-text"
    >
      <span className="flex -space-x-1.5">
        <AgentChip><ClaudeMark className="h-3 w-3 text-[#D97757]" /></AgentChip>
        <AgentChip><img src="/logos/openai.png" alt="Codex" className="h-3.5 w-3.5 object-contain" /></AgentChip>
        <AgentChip><img src="/logos/qwen.png" alt="Qwen Code" className="h-3.5 w-3.5 object-contain" /></AgentChip>
        <AgentChip><TerminalMark className="h-3 w-3 text-text-muted" /></AgentChip>
      </span>
      Connect to a coding agent
    </button>
  )

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

      {/* Search and category filters collapse the shelves into a plain grid —
          scanning results sideways is worse than scanning them down. */}
      {!isBrowsing ? (
        <ResultsGrid
          recipes={filtered}
          comparator={comparator}
          highlight={groupByLab ? null : modelSort.id}
          sortControl={sortControl}
          search={search}
        />
      ) : (
        <div className="space-y-9 pt-4">
          {shelves.activeModels.length > 0 && (
            <CardRow
              title="Jump back in"
              subtitle="Installed on this Spark · one model serves port 9001 at a time"
              wrap
            >
              <ModelList items={shelves.activeModels} variant="installed" />
            </CardRow>
          )}

          {shelves.activeApps.length > 0 && (
            <CardRow
              title={shelves.activeModels.length > 0 ? 'Installed apps' : 'Jump back in'}
              subtitle="Installed on this Spark"
              wrap
            >
              {shelves.activeApps.map((r) => <PosterCard key={r.slug} recipe={r} />)}
            </CardRow>
          )}

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

          {shelves.vendorShelves.length > 0 && (
            <div className="space-y-7">
              <div className="flex flex-wrap items-end gap-x-3 gap-y-2 px-6">
                <div>
                  <h2 className="m-0 font-display text-xl font-bold tracking-tight text-text">
                    Ready-to-Serve Models
                  </h2>
                  <p className="m-0 mt-0.5 text-xs text-text-dim">
                    Curated for DGX Spark. Served on port 9001, one at a time.
                  </p>
                </div>
                <div className="ml-auto flex flex-wrap items-center gap-2">
                  {sortControl}
                  {connectButton}
                </div>
              </div>

              {/* Two shapes for two questions. Browsing by lab, you are
                  choosing a model, so builds sit grouped under theirs. On a
                  ranked sort you are comparing builds, so they are one flat
                  list in rank order. Either way every build is on screen. */}
              {groupByLab
                ? shelves.vendorShelves.map(({ key, items }) => (
                    <CardRow key={key} title={vendorLabel(key)} subtitle={shelfSubtitle(items)} wrap>
                      {/* Columns, not a grid: the blocks are different heights
                          because models have different numbers of builds, and
                          a grid reserves the tallest block's height for its
                          whole row — leaving holes the size of a block. */}
                      <div className="w-full" style={{ columnWidth: '470px', columnGap: '12px' }}>
                        {items.map((g) => (
                          <div key={g.key} className="mb-3 break-inside-avoid">
                            <ModelBlock group={g} />
                          </div>
                        ))}
                      </div>
                    </CardRow>
                  ))
                : shelves.bandShelves.map(({ key, items }) => (
                    <CardRow
                      key={key}
                      title={key}
                      subtitle={`${items.length} build${items.length > 1 ? 's' : ''}`}
                      wrap
                    >
                      <div className="w-full">
                        <ModelList items={items} variant="ranked" highlight={modelSort.id} />
                      </div>
                    </CardRow>
                  ))}
            </div>
          )}

          {shelves.activeModels.length === 0 && shelves.activeApps.length === 0
            && shelves.spark.length === 0 && shelves.official.length === 0
            && shelves.vendorShelves.length === 0 && <Empty />}
        </div>
      )}
    </div>
  )
}

// "6 models · 19 builds" — the second number is the one the old shelves showed,
// and on its own it overstated how many things there are to choose between.
function shelfSubtitle(groups) {
  const builds = groups.reduce((n, g) => n + g.items.length, 0)
  const models = `${groups.length} model${groups.length > 1 ? 's' : ''}`
  return builds === groups.length ? models : `${models} · ${builds} builds`
}

function ResultsGrid({ recipes, comparator, highlight, sortControl, search }) {
  const sorted = useMemo(() => [...recipes].sort(comparator), [recipes, comparator])
  if (sorted.length === 0) return <Empty />
  return (
    <div className="px-6 pt-4">
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <h2 className="m-0 font-display text-base font-bold tracking-tight text-text">
          {sorted.length} {sorted.length === 1 ? 'result' : 'results'}
          {search && <span className="text-text-dim font-normal"> for “{search}”</span>}
        </h2>
        <div className="ml-auto">{sortControl}</div>
      </div>
      <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))' }}>
        {sorted.map((r) => <RecipeCard key={r.slug} recipe={r} highlight={highlight} />)}
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

function AgentChip({ children }) {
  return (
    <span className="flex h-5 w-5 items-center justify-center overflow-hidden rounded-full bg-white ring-1 ring-outline-dim">
      {children}
    </span>
  )
}

// Anthropic/Claude-style radial burst mark.
function ClaudeMark({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="currentColor" aria-label="Claude Code">
      <g transform="translate(12 12)">
        {[0, 45, 90, 135, 180, 225, 270, 315].map((deg) => (
          <rect key={deg} x="-1" y="-10" width="2" height="9" rx="1" transform={`rotate(${deg})`} />
        ))}
      </g>
    </svg>
  )
}

// Generic terminal/CLI glyph for the other coding agents.
function TerminalMark({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-label="CLI agents">
      <path d="M5 8l4 4-4 4" />
      <line x1="12" y1="16" x2="18" y2="16" />
    </svg>
  )
}
