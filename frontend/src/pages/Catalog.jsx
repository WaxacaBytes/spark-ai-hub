import { useState } from 'react'
import { useStore } from '../store'
import RecipeCard from '../components/RecipeCard'

const CATEGORIES = [
  { id: 'all', label: 'All', icon: '◆' },
  { id: 'llm', label: 'LLMs', icon: '💬' },
  { id: 'image-gen', label: 'Image Gen', icon: '🎨' },
  { id: 'video-gen', label: 'Video Gen', icon: '🎬' },
  { id: '3d-gen', label: '3D Gen', icon: '🧊' },
  { id: 'multi-modal', label: 'Multi-Modal', icon: '🤖' },
]

export default function Catalog() {
  const recipes = useStore((s) => s.recipes)
  const [category, setCategory] = useState('all')
  const [search, setSearch] = useState('')

  const filtered = recipes.filter((r) => {
    if (category !== 'all' && r.category !== category) return false
    if (search) {
      const q = search.toLowerCase()
      if (!r.name.toLowerCase().includes(q) && !r.tags.some((t) => t.includes(q))) return false
    }
    return true
  })

  return (
    <div>
      <div className="px-6 py-4 flex gap-3 items-center flex-wrap">
        <div className="relative flex-1 max-w-sm">
          <span className="absolute left-3 top-1/2 -translate-y-1/2 text-text-dim text-sm">⌕</span>
          <input
            type="text"
            placeholder="Search recipes, tags..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full py-2 pl-8 pr-3 bg-surface border border-border rounded-lg text-text text-[13px] outline-none focus:border-border-hover"
          />
        </div>
        <div className="flex gap-1">
          {CATEGORIES.map((c) => (
            <button
              key={c.id}
              onClick={() => setCategory(c.id)}
              className={`px-3 py-1.5 rounded-md text-xs font-medium cursor-pointer transition-all border ${
                category === c.id
                  ? 'bg-spark/10 text-spark border-spark/25'
                  : 'bg-surface text-text-muted border-border hover:bg-surface-hover'
              }`}
            >
              {c.icon} {c.label}
            </button>
          ))}
        </div>
      </div>

      <div className="px-6 pb-60 grid gap-4" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))' }}>
        {filtered.map((r) => (
          <RecipeCard key={r.slug} recipe={r} />
        ))}
        {filtered.length === 0 && (
          <div className="col-span-full text-center py-16 text-text-dim">
            <div className="text-4xl mb-3">🔍</div>
            <div className="text-[15px]">No recipes match your filters</div>
          </div>
        )}
      </div>
    </div>
  )
}
