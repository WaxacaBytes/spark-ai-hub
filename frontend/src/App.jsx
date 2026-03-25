import { useEffect, useState } from 'react'
import { useStore } from './store'
import { useMetrics } from './hooks/useMetrics'
import SystemBar from './components/SystemBar'
import ThemeToggle from './components/ThemeToggle'
import Catalog from './pages/Catalog'
import Running from './pages/Running'
import System from './pages/System'
import RecipeDetail from './pages/RecipeDetail'

const TABS = [
  { id: 'catalog', label: 'Catalog', icon: '◆' },
  { id: 'running', label: 'Running', icon: '▶' },
  { id: 'system', label: 'System', icon: '⚙' },
]

export default function App() {
  const [tab, setTab] = useState('catalog')
  const recipes = useStore((s) => s.recipes)
  const fetchRecipes = useStore((s) => s.fetchRecipes)
  const selectedRecipe = useStore((s) => s.selectedRecipe)
  const clearRecipe = useStore((s) => s.clearRecipe)
  const theme = useStore((s) => s.theme)
  const [search, setSearch] = useState('')

  useMetrics()

  // Apply theme on mount
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
  }, [theme])

  useEffect(() => {
    fetchRecipes()
    const interval = setInterval(fetchRecipes, 5000)
    return () => clearInterval(interval)
  }, [fetchRecipes])

  const runningCount = recipes.filter((r) => r.running).length

  const counts = {
    catalog: recipes.length,
    running: runningCount,
    system: null,
  }

  return (
    <div className={`bg-bg text-text flex flex-col transition-colors duration-300 ${selectedRecipe ? 'h-screen overflow-hidden' : 'min-h-screen'}`}>
      {/* ─── Header ─── */}
      <header className="flex items-center justify-between px-6 py-3 bg-surface-low/80 backdrop-blur-xl sticky top-0 z-50">
        {/* Logo */}
        <div
          className="flex items-center gap-3 cursor-pointer group"
          onClick={() => { clearRecipe(); setTab('catalog') }}
        >
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-primary to-primary-container flex items-center justify-center text-primary-on font-extrabold text-base shadow-lg shadow-primary/20 group-hover:shadow-primary/40 transition-shadow">
            ⚡
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-lg font-bold tracking-tight font-[Manrope]">SparkDeck</span>
            <span className="text-[10px] text-text-dim font-medium bg-surface-high px-1.5 py-0.5 rounded-md">v0.1</span>
          </div>
        </div>

        {/* Center: Search + Nav */}
        <div className="flex items-center gap-4">
          {/* Search */}
          {!selectedRecipe && (
            <div className="relative">
              <svg className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-dim" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="11" cy="11" r="8" />
                <line x1="21" y1="21" x2="16.65" y2="16.65" />
              </svg>
              <input
                type="text"
                placeholder="Search apps..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-64 py-2 pl-9 pr-4 glass rounded-full text-text text-sm outline-none focus:ring-2 focus:ring-primary/30 placeholder:text-text-dim transition-all"
              />
            </div>
          )}

          {/* Nav Tabs */}
          <div className="flex gap-1 bg-surface-high rounded-xl p-1">
            {TABS.map((t) => (
              <button
                key={t.id}
                onClick={() => { clearRecipe(); setTab(t.id) }}
                className={`px-4 py-2 rounded-lg border-none text-sm font-semibold cursor-pointer transition-all duration-200 ${
                  tab === t.id && !selectedRecipe
                    ? 'bg-primary text-primary-on shadow-md shadow-primary/20'
                    : 'bg-transparent text-text-muted hover:text-text hover:bg-surface-highest'
                }`}
              >
                {t.label}
                {counts[t.id] != null && (
                  <span className={`text-xs ml-1.5 ${
                    tab === t.id && !selectedRecipe ? 'opacity-80' : 'opacity-50'
                  }`}>
                    {counts[t.id]}
                  </span>
                )}
              </button>
            ))}
          </div>
        </div>

        {/* Right: Theme Toggle */}
        <div className="flex items-center gap-3">
          <SystemBar />
          <ThemeToggle />
        </div>
      </header>

      {/* ─── Content ─── */}
      <main className="flex-1 flex flex-col min-h-0">
        {selectedRecipe ? (
          <RecipeDetail />
        ) : (
          <div className="animate-fadeIn">
            {tab === 'catalog' && <Catalog search={search} />}
            {tab === 'running' && <Running />}
            {tab === 'system' && <System />}
          </div>
        )}
      </main>
    </div>
  )
}
