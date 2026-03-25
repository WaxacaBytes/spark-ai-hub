import { useState, useMemo, useRef } from 'react'
import { useStore } from '../store'
import RecipeCard from '../components/RecipeCard'

// Map slugs to banner config: { img, layout }
// layout: 'wide' = 2:1 extended image (320px), 'full' = full-width top-aligned (360px, less gradient)
// Qwen capybaras scale with model size: beach → basketball → driving → coder
const BANNERS = {
  'vllm-qwen35-08b':      { img: '/banners/wide/qwen-beach.png', layout: 'wide' },
  'vllm-qwen35-2b':       { img: '/banners/wide/qwen-beach.png', layout: 'wide' },
  'vllm-qwen35-4b':       { img: '/banners/wide/qwen-basketball.png', layout: 'wide' },
  'vllm-qwen35-9b':       { img: '/banners/wide/qwen-driving.png', layout: 'wide' },
  'vllm-qwen3.5-27b':     { img: '/banners/wide/qwen-driving.png', layout: 'wide' },
  'vllm-qwen35-27b-int4': { img: '/banners/wide/qwen-driving.png', layout: 'wide' },
  'vllm-qwen35-35b-a3b':  { img: '/banners/wide/qwen-coder.png', layout: 'wide' },
  'vllm-qwen35-122b-a10b':{ img: '/banners/wide/qwen-coder.png', layout: 'wide' },
  'ollama-openwebui':      { img: '/banners/wide/ollama-openwebui.png', layout: 'wide' },
  'comfyui':               { img: '/banners/wide/comfyui-spark.jpg', layout: 'wide' },
  'facefusion':            { img: '/banners/wide/facefusion-spark.png', layout: 'wide' },
  'hunyuan3d':             { img: '/banners/wide/hunyuan3d-spark.png', layout: 'wide' },
  'trellis2':              { img: '/banners/wide/trellis2-spark.png', layout: 'wide' },
  'anythingllm':           { img: '/banners/wide/anythingllm.png', layout: 'wide' },
  'flowise':               { img: '/banners/wide/flowise.png', layout: 'wide' },
  'langflow':              { img: '/banners/wide/langflow.png', layout: 'wide' },
  'localai':               { img: '/banners/wide/localai.png', layout: 'wide' },
}

function getBanner(slug) {
  // Try exact match first, then prefix match
  if (BANNERS[slug]) return BANNERS[slug]
  for (const [prefix, conf] of Object.entries(BANNERS)) {
    if (slug.startsWith(prefix)) return conf
  }
  return null
}

const CATEGORIES = [
  { id: 'all', label: 'All' },
  { id: 'llm', label: 'LLMs', icon: '💬' },
  { id: 'image-gen', label: 'Image Gen', icon: '🎨' },
  { id: 'video-gen', label: 'Video Gen', icon: '🎬' },
  { id: '3d-gen', label: '3D Gen', icon: '🧊' },
  { id: 'multi-modal', label: 'Multi-Modal', icon: '🤖' },
]

const SOURCE_SECTIONS = [
  { id: 'sparkdeck', label: 'Spark-Optimized', subtitle: 'Built and tested for DGX Spark' },
  { id: 'official', label: 'Official Images', subtitle: 'Published by the original developers' },
  { id: 'community', label: 'Community', subtitle: 'Contributed by the community' },
]

export default function Catalog({ search = '' }) {
  const recipes = useStore((s) => s.recipes)
  const selectRecipe = useStore((s) => s.selectRecipe)
  const [category, setCategory] = useState('all')

  // Pick a random hero index once per mount
  const heroIndex = useRef(Math.floor(Math.random() * 1000))

  const filtered = recipes.filter((r) => {
    const recipeCategories = Array.isArray(r.categories) && r.categories.length > 0
      ? r.categories
      : [r.category]
    if (category !== 'all' && !recipeCategories.includes(category)) return false
    if (search) {
      const q = search.toLowerCase()
      if (!r.name.toLowerCase().includes(q) && !r.tags.some((t) => t.includes(q))) return false
    }
    return true
  })

  const grouped = useMemo(() => {
    return SOURCE_SECTIONS.map((section) => ({
      ...section,
      recipes: filtered.filter((r) => (r.source || 'community') === section.id),
    })).filter((section) => section.recipes.length > 0)
  }, [filtered])

  // Hero: random app that HAS a banner, stable per page load
  const recipesWithBanners = recipes.filter((r) => getBanner(r.slug))
  const featured = recipesWithBanners.length > 0
    ? recipesWithBanners[heroIndex.current % recipesWithBanners.length]
    : null
  const bannerConf = featured ? getBanner(featured.slug) : null

  // 'wide' layout: 320px, uses 2:1 extended image, strong left gradient
  // 'full' layout: 360px, uses original image with object-position top, softer gradient
  const isWide = bannerConf?.layout === 'wide'
  const heroHeight = isWide ? 'h-[320px]' : 'h-[360px]'
  const heroImgClass = isWide
    ? 'absolute inset-0 w-full h-full object-cover transition-transform duration-700 group-hover:scale-105'
    : 'absolute inset-0 w-full h-full object-cover object-top transition-transform duration-700 group-hover:scale-105'
  const heroGradientStyle = { background: 'var(--hero-overlay-left)' }
  const heroBottomStyle = { background: 'var(--hero-overlay-bottom)' }

  return (
    <div className="pb-24">
      {/* ─── Hero ─── */}
      {featured && bannerConf && !search && category === 'all' && (
        <div
          className={`mx-6 mt-6 rounded-3xl overflow-hidden cursor-pointer relative group ${heroHeight}`}
          onClick={() => selectRecipe(featured.slug)}
        >
          {/* Banner image */}
          <img
            src={bannerConf.img}
            alt=""
            className={heroImgClass}
          />
          {/* Gradient overlay */}
          <div className="absolute inset-0" style={heroGradientStyle} />
          <div className="absolute inset-0" style={heroBottomStyle} />

          {/* Content */}
          <div className="relative h-full flex items-center gap-6 px-10">
            {/* App icon */}
            {featured.logo ? (
              <img
                src={featured.logo}
                alt={featured.name}
                className="w-24 h-24 rounded-2xl object-contain bg-surface/80 backdrop-blur-md p-3 shadow-2xl shrink-0 border border-glass-border"
              />
            ) : (
              <div className="w-24 h-24 rounded-2xl bg-surface/80 backdrop-blur-md flex items-center justify-center text-5xl shrink-0 border border-glass-border">
                {featured.icon || '◻'}
              </div>
            )}

            <div className="flex-1 min-w-0">
              <span className="text-[11px] font-bold text-primary uppercase tracking-widest drop-shadow-sm">
                {featured.running ? 'Now Running' : 'Featured'}
              </span>
              <h1 className="text-4xl font-extrabold text-text mt-1 mb-2 tracking-tight font-[Manrope] m-0 drop-shadow-md">
                {featured.name}
              </h1>
              <p className="text-text-muted text-sm leading-relaxed max-w-lg line-clamp-2 m-0 drop-shadow-sm">
                {featured.description}
              </p>
              <div className="mt-5 flex items-center gap-3">
                {featured.running && featured.ready ? (
                  <a
                    href={`http://${location.hostname}:${featured.ui?.port ?? 8080}${featured.ui?.path ?? '/'}`}
                    target="_blank"
                    rel="noreferrer"
                    onClick={(e) => e.stopPropagation()}
                    className="btn-primary px-7 py-2.5 text-sm font-bold no-underline inline-block shadow-lg shadow-primary/25"
                  >
                    Open ↗
                  </a>
                ) : (
                  <button
                    onClick={(e) => { e.stopPropagation(); selectRecipe(featured.slug) }}
                    className="btn-primary px-7 py-2.5 text-sm font-bold shadow-lg shadow-primary/25"
                  >
                    View Details
                  </button>
                )}
                <span className="text-xs text-text-dim drop-shadow-sm">{featured.author}</span>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ─── Categories ─── */}
      <div className="px-6 pt-6 pb-2 flex gap-2 overflow-x-auto">
        {CATEGORIES.map((c) => (
          <button
            key={c.id}
            onClick={() => setCategory(c.id)}
            className={`shrink-0 px-4 py-2 rounded-xl text-sm font-semibold cursor-pointer transition-all duration-200 border-none ${
              category === c.id
                ? 'bg-primary text-primary-on shadow-md shadow-primary/20'
                : 'bg-surface-high text-text-muted hover:text-text hover:bg-surface-highest'
            }`}
          >
            {c.icon && <span className="mr-1">{c.icon}</span>}
            {c.label}
          </button>
        ))}
      </div>

      {/* ─── App List ─── */}
      <div className="px-6 pt-4 space-y-10">
        {grouped.map((section) => (
          <div key={section.id} className="animate-fadeIn">
            <div className="mb-4">
              <h2 className="text-xl font-bold text-text tracking-tight font-[Manrope] m-0">
                {section.label}
              </h2>
              <p className="text-sm text-text-dim mt-1 m-0">{section.subtitle}</p>
            </div>
            <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(360px, 1fr))' }}>
              {section.recipes.map((r) => (
                <RecipeCard key={r.slug} recipe={r} />
              ))}
            </div>
          </div>
        ))}
        {grouped.length === 0 && (
          <div className="text-center py-20 text-text-dim animate-fadeIn">
            <div className="text-5xl mb-4">🔍</div>
            <div className="text-lg font-semibold font-[Manrope]">No apps found</div>
            <div className="text-sm mt-1">Try a different search or category</div>
          </div>
        )}
      </div>
    </div>
  )
}
