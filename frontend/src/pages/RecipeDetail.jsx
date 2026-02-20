import { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'

export default function RecipeDetail() {
  const selectedRecipe = useStore((s) => s.selectedRecipe)
  const recipes = useStore((s) => s.recipes)
  const clearRecipe = useStore((s) => s.clearRecipe)
  const installing = useStore((s) => s.installing)
  const removing = useStore((s) => s.removing)
  const installRecipe = useStore((s) => s.installRecipe)
  const launchRecipe = useStore((s) => s.launchRecipe)
  const stopRecipe = useStore((s) => s.stopRecipe)
  const removeRecipe = useStore((s) => s.removeRecipe)
  const buildLogs = useStore((s) => s.buildLogs)
  const containerLogs = useStore((s) => s.containerLogs)
  const connectLogs = useStore((s) => s.connectLogs)
  const disconnectLogs = useStore((s) => s.disconnectLogs)

  const recipe = recipes.find((r) => r.slug === selectedRecipe)
  const scrollRef = useRef(null)
  const [logoFailed, setLogoFailed] = useState(false)
  const [launching, setLaunching] = useState(false)
  const [stopping, setStopping] = useState(false)

  // Connect logs when recipe is running
  useEffect(() => {
    if (recipe?.running) {
      connectLogs(recipe.slug)
    } else {
      disconnectLogs()
    }
    return () => disconnectLogs()
  }, [recipe?.running, recipe?.slug, connectLogs, disconnectLogs])

  if (!recipe) {
    return (
      <div className="p-6">
        <button onClick={clearRecipe} className="text-spark hover:text-spark-light bg-transparent border-none cursor-pointer text-sm">
          ← Back to Catalog
        </button>
        <p className="text-text-muted mt-4">Recipe not found.</p>
      </div>
    )
  }

  const isInstalling = installing === recipe.slug
  const isRemoving = removing === recipe.slug
  const officialUrl = recipe.website || ''
  const sourceUrl = recipe.upstream || recipe.fork || ''
  const logoUrl = recipe.logo || ''
  const isBuilding = isInstalling
  const isReady = recipe.ready

  const logLines = isBuilding
    ? (buildLogs[recipe.slug] || [])
    : (containerLogs[recipe.slug] || [])

  const handleRemove = () => {
    const ok = window.confirm(`Uninstall ${recipe.name}? This removes containers, images, and volumes.`)
    if (ok) removeRecipe(recipe.slug)
  }

  const handleLaunch = async () => {
    setLaunching(true)
    await launchRecipe(recipe.slug)
    setLaunching(false)
  }

  const handleStop = async () => {
    setStopping(true)
    await stopRecipe(recipe.slug)
    setStopping(false)
  }

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {/* Top bar */}
      <div className="flex items-center gap-4 px-6 py-3 border-b border-border shrink-0">
        <button
          onClick={clearRecipe}
          className="text-text-muted hover:text-spark bg-transparent border-none cursor-pointer text-sm p-0 active:scale-95 transition-all"
        >
          ←
        </button>
        <div className="flex items-center gap-2.5 flex-1 min-w-0">
          {logoUrl && !logoFailed ? (
            <img
              src={logoUrl}
              alt={`${recipe.name} logo`}
              className="w-7 h-7 rounded-md object-contain bg-white/95 p-0.5 shrink-0"
              onError={() => setLogoFailed(true)}
            />
          ) : (
            <span className="text-xl shrink-0">{recipe.icon || '◻'}</span>
          )}
          <span className="font-bold text-[15px] truncate">{recipe.name}</span>
          <span className="text-[11px] text-text-dim shrink-0">by {recipe.author}</span>
        </div>
      </div>

      {/* Main content: sidebar + logs */}
      <div className="flex flex-1 min-h-0">
        {/* Sidebar */}
        <div className="w-80 shrink-0 border-r border-border overflow-y-auto p-5 flex flex-col gap-4">
          <p className="text-[13px] text-text-muted leading-relaxed m-0">
            {recipe.description}
          </p>

          {(officialUrl || sourceUrl) && (
            <div className="flex flex-col gap-1.5 text-[12px]">
              {officialUrl && (
                <a href={officialUrl} target="_blank" rel="noreferrer" className="text-spark hover:text-spark-light no-underline">
                  Official Website ↗
                </a>
              )}
              {sourceUrl && (
                <a href={sourceUrl} target="_blank" rel="noreferrer" className="text-text-dim hover:text-text no-underline">
                  Source Code ↗
                </a>
              )}
            </div>
          )}

          <div className="flex flex-wrap gap-1.5">
            {recipe.tags.map((t) => (
              <span key={t} className="bg-white/[0.06] text-text-dim px-2 py-0.5 rounded text-[11px] font-mono">
                {t}
              </span>
            ))}
          </div>

          <div className="flex flex-col gap-1 text-[12px] text-text-dim border-t border-border pt-3">
            <span>🧠 {recipe.requirements?.min_memory_gb ?? 8}GB RAM required</span>
            <span>💾 {recipe.requirements?.disk_gb ?? 10}GB disk space</span>
            <span>⏱ ~{recipe.docker?.build_time_minutes ?? 5}min build time</span>
          </div>

          {/* Actions */}
          <div className="flex flex-col gap-2 border-t border-border pt-3">
            {!recipe.installed && !isBuilding && (
              <button
                onClick={() => installRecipe(recipe.slug)}
                className="w-full py-2.5 bg-gradient-to-br from-spark to-spark-dark text-white border-none rounded-lg text-[13px] font-semibold cursor-pointer hover:brightness-110 active:scale-[0.98] transition-all"
              >
                Install
              </button>
            )}
            {isBuilding && (
              <div className="w-full py-2.5 bg-spark/10 border border-spark/30 rounded-lg text-center text-[13px] text-spark">
                <span className="inline-block animate-spin">⟳</span> Building...
              </div>
            )}
            {recipe.installed && !recipe.running && !isBuilding && (
              <>
                <button
                  disabled={launching || isRemoving}
                  onClick={handleLaunch}
                  className="w-full py-2.5 bg-gradient-to-br from-spark to-spark-dark text-white border-none rounded-lg text-[13px] font-semibold cursor-pointer hover:brightness-110 active:scale-[0.98] transition-all disabled:opacity-50 disabled:cursor-not-allowed disabled:active:scale-100"
                >
                  {launching ? (
                    <><span className="inline-block animate-spin">⟳</span> Launching...</>
                  ) : '▶ Launch'}
                </button>
                <button
                  disabled={isRemoving || launching}
                  onClick={handleRemove}
                  className="w-full py-2 bg-red-500/10 text-red-400 border border-red-500/20 rounded-lg text-[13px] cursor-pointer hover:bg-red-500/15 active:scale-[0.98] transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {isRemoving ? (
                    <><span className="inline-block animate-spin">⟳</span> Removing...</>
                  ) : 'Uninstall'}
                </button>
              </>
            )}
            {recipe.running && (
              <>
                <a
                  href={isReady ? `http://${location.hostname}:${recipe.ui?.port ?? 8080}${recipe.ui?.path ?? '/'}` : undefined}
                  target="_blank"
                  rel="noreferrer"
                  onClick={(e) => { if (!isReady) e.preventDefault() }}
                  className={`w-full py-2.5 rounded-lg text-center text-[13px] font-semibold no-underline transition-all ${
                    isReady
                      ? 'bg-spark/10 border border-spark/30 text-spark cursor-pointer hover:bg-spark/20 active:scale-[0.98]'
                      : 'bg-white/[0.03] border border-white/[0.06] text-text-dim cursor-not-allowed'
                  }`}
                >
                  {isReady ? `Open UI ↗ :${recipe.ui?.port ?? 8080}` : 'Waiting for service...'}
                </a>
                <button
                  disabled={stopping || isRemoving}
                  onClick={handleStop}
                  className="w-full py-2 bg-red-500/10 text-red-400 border border-red-500/20 rounded-lg text-[13px] cursor-pointer hover:bg-red-500/15 active:scale-[0.98] transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {stopping ? (
                    <><span className="inline-block animate-spin">⟳</span> Stopping...</>
                  ) : '■ Stop'}
                </button>
                <button
                  disabled={isRemoving || stopping}
                  onClick={handleRemove}
                  className="w-full py-2 bg-red-500/10 text-red-400 border border-red-500/20 rounded-lg text-[13px] cursor-pointer hover:bg-red-500/15 active:scale-[0.98] transition-all disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {isRemoving ? (
                    <><span className="inline-block animate-spin">⟳</span> Removing...</>
                  ) : 'Uninstall'}
                </button>
              </>
            )}
          </div>
        </div>

        {/* Logs panel */}
        <LogPanel
          lines={logLines}
          isBuilding={isBuilding}
          isRunning={recipe.running}
          isReady={isReady}
          scrollRef={scrollRef}
        />
      </div>
    </div>
  )
}

function LogPanel({ lines, isBuilding, isRunning, isReady, scrollRef }) {
  const [autoScroll, setAutoScroll] = useState(true)

  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [lines.length, autoScroll, scrollRef])

  const handleScroll = () => {
    if (!scrollRef.current) return
    const { scrollTop, scrollHeight, clientHeight } = scrollRef.current
    setAutoScroll(scrollHeight - scrollTop - clientHeight < 40)
  }

  const label = isBuilding ? 'BUILD LOG' : 'CONTAINER LOG'
  const showEmpty = !isBuilding && !isRunning

  let statusIndicator = null
  if (isBuilding) {
    statusIndicator = <span className="text-spark text-xs"><span className="inline-block animate-spin mr-1">⟳</span>Building...</span>
  } else if (isRunning && isReady) {
    statusIndicator = <span className="text-emerald-400 text-xs">● Ready</span>
  } else if (isRunning) {
    statusIndicator = <span className="text-amber-400 text-xs animate-pulse">● Starting...</span>
  }

  return (
    <div className="flex-1 flex flex-col min-w-0 bg-[#0a0a0e]">
      <div className="flex justify-between items-center px-4 py-2 border-b border-white/[0.06] shrink-0">
        <span className="text-text-dim font-bold text-[11px] font-mono tracking-wider">{label}</span>
        {statusIndicator}
      </div>
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-auto p-4 font-mono text-[11px] leading-5"
      >
        {showEmpty && (
          <div className="text-text-dim italic">Container not running. Launch the recipe to see logs.</div>
        )}
        {lines.map((l, i) => (
          <div
            key={i}
            className={
              l.includes('[error]') || l.includes('Error') || l.includes('FATAL') || l.includes('Traceback')
                ? 'text-red-400'
                : l.includes('successfully') || l.includes('Started') || l.includes('Running on')
                ? 'text-emerald-400'
                : 'text-text-muted'
            }
          >
            {l}
          </div>
        ))}
        {(isBuilding || (isRunning && !isReady)) && lines.length > 0 && (
          <div className="text-spark animate-pulse mt-1">▋</div>
        )}
      </div>
    </div>
  )
}
