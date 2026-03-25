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
  const [activeTab, setActiveTab] = useState('about')

  const isBuilding = installing === recipe?.slug

  // Connect logs when running
  useEffect(() => {
    if (recipe?.running) {
      connectLogs(recipe.slug)
    } else {
      disconnectLogs()
    }
    return () => disconnectLogs()
  }, [recipe?.running, recipe?.slug, connectLogs, disconnectLogs])

  // Auto-switch to terminal when building or running
  useEffect(() => {
    if (isBuilding || recipe?.running) setActiveTab('terminal')
  }, [isBuilding, recipe?.running])

  if (!recipe) {
    return (
      <div className="p-8 animate-fadeIn">
        <button onClick={clearRecipe} className="text-primary bg-transparent border-none cursor-pointer text-sm font-semibold">
          ← Back
        </button>
        <p className="text-text-muted mt-4">Recipe not found.</p>
      </div>
    )
  }

  const isRemoving = removing === recipe.slug
  const logoUrl = recipe.logo || ''
  const isReady = recipe.ready
  const cLogs = containerLogs[recipe.slug] || []
  const logLines = isBuilding ? (buildLogs[recipe.slug] || []) : cLogs

  const handleRemove = () => {
    if (window.confirm(`Uninstall ${recipe.name}? This removes containers, images, and volumes.`)) {
      removeRecipe(recipe.slug)
    }
  }
  const handleLaunch = async () => { setLaunching(true); await launchRecipe(recipe.slug); setLaunching(false) }
  const handleStop = async () => { setStopping(true); await stopRecipe(recipe.slug); setStopping(false) }

  return (
    <div className="flex flex-col flex-1 min-h-0 animate-fadeIn">

      {/* ═══ App Header ═══ */}
      <div className="shrink-0 px-6 py-5 bg-surface-low/60 backdrop-blur-md">
        {/* Back */}
        <button
          onClick={clearRecipe}
          className="flex items-center gap-1.5 text-text-muted hover:text-primary bg-transparent border-none cursor-pointer text-sm p-0 mb-4 transition-colors font-medium"
        >
          <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="15 18 9 12 15 6" />
          </svg>
          Back
        </button>

        <div className="flex items-center gap-5">
          {/* Icon */}
          {logoUrl && !logoFailed ? (
            <img
              src={logoUrl}
              alt={recipe.name}
              className="w-20 h-20 rounded-2xl object-contain bg-surface-high p-2.5 shadow-lg shadow-primary/10 shrink-0"
              onError={() => setLogoFailed(true)}
            />
          ) : (
            <div className="w-20 h-20 rounded-2xl bg-surface-high flex items-center justify-center text-4xl shrink-0">
              {recipe.icon || '◻'}
            </div>
          )}

          {/* Title + meta */}
          <div className="flex-1 min-w-0">
            <h1 className="text-2xl font-extrabold text-text tracking-tight m-0 font-[Manrope]">{recipe.name}</h1>
            <p className="text-sm text-text-dim mt-0.5 m-0">{recipe.author}</p>
            <div className="flex items-center gap-2 mt-2">
              {/* Status */}
              {isBuilding && <StatusPill color="primary" pulse>Building...</StatusPill>}
              {!isBuilding && recipe.running && isReady && <StatusPill color="success">Running</StatusPill>}
              {!isBuilding && recipe.running && !isReady && <StatusPill color="warning" pulse>Starting...</StatusPill>}
              {!isBuilding && !recipe.running && recipe.installed && <StatusPill color="dim">Stopped</StatusPill>}
              {/* Specs */}
              <span className="text-xs text-text-dim">
                {recipe.requirements?.min_memory_gb ?? 8}GB RAM · {recipe.requirements?.disk_gb ?? 10}GB Disk
              </span>
            </div>
          </div>

          {/* Actions */}
          <div className="shrink-0 flex items-center gap-2">
            {!recipe.installed && !isBuilding && (
              <button onClick={() => installRecipe(recipe.slug)} className="btn-primary px-6 py-2.5 text-sm font-bold">
                Install
              </button>
            )}
            {isBuilding && (
              <div className="px-6 py-2.5 bg-primary/10 rounded-xl text-sm text-primary font-semibold">
                <span className="inline-block animate-spin mr-1">⟳</span>Building...
              </div>
            )}
            {recipe.installed && !recipe.running && !isBuilding && (
              <>
                <button disabled={launching || isRemoving} onClick={handleLaunch} className="btn-primary px-6 py-2.5 text-sm font-bold">
                  {launching ? '⟳ Launching...' : '▶ Launch'}
                </button>
                <button disabled={isRemoving} onClick={handleRemove} className="px-4 py-2.5 bg-error-surface text-error border-none rounded-xl text-sm font-semibold cursor-pointer transition-all disabled:opacity-50">
                  {isRemoving ? 'Removing...' : 'Uninstall'}
                </button>
              </>
            )}
            {recipe.running && (
              <>
                {isReady && (
                  <a
                    href={`http://${location.hostname}:${recipe.ui?.port ?? 8080}${recipe.ui?.path ?? '/'}`}
                    target="_blank" rel="noreferrer"
                    className="btn-primary px-6 py-2.5 text-sm font-bold no-underline inline-block"
                  >
                    Open ↗
                  </a>
                )}
                <button disabled={stopping} onClick={handleStop} className="px-4 py-2.5 bg-surface-high text-text-muted border-none rounded-xl text-sm font-semibold cursor-pointer transition-all disabled:opacity-50">
                  {stopping ? 'Stopping...' : '■ Stop'}
                </button>
                <button disabled={isRemoving || stopping} onClick={handleRemove} className="px-4 py-2.5 bg-error-surface text-error border-none rounded-xl text-sm font-semibold cursor-pointer transition-all disabled:opacity-50">
                  Uninstall
                </button>
              </>
            )}
          </div>
        </div>

        {/* ─── Tabs ─── */}
        <div className="flex gap-1 mt-5 bg-surface-high rounded-xl p-1 w-fit">
          {['about', 'terminal'].map((t) => (
            <button
              key={t}
              onClick={() => setActiveTab(t)}
              className={`px-5 py-2 rounded-lg border-none text-sm font-semibold cursor-pointer transition-all duration-200 capitalize ${
                activeTab === t
                  ? 'bg-primary text-primary-on shadow-sm'
                  : 'bg-transparent text-text-muted hover:text-text'
              }`}
            >
              {t === 'terminal' ? 'Terminal' : 'About'}
              {t === 'terminal' && (isBuilding || recipe.running) && (
                <span className="w-1.5 h-1.5 rounded-full bg-primary inline-block ml-2 animate-pulse" />
              )}
            </button>
          ))}
        </div>
      </div>

      {/* ═══ Tab Content ═══ */}
      <div className="flex-1 min-h-0">
        {activeTab === 'about' ? (
          <AboutTab recipe={recipe} purging={purging} purgeRecipe={purgeRecipe} isBuilding={isBuilding} />
        ) : (
          <TerminalTab
            lines={logLines}
            isBuilding={isBuilding}
            isRunning={recipe.running}
            isReady={isReady}
            hasLogs={cLogs.length > 0}
            scrollRef={scrollRef}
          />
        )}
      </div>
    </div>
  )
}

/* ─── Status Pill ─── */
function StatusPill({ color, pulse, children }) {
  const colorMap = {
    primary: 'bg-primary/10 text-primary',
    success: 'bg-success/10 text-success',
    warning: 'bg-warning/10 text-warning',
    dim: 'bg-surface-highest text-text-dim',
  }
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-medium px-2.5 py-1 rounded-lg ${colorMap[color]} ${pulse ? 'animate-pulse' : ''}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${
        color === 'primary' ? 'bg-primary' : color === 'success' ? 'bg-success' : color === 'warning' ? 'bg-warning' : 'bg-text-dim'
      }`} />
      {children}
    </span>
  )
}

/* ═══ About Tab ═══ */
function AboutTab({ recipe, purging, purgeRecipe, isBuilding }) {
  const officialUrl = recipe.website || ''
  const sourceUrl = recipe.upstream || recipe.fork || ''

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-3xl mx-auto px-6 py-8 space-y-8">
        {/* Description */}
        <section>
          <h2 className="text-base font-bold text-text font-[Manrope] m-0 mb-3">About</h2>
          <p className="text-sm text-text-muted leading-relaxed m-0">{recipe.description}</p>
        </section>

        {/* Tags */}
        {recipe.tags.length > 0 && (
          <section className="flex flex-wrap gap-2">
            {recipe.tags.map((t) => (
              <span key={t} className="bg-surface text-text-dim px-3 py-1.5 rounded-xl text-xs font-medium">
                {t}
              </span>
            ))}
          </section>
        )}

        {/* Specs */}
        <section>
          <h2 className="text-base font-bold text-text font-[Manrope] m-0 mb-3">Requirements</h2>
          <div className="grid grid-cols-3 gap-3">
            <SpecCard icon="🧠" value={`${recipe.requirements?.min_memory_gb ?? 8} GB`} label="RAM" />
            <SpecCard icon="💾" value={`${recipe.requirements?.disk_gb ?? 10} GB`} label="Disk" />
            <SpecCard icon="⏱" value={`~${recipe.docker?.build_time_minutes ?? 5} min`} label="Build" />
          </div>
        </section>

        {/* Links */}
        {(officialUrl || sourceUrl) && (
          <section>
            <h2 className="text-base font-bold text-text font-[Manrope] m-0 mb-3">Links</h2>
            <div className="flex gap-3">
              {officialUrl && (
                <a href={officialUrl} target="_blank" rel="noreferrer" className="flex items-center gap-2 bg-surface rounded-xl px-4 py-3 text-sm text-primary no-underline hover:bg-surface-high transition-colors font-medium">
                  <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>
                  Website
                </a>
              )}
              {sourceUrl && (
                <a href={sourceUrl} target="_blank" rel="noreferrer" className="flex items-center gap-2 bg-surface rounded-xl px-4 py-3 text-sm text-text-muted no-underline hover:bg-surface-high transition-colors font-medium">
                  <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"/></svg>
                  Source
                </a>
              )}
            </div>
          </section>
        )}

        {/* Integration */}
        {recipe.integration && (
          <section>
            <h2 className="text-base font-bold text-text font-[Manrope] m-0 mb-3">API Integration</h2>
            <div className="bg-surface rounded-2xl p-5 space-y-3">
              <Field label="API URL" value={recipe.integration.api_url.replace('<SPARK_IP>', location.hostname)} />
              <Field label="Model ID" value={recipe.integration.model_id} />
              <Field label="API Key" value={recipe.integration.api_key} />
              {recipe.integration.max_context && <Field label="Max Context" value={recipe.integration.max_context} />}
              {recipe.integration.max_output_tokens && <Field label="Max Output" value={recipe.integration.max_output_tokens} />}
              {recipe.integration.curl_example && (
                <div className="pt-2">
                  <span className="text-xs text-text-dim block mb-1.5">curl example</span>
                  <pre className="bg-surface-high rounded-xl p-3 text-[11px] text-text-muted font-mono overflow-x-auto whitespace-pre-wrap break-all m-0 leading-relaxed">
                    {recipe.integration.curl_example.replace(/<SPARK_IP>/g, location.hostname)}
                  </pre>
                </div>
              )}
            </div>
          </section>
        )}

        {/* Purge */}
        {!recipe.installed && !isBuilding && recipe.has_leftovers && (
          <button
            disabled={purging === recipe.slug}
            onClick={() => {
              if (window.confirm(`Wipe all data for ${recipe.name}?`)) purgeRecipe(recipe.slug)
            }}
            className="w-full py-3 bg-warning/10 text-warning border-none rounded-xl text-sm font-semibold cursor-pointer hover:bg-warning/15 transition-all disabled:opacity-50"
          >
            {purging === recipe.slug ? '⟳ Wiping...' : '🗑 Wipe cached data'}
          </button>
        )}
      </div>
    </div>
  )
}

/* ─── Spec Card ─── */
function SpecCard({ icon, value, label }) {
  return (
    <div className="bg-surface rounded-2xl p-4 text-center">
      <div className="text-xl mb-1">{icon}</div>
      <div className="text-base font-bold text-text font-[Manrope]">{value}</div>
      <div className="text-[11px] text-text-dim mt-0.5">{label}</div>
    </div>
  )
}

/* ─── Integration Field ─── */
function Field({ label, value }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    const text = String(value)
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1200) })
        .catch(() => fallbackCopy(text, setCopied))
    } else {
      fallbackCopy(text, setCopied)
    }
  }
  return (
    <div className="flex items-center justify-between gap-3">
      <div className="min-w-0">
        <span className="text-[11px] text-text-dim block">{label}</span>
        <code className="text-sm text-text-muted font-mono break-all">{String(value)}</code>
      </div>
      <button onClick={copy} className="shrink-0 p-2 bg-surface-high border-none rounded-lg cursor-pointer text-text-dim hover:text-primary transition-colors" title="Copy">
        {copied ? (
          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
        ) : (
          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
        )}
      </button>
    </div>
  )
}

function fallbackCopy(text, setCopied) {
  const ta = document.createElement('textarea')
  ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0'
  document.body.appendChild(ta); ta.select(); document.execCommand('copy'); document.body.removeChild(ta)
  setCopied(true); setTimeout(() => setCopied(false), 1200)
}

/* ═══ Terminal Tab ═══ */
function TerminalTab({ lines, isBuilding, isRunning, isReady, hasLogs, scrollRef }) {
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

  const showEmpty = !isBuilding && !isRunning && !hasLogs

  return (
    <div className="h-full flex flex-col bg-[#08080c] [data-theme='light']_&:bg-[#0f0f1a]">
      {/* Terminal header */}
      <div className="flex items-center justify-between px-5 py-2.5 bg-[#0c0c12] shrink-0">
        <div className="flex items-center gap-3">
          <div className="flex gap-1.5">
            <span className="w-3 h-3 rounded-full bg-[#ff5f57]/80" />
            <span className="w-3 h-3 rounded-full bg-[#febc2e]/80" />
            <span className="w-3 h-3 rounded-full bg-[#28c840]/80" />
          </div>
          <span className="text-[11px] text-gray-500 font-mono">
            {isBuilding ? 'build' : 'container'} — logs
          </span>
        </div>
        <div className="flex items-center gap-2">
          {isBuilding && <span className="text-[11px] text-primary animate-pulse font-mono">● building</span>}
          {!isBuilding && isRunning && isReady && <span className="text-[11px] text-emerald-400 font-mono">● running</span>}
          {!isBuilding && isRunning && !isReady && <span className="text-[11px] text-amber-400 animate-pulse font-mono">● starting</span>}
        </div>
      </div>

      {/* Terminal body — fills all remaining space */}
      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto p-5 font-mono text-[12px] leading-6 selection:bg-primary/30"
      >
        {showEmpty && (
          <div className="text-gray-600 italic">No logs. Launch the app to see output here.</div>
        )}
        {lines.map((l, i) => (
          <div
            key={i}
            className={
              l.includes('[error]') || l.includes('Error') || l.includes('FATAL') || l.includes('Traceback')
                ? 'text-red-400'
                : l.includes('successfully') || l.includes('Started') || l.includes('Running on')
                ? 'text-emerald-400'
                : 'text-gray-400'
            }
          >
            {l}
          </div>
        ))}
        {(isBuilding || (isRunning && !isReady)) && lines.length > 0 && (
          <span className="text-primary animate-pulse">▋</span>
        )}
      </div>
    </div>
  )
}
