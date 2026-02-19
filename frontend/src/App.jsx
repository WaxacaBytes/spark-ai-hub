import { useEffect, useState } from 'react'
import { useStore } from './store'
import { useMetrics } from './hooks/useMetrics'
import SystemBar from './components/SystemBar'
import BuildLog from './components/BuildLog'
import Catalog from './pages/Catalog'
import Running from './pages/Running'
import System from './pages/System'

const TABS = [
  { id: 'catalog', label: 'Catalog' },
  { id: 'running', label: 'Running' },
  { id: 'system', label: 'System' },
]

export default function App() {
  const [tab, setTab] = useState('catalog')
  const recipes = useStore((s) => s.recipes)
  const fetchRecipes = useStore((s) => s.fetchRecipes)

  useMetrics()

  useEffect(() => {
    fetchRecipes()
    const interval = setInterval(fetchRecipes, 5000)
    return () => clearInterval(interval)
  }, [fetchRecipes])

  const runningCount = recipes.filter((r) => r.running).length
  const installedCount = recipes.filter((r) => r.installed).length

  const counts = {
    catalog: recipes.length,
    running: runningCount,
    system: null,
  }

  return (
    <div className="min-h-screen bg-bg text-text">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-border">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-spark to-spark-dark flex items-center justify-center text-white font-extrabold text-base">
            ⚡
          </div>
          <div>
            <span className="text-lg font-bold tracking-tight">SparkForge</span>
            <span className="text-[11px] text-text-dim ml-2 font-medium">v0.1.0</span>
          </div>
        </div>
        <div className="flex gap-1 bg-surface rounded-lg p-0.5">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`px-3.5 py-1.5 rounded-md border-none text-[13px] font-semibold cursor-pointer transition-all ${
                tab === t.id
                  ? 'bg-spark/15 text-spark'
                  : 'bg-transparent text-text-muted hover:text-text'
              }`}
            >
              {t.label}
              {counts[t.id] != null && (
                <span className="text-[11px] opacity-70 ml-1">({counts[t.id]})</span>
              )}
            </button>
          ))}
        </div>
      </div>

      <SystemBar />

      {tab === 'catalog' && <Catalog />}
      {tab === 'running' && <Running />}
      {tab === 'system' && <System />}

      <BuildLog />
    </div>
  )
}
