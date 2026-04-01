import { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'
import { useThemedLogo } from '../hooks/useThemedLogo'

export default function RecipeDetail() {
  const selectedRecipe = useStore((s) => s.selectedRecipe)
  const recipes = useStore((s) => s.recipes)
  const clearRecipe = useStore((s) => s.clearRecipe)
  const installing = useStore((s) => s.installing)
  const updating = useStore((s) => s.updating)
  const removing = useStore((s) => s.removing)
  const installRecipe = useStore((s) => s.installRecipe)
  const updateRecipe = useStore((s) => s.updateRecipe)
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

  const isBuilding = installing === recipe?.slug
  const isUpdating = updating === recipe?.slug
  const isBusy = isBuilding || isUpdating

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
      <div className="p-8 animate-fadeIn">
        <button onClick={clearRecipe} className="text-primary bg-transparent border-none cursor-pointer text-sm font-semibold font-display">
          ← Back
        </button>
        <p className="text-text-muted mt-4">Recipe not found.</p>
      </div>
    )
  }

  const isRemoving = removing === recipe.slug
  const logoUrl = useThemedLogo(recipe.logo)
  const isReady = recipe.ready
  const cLogs = containerLogs[recipe.slug] || []
  const logLines = isBusy ? (buildLogs[recipe.slug] || []) : cLogs

  const handleRemove = () => {
    if (window.confirm(`Uninstall ${recipe.name}? This removes containers, images, and volumes.`)) {
      removeRecipe(recipe.slug)
    }
  }
  const handleLaunch = async () => { setLaunching(true); await launchRecipe(recipe.slug); setLaunching(false) }
  const handleStop = async () => { setStopping(true); await stopRecipe(recipe.slug); setStopping(false) }

  const recipeCategories = Array.isArray(recipe.categories) && recipe.categories.length > 0
    ? recipe.categories
    : [recipe.category]

  return (
    <div className="flex flex-col h-full animate-fadeIn">
      {/* ═══ App Header ═══ */}
      <div className="shrink-0 px-6 py-5 bg-surface-low/60 backdrop-blur-md border-b border-outline-dim">
        <button
          onClick={clearRecipe}
          className="flex items-center gap-1.5 text-text-muted hover:text-primary bg-transparent border-none cursor-pointer text-sm p-0 mb-4 transition-colors font-medium font-display"
        >
          <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="15 18 9 12 15 6" />
          </svg>
          Back
        </button>

        <div className="flex items-center gap-5">
          {logoUrl && !logoFailed ? (
            <img src={logoUrl} alt={recipe.name} className="w-20 h-20 rounded-2xl object-contain bg-surface-high p-2.5 shadow-lg shrink-0" onError={() => setLogoFailed(true)} />
          ) : (
            <div className="w-20 h-20 rounded-2xl bg-surface-high flex items-center justify-center text-4xl shrink-0">{recipe.icon || '◻'}</div>
          )}

          <div className="flex-1 min-w-0">
            <h1 className="text-2xl font-bold text-text tracking-tight m-0 font-display">{recipe.name}</h1>
            <p className="text-sm text-text-dim mt-0.5 m-0">{recipe.author}</p>
            <div className="flex items-center gap-2 mt-2 flex-wrap">
              {recipeCategories.map((cat) => (
                <span key={cat} className="text-[10px] font-label text-secondary bg-secondary/10 px-2 py-0.5 rounded-full">{cat}</span>
              ))}
              {isBuilding && <StatusPill color="primary" pulse>Building...</StatusPill>}
              {isUpdating && <StatusPill color="primary" pulse>Updating...</StatusPill>}
              {!isBusy && recipe.running && isReady && <StatusPill color="success">Running</StatusPill>}
              {!isBusy && recipe.running && !isReady && <StatusPill color="warning" pulse>Starting...</StatusPill>}
              {!isBusy && !recipe.running && recipe.installed && <StatusPill color="dim">Stopped</StatusPill>}
            </div>
          </div>

          {/* Specs */}
          <div className="hidden md:flex items-center gap-2 shrink-0">
            <SpecBadge icon="🧠" value={`${recipe.requirements?.min_memory_gb ?? 8}–${recipe.requirements?.recommended_memory_gb ?? recipe.requirements?.min_memory_gb ?? 8} GB`} />
            <SpecBadge icon="💾" value={`${recipe.requirements?.disk_gb ?? 10} GB`} />
            <SpecBadge icon="⏱" value={`~${recipe.docker?.build_time_minutes ?? 5} min`} />
          </div>

          {/* Actions */}
          <div className="shrink-0 flex items-center gap-2">
            {!recipe.installed && !isBusy && (
              <button onClick={() => installRecipe(recipe.slug)} className="btn-primary px-6 py-2.5 text-sm font-bold">
                Install
              </button>
            )}
            {isBuilding && (
              <div className="px-5 py-2.5 bg-primary/10 rounded-xl text-sm text-primary font-semibold font-label">
                <span className="inline-block animate-spin mr-1">⟳</span>Building
              </div>
            )}
            {isUpdating && (
              <div className="px-5 py-2.5 bg-primary/10 rounded-xl text-sm text-primary font-semibold font-label">
                <span className="inline-block animate-spin mr-1">⟳</span>Updating
              </div>
            )}
            {recipe.installed && !recipe.running && !isBusy && (
              <>
                <button disabled={launching || isRemoving} onClick={handleLaunch} className="btn-primary px-6 py-2.5 text-sm font-bold">
                  {launching ? '...' : '▶ Launch'}
                </button>
                <button disabled={launching || isRemoving} onClick={() => updateRecipe(recipe.slug)} className="px-4 py-2.5 bg-surface-high text-text-muted border-none rounded-xl text-sm font-semibold cursor-pointer transition-all hover:text-primary hover:bg-surface-highest disabled:opacity-50">
                  ↻ Update
                </button>
                <button disabled={isRemoving} onClick={handleRemove} className="px-4 py-2.5 bg-error-surface text-error border-none rounded-xl text-sm font-semibold cursor-pointer transition-all disabled:opacity-50">
                  {isRemoving ? '...' : 'Uninstall'}
                </button>
              </>
            )}
            {recipe.running && (
              <>
                {isReady && (
                  <a href={`http://${location.hostname}:${recipe.ui?.port ?? 8080}${recipe.ui?.path ?? '/'}`} target="_blank" rel="noreferrer"
                    className="btn-primary px-6 py-2.5 text-sm font-bold no-underline inline-block">
                    Open ↗
                  </a>
                )}
                <button disabled={stopping} onClick={handleStop} className="px-4 py-2.5 bg-surface-high text-text-muted border-none rounded-xl text-sm font-semibold cursor-pointer disabled:opacity-50">
                  {stopping ? '...' : '■ Stop'}
                </button>
                <button disabled={isRemoving} onClick={handleRemove} className="px-4 py-2.5 bg-error-surface text-error border-none rounded-xl text-sm font-semibold cursor-pointer disabled:opacity-50">
                  Uninstall
                </button>
              </>
            )}
          </div>
        </div>
      </div>

      {/* ═══ Two-Column Content ═══ */}
      <div className="flex-1 min-h-0 flex">
        {/* Left: About */}
        <div className="flex-1 overflow-y-auto">
          <AboutTab recipe={recipe} purging={purging} purgeRecipe={purgeRecipe} isBuilding={isBusy} />
        </div>

        {/* Right: Terminal */}
        <div className="w-[420px] xl:w-[500px] shrink-0 border-l border-outline-dim flex flex-col">
          <TerminalPanel
            lines={logLines}
            isBuilding={isBusy}
            isUpdating={isUpdating}
            isRunning={recipe.running}
            isReady={isReady}
            hasLogs={cLogs.length > 0}
            scrollRef={scrollRef}
          />
        </div>
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
    <span className={`inline-flex items-center gap-1.5 text-[11px] font-medium font-label px-2.5 py-1 rounded-full ${colorMap[color]} ${pulse ? 'animate-pulse' : ''}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${
        color === 'primary' ? 'bg-primary' : color === 'success' ? 'bg-success' : color === 'warning' ? 'bg-warning' : 'bg-text-dim'
      }`} />
      {children}
    </span>
  )
}

function SpecBadge({ icon, value }) {
  return (
    <div className="bg-surface-high rounded-xl px-3 py-2 text-center">
      <div className="text-sm mb-0.5">{icon}</div>
      <div className="text-[11px] font-semibold text-text font-label">{value}</div>
    </div>
  )
}

/* ═══ About Tab ═══ */
function AboutTab({ recipe, purging, purgeRecipe, isBuilding }) {
  const officialUrl = recipe.website || ''
  const sourceUrl = recipe.upstream || recipe.fork || ''

  return (
    <div className="max-w-2xl px-6 py-6 space-y-6">
      <section>
        <h2 className="text-sm font-bold text-text font-display m-0 mb-2">About</h2>
        <p className="text-sm text-text-muted leading-relaxed m-0">{recipe.description}</p>
      </section>

      {recipe.tags.length > 0 && (
        <section className="flex flex-wrap gap-1.5">
          {recipe.tags.map((t) => (
            <span key={t} className="bg-surface-high text-text-dim px-2.5 py-1 rounded-full text-[11px] font-label">{t}</span>
          ))}
        </section>
      )}

      <section>
        <h2 className="text-sm font-bold text-text font-display m-0 mb-2">Requirements</h2>
        <div className="grid grid-cols-3 gap-2">
          <SpecCard icon="🧠" value={`${recipe.requirements?.min_memory_gb ?? 8} GB`} label="Min RAM" />
          <SpecCard icon="💾" value={`${recipe.requirements?.disk_gb ?? 10} GB`} label="Disk" />
          <SpecCard icon="⏱" value={`~${recipe.docker?.build_time_minutes ?? 5} min`} label="Build" />
        </div>
      </section>

      {(officialUrl || sourceUrl) && (
        <section>
          <h2 className="text-sm font-bold text-text font-display m-0 mb-2">Links</h2>
          <div className="flex gap-2">
            {officialUrl && (
              <a href={officialUrl} target="_blank" rel="noreferrer" className="flex items-center gap-2 bg-surface-high rounded-xl px-4 py-2.5 text-sm text-primary no-underline hover:bg-surface-highest transition-colors font-medium">
                <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/></svg>
                Website
              </a>
            )}
            {sourceUrl && (
              <a href={sourceUrl} target="_blank" rel="noreferrer" className="flex items-center gap-2 bg-surface-high rounded-xl px-4 py-2.5 text-sm text-text-muted no-underline hover:bg-surface-highest transition-colors font-medium">
                <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22"/></svg>
                Source
              </a>
            )}
          </div>
        </section>
      )}

      {recipe.integration && (
        <section>
          <h2 className="text-sm font-bold text-text font-display m-0 mb-2">API Integration</h2>
          <div className="bg-surface-high rounded-2xl p-4 space-y-2.5">
            <Field label="API URL" value={recipe.integration.api_url.replace('<SPARK_IP>', location.hostname)} />
            <Field label="Model ID" value={recipe.integration.model_id} />
            <Field label="API Key" value={recipe.integration.api_key} />
            {recipe.integration.max_context && <Field label="Max Context" value={recipe.integration.max_context} />}
            {recipe.integration.max_output_tokens && <Field label="Max Output" value={recipe.integration.max_output_tokens} />}
            {recipe.integration.curl_example && (
              <div className="pt-1">
                <span className="text-[10px] text-text-dim font-label block mb-1">curl example</span>
                <pre className="bg-surface rounded-xl p-3 text-[11px] text-text-muted font-mono overflow-x-auto whitespace-pre-wrap break-all m-0 leading-relaxed">
                  {recipe.integration.curl_example.replace(/<SPARK_IP>/g, location.hostname)}
                </pre>
              </div>
            )}
          </div>
        </section>
      )}

      <ComposeEditor slug={recipe.slug} />

      {!recipe.installed && !isBuilding && recipe.has_leftovers && (
        <button
          disabled={purging === recipe.slug}
          onClick={() => { if (window.confirm(`Wipe all data for ${recipe.name}?`)) purgeRecipe(recipe.slug) }}
          className="w-full py-2.5 bg-warning/10 text-warning border-none rounded-xl text-sm font-semibold cursor-pointer hover:bg-warning/15 transition-all disabled:opacity-50"
        >
          {purging === recipe.slug ? '⟳ Wiping...' : 'Wipe cached data'}
        </button>
      )}
    </div>
  )
}

/* ═══ Compose Editor ═══ */
function ComposeEditor({ slug }) {
  const [open, setOpen] = useState(false)
  const [content, setContent] = useState('')
  const [original, setOriginal] = useState('')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  const load = async () => {
    try {
      const res = await fetch(`/api/recipes/${slug}/compose`)
      if (res.ok) {
        const data = await res.json()
        setContent(data.content)
        setOriginal(data.content)
      }
    } catch (e) {
      console.error('Failed to load compose file:', e)
    }
  }

  const toggle = () => {
    if (!open) load()
    setOpen(!open)
    setSaved(false)
  }

  const save = async () => {
    setSaving(true)
    try {
      const res = await fetch(`/api/recipes/${slug}/compose`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content }),
      })
      if (res.ok) {
        setOriginal(content)
        setSaved(true)
        setTimeout(() => setSaved(false), 2000)
      }
    } catch (e) {
      console.error('Failed to save compose file:', e)
    } finally {
      setSaving(false)
    }
  }

  const dirty = content !== original

  return (
    <section>
      <button
        onClick={toggle}
        className="flex items-center gap-2 text-sm font-bold text-text font-display bg-transparent border-none cursor-pointer p-0 hover:text-primary transition-colors"
      >
        <svg className={`w-3.5 h-3.5 transition-transform ${open ? 'rotate-90' : ''}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
          <polyline points="9 18 15 12 9 6" />
        </svg>
        docker-compose.yml
      </button>
      {open && (
        <div className="mt-2">
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            spellCheck={false}
            className="w-full h-64 bg-[#08080F] text-gray-300 font-mono text-[11px] leading-5 p-3 rounded-xl border border-outline-dim resize-y focus:outline-none focus:border-primary/50"
          />
          <div className="flex items-center gap-2 mt-2">
            <button
              disabled={!dirty || saving}
              onClick={save}
              className="px-4 py-1.5 bg-primary text-white border-none rounded-lg text-xs font-semibold cursor-pointer disabled:opacity-40 disabled:cursor-default transition-all"
            >
              {saving ? 'Saving...' : 'Save'}
            </button>
            {saved && <span className="text-xs text-success font-label">Saved! Reinstall or relaunch to apply.</span>}
          </div>
        </div>
      )}
    </section>
  )
}

function SpecCard({ icon, value, label }) {
  return (
    <div className="bg-surface-high rounded-xl p-3 text-center">
      <div className="text-base mb-0.5">{icon}</div>
      <div className="text-sm font-bold text-text font-display">{value}</div>
      <div className="text-[10px] text-text-dim font-label mt-0.5">{label}</div>
    </div>
  )
}

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
        <span className="text-[10px] text-text-dim font-label block">{label}</span>
        <code className="text-xs text-text-muted font-mono break-all">{String(value)}</code>
      </div>
      <button onClick={copy} className="shrink-0 p-1.5 bg-surface border-none rounded-lg cursor-pointer text-text-dim hover:text-primary transition-colors" title="Copy">
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

/* ═══ Terminal Panel ═══ */
function TerminalPanel({ lines, isBuilding, isUpdating, isRunning, isReady, hasLogs, scrollRef }) {
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
    <div className="h-full flex flex-col bg-[#08080F]">
      <div className="flex items-center justify-between px-4 py-2.5 bg-[#0C0C14] shrink-0 border-b border-[#1a1a2a]">
        <div className="flex items-center gap-3">
          <div className="flex gap-1.5">
            <span className="w-2.5 h-2.5 rounded-full bg-[#ff5f57]/70" />
            <span className="w-2.5 h-2.5 rounded-full bg-[#febc2e]/70" />
            <span className="w-2.5 h-2.5 rounded-full bg-[#28c840]/70" />
          </div>
          <span className="text-[10px] text-gray-500 font-mono">
            {isUpdating ? 'update' : isBuilding ? 'build' : 'container'} — logs
          </span>
        </div>
        <div className="flex items-center gap-2">
          {isBuilding && <span className="text-[10px] text-primary animate-pulse font-mono">{isUpdating ? '● updating' : '● building'}</span>}
          {!isBuilding && isRunning && isReady && <span className="text-[10px] text-emerald-400 font-mono">● running</span>}
          {!isBuilding && isRunning && !isReady && <span className="text-[10px] text-amber-400 animate-pulse font-mono">● starting</span>}
        </div>
      </div>

      <div
        ref={scrollRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto p-4 font-mono text-[11px] leading-5 selection:bg-primary/30"
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
