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
  const purging = useStore((s) => s.purging)
  const purgeRecipe = useStore((s) => s.purgeRecipe)
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

  const cLogs = containerLogs[recipe.slug] || []
  const logLines = isBuilding
    ? (buildLogs[recipe.slug] || [])
    : cLogs

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
          className="flex items-center gap-2 text-text-muted hover:text-spark bg-transparent border-none cursor-pointer text-sm p-0 active:scale-95 transition-all"
        >
          <span>←</span>
          <span className="font-bold text-[15px] truncate text-text">{recipe.name}</span>
        </button>
      </div>

      {/* Main content: sidebar + logs */}
      <div className="flex flex-1 min-h-0">
        {/* Sidebar */}
        <div className="w-80 shrink-0 border-r border-border overflow-y-auto p-5 flex flex-col gap-4">
          {/* Hero */}
          <div className="flex flex-col items-center gap-2 pb-4 border-b border-border">
            {logoUrl && !logoFailed ? (
              <img
                src={logoUrl}
                alt={`${recipe.name} logo`}
                className="w-16 h-16 rounded-xl object-contain bg-white/95 p-2"
                onError={() => setLogoFailed(true)}
              />
            ) : (
              <span className="text-4xl">{recipe.icon || '◻'}</span>
            )}
            <div className="text-lg font-bold text-text text-center leading-tight">{recipe.name}</div>
            <div className="text-xs text-text-dim">by {recipe.author}</div>
            {isBuilding && (
              <span className="text-spark text-xs animate-pulse">● Building...</span>
            )}
            {!isBuilding && recipe.running && isReady && (
              <span className="text-emerald-400 text-xs">● Ready</span>
            )}
            {!isBuilding && recipe.running && !isReady && (
              <span className="text-amber-400 text-xs animate-pulse">● Starting...</span>
            )}
            {!isBuilding && !recipe.running && recipe.installed && (
              <span className="text-text-dim text-xs">● Stopped</span>
            )}
          </div>

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

          {/* Integration info */}
          {recipe.integration && (
            <div className="flex flex-col gap-2 border-t border-border pt-3">
              <span className="text-text-dim font-bold text-[11px] font-mono tracking-wider">INTEGRATION</span>
              <div className="flex flex-col gap-1.5 text-[12px]">
                <IntegrationField label="API URL" value={recipe.integration.api_url.replace('<SPARK_IP>', location.hostname)} breakAll />
                <IntegrationField label="Model ID" value={recipe.integration.model_id} />
                <IntegrationField label="API Key" value={recipe.integration.api_key} />
                {recipe.integration.max_context && (
                  <IntegrationField label="Max Context" value={recipe.integration.max_context} />
                )}
                {recipe.integration.max_output_tokens && (
                  <IntegrationField label="Max Output Tokens" value={recipe.integration.max_output_tokens} />
                )}
                {recipe.integration.curl_example && (
                  <IntegrationField
                    label="Test with curl"
                    value={recipe.integration.curl_example.replace(/<SPARK_IP>/g, location.hostname)}
                    breakAll
                    small
                  />
                )}
              </div>
            </div>
          )}

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

          {/* Purge leftover data */}
          {!recipe.installed && !isBuilding && recipe.has_leftovers && (
            <button
              disabled={purging === recipe.slug}
              onClick={() => {
                const ok = window.confirm(`Wipe all data for ${recipe.name}? This removes cached models, Docker images, and volumes. A fresh install will re-download everything.`)
                if (ok) purgeRecipe(recipe.slug)
              }}
              className="w-full py-2 bg-amber-500/10 text-amber-400 border border-amber-500/20 rounded-lg text-[13px] cursor-pointer hover:bg-amber-500/15 active:scale-[0.98] transition-all disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {purging === recipe.slug ? (
                <><span className="inline-block animate-spin">⟳</span> Wiping...</>
              ) : '🗑 Wipe data'}
            </button>
          )}
        </div>

        {/* Logs panel */}
        <LogPanel
          lines={logLines}
          isBuilding={isBuilding}
          isRunning={recipe.running}
          isReady={isReady}
          hasLogs={cLogs.length > 0}
          scrollRef={scrollRef}
        />
      </div>
    </div>
  )
}

function fallbackCopy(text, setCopied) {
  const ta = document.createElement('textarea')
  ta.value = text
  ta.style.position = 'fixed'
  ta.style.opacity = '0'
  document.body.appendChild(ta)
  ta.select()
  document.execCommand('copy')
  document.body.removeChild(ta)
  setCopied(true)
  setTimeout(() => setCopied(false), 1500)
}

function IntegrationField({ label, value, breakAll, small }) {
  const [copied, setCopied] = useState(false)
  const handleCopy = () => {
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(value).then(() => {
        setCopied(true)
        setTimeout(() => setCopied(false), 1500)
      }).catch(() => fallbackCopy(value, setCopied))
    } else {
      fallbackCopy(value, setCopied)
    }
  }
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-text-dim">{label}</span>
      <div className="flex items-start gap-1">
        <code className={`flex-1 bg-white/[0.06] text-text-muted px-2 py-1 rounded font-mono select-all ${small ? 'text-[10px] leading-4' : 'text-[11px]'} ${breakAll ? 'break-all' : ''}`}>
          {value}
        </code>
        <button
          onClick={handleCopy}
          title="Copy to clipboard"
          className="shrink-0 mt-0.5 p-1 bg-transparent border-none cursor-pointer text-text-dim hover:text-spark transition-colors rounded hover:bg-white/[0.06]"
        >
          {copied ? (
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
          ) : (
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
          )}
        </button>
      </div>
    </div>
  )
}

function LogPanel({ lines, isBuilding, isRunning, isReady, hasLogs, scrollRef }) {
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
  const showEmpty = !isBuilding && !isRunning && !hasLogs

  let statusIndicator = null
  if (isBuilding) {
    statusIndicator = <span className="text-spark text-xs animate-pulse">● Building...</span>
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
