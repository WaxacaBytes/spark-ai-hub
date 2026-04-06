import { useEffect, useState } from 'react'
import { useStore } from './store'
import { useMetrics } from './hooks/useMetrics'
import ThemeToggle from './components/ThemeToggle'
import Catalog from './pages/Catalog'
import Running from './pages/Running'
import System from './pages/System'
import RecipeDetail from './pages/RecipeDetail'

const NAV_ITEMS = [
  { id: 'catalog', label: 'Store', icon: StorefrontIcon },
  { id: 'running', label: 'Running', icon: PlayIcon },
  { id: 'system', label: 'System', icon: GaugeIcon },
]

export default function App() {
  const [tab, setTab] = useState('catalog')
  const recipes = useStore((s) => s.recipes)
  const fetchRecipes = useStore((s) => s.fetchRecipes)
  const selectedRecipe = useStore((s) => s.selectedRecipe)
  const clearRecipe = useStore((s) => s.clearRecipe)
  const theme = useStore((s) => s.theme)
  const metrics = useStore((s) => s.metrics)
  const [search, setSearch] = useState('')

  useMetrics()

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
  }, [theme])

  useEffect(() => {
    fetchRecipes()
    const interval = setInterval(fetchRecipes, 5000)
    return () => clearInterval(interval)
  }, [fetchRecipes])

  const runningCount = recipes.filter((r) => r.running || r.starting).length

  return (
    <div className="bg-bg text-text flex h-screen overflow-hidden transition-colors duration-300">
      {/* ─── Sidebar ─── */}
      <aside className="w-[72px] shrink-0 bg-sidebar-bg flex flex-col items-center py-4 border-r border-outline-dim">
        {/* Logo */}
        <button
          onClick={() => { clearRecipe(); setTab('catalog') }}
          className="w-11 h-11 rounded-2xl bg-gradient-to-br from-[#152608] to-[#0A1404] flex items-center justify-center border-none cursor-pointer shadow-lg shadow-primary/20 hover:shadow-primary/40 transition-shadow mb-6 p-1.5"
          title="Spark AI Hub"
        >
          <img src="/brand/spark-ai-hub-mark.svg" alt="Spark AI Hub" className="w-full h-full" />
        </button>

        {/* Nav */}
        <nav className="flex flex-col gap-1 flex-1">
          {NAV_ITEMS.map((item) => {
            const isActive = tab === item.id && !selectedRecipe
            const Icon = item.icon
            return (
              <button
                key={item.id}
                onClick={() => { clearRecipe(); setTab(item.id) }}
                title={item.label}
                className={`relative w-11 h-11 rounded-xl flex items-center justify-center border-none cursor-pointer sidebar-link ${
                  isActive ? 'active' : 'bg-transparent text-text-dim hover:text-text'
                }`}
              >
                <Icon className="w-5 h-5" />
                {item.id === 'running' && runningCount > 0 && (
                  <span className="absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] bg-primary text-primary-on text-[10px] font-bold font-label rounded-full flex items-center justify-center px-1">
                    {runningCount}
                  </span>
                )}
              </button>
            )
          })}
        </nav>

        {/* Bottom: System gauges + Theme toggle */}
        <div className="flex flex-col items-center gap-3 mt-auto">
          {metrics && (
            <div className="flex flex-col items-center gap-1.5">
              <MiniGauge value={metrics.gpu_utilization} label="GPU" color="var(--tertiary)" />
              <MiniGauge value={metrics.ram_total_gb > 0 ? Math.round((metrics.ram_used_gb / metrics.ram_total_gb) * 100) : 0} label="RAM" color="var(--tertiary)" />
            </div>
          )}
          <ThemeToggle />
        </div>
      </aside>

      {/* ─── Main ─── */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Top Bar */}
        <header className="shrink-0 flex items-center justify-between px-6 py-3 bg-surface-low/60 backdrop-blur-md border-b border-outline-dim">
          <div className="flex items-center gap-3">
            <span className="text-lg font-bold tracking-tight font-display">Spark AI Hub</span>
            <span className="text-[10px] text-text-dim font-medium font-label bg-surface-high px-2 py-0.5 rounded-md">v0.1</span>
          </div>

          {!selectedRecipe && (
            <div className="relative">
              <svg className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-text-dim" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="11" cy="11" r="8" />
                <line x1="21" y1="21" x2="16.65" y2="16.65" />
              </svg>
              <input
                type="text"
                placeholder="Search apps..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-72 py-2 pl-10 pr-4 bg-surface-high rounded-xl text-text text-sm outline-none border border-outline-dim focus:border-primary/40 focus:ring-2 focus:ring-primary/10 placeholder:text-text-dim transition-all"
              />
            </div>
          )}

          {selectedRecipe && (
            <div className="text-sm text-text-dim font-label">
              {metrics?.gpu_name || 'NVIDIA GB10'}
            </div>
          )}
        </header>

        {/* Content */}
        <main className="flex-1 overflow-y-auto">
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
    </div>
  )
}

/* ─── Mini Gauge for sidebar ─── */
function MiniGauge({ value, label, color }) {
  const radius = 14
  const stroke = 3
  const circ = 2 * Math.PI * radius
  const offset = circ - (value / 100) * circ

  return (
    <div className="flex flex-col items-center" title={`${label}: ${value}%`}>
      <svg width="36" height="36" viewBox="0 0 36 36">
        <circle cx="18" cy="18" r={radius} fill="none" stroke="var(--outline-dim)" strokeWidth={stroke} />
        <circle
          cx="18" cy="18" r={radius} fill="none"
          stroke={color} strokeWidth={stroke}
          strokeDasharray={circ} strokeDashoffset={offset}
          strokeLinecap="round"
          transform="rotate(-90 18 18)"
          className="gauge-ring"
        />
        <text x="18" y="19" textAnchor="middle" dominantBaseline="middle" fill="var(--text-muted)" fontSize="8" fontFamily="Space Grotesk" fontWeight="600">
          {value}%
        </text>
      </svg>
      <span className="text-[9px] text-text-dim font-label mt-0.5">{label}</span>
    </div>
  )
}

/* ─── Icons ─── */
function StorefrontIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
      <rect x="14" y="14" width="7" height="7" rx="1.5" />
    </svg>
  )
}

function PlayIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="5 3 19 12 5 21 5 3" />
    </svg>
  )
}

function GaugeIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20z" />
      <path d="M12 6v6l4 2" />
    </svg>
  )
}
