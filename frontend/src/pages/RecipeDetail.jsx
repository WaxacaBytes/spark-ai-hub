import { useEffect, useLayoutEffect, useRef, useState } from 'react'
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
  const [logoFailed, setLogoFailed] = useState(false)
  const [launching, setLaunching] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [logsOpen, setLogsOpen] = useState(false)

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
  const requirements = recipe.requirements || {}
  const buildMinutes = recipe.docker?.build_time_minutes ?? 5
  const recipeTags = Array.isArray(recipe.tags) ? recipe.tags : []
  const recipeCategories = Array.isArray(recipe.categories) && recipe.categories.length > 0
    ? recipe.categories
    : [recipe.category]
  const previewLines = logLines.slice(-24)

  useEffect(() => {
    if (!logsOpen) return
    const onKeyDown = (e) => {
      if (e.key === 'Escape') setLogsOpen(false)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [logsOpen])

  const handleRemove = () => {
    if (window.confirm(`Uninstall ${recipe.name}? This removes containers, images, and volumes.`)) {
      removeRecipe(recipe.slug)
    }
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
    <div className="flex flex-col h-full animate-fadeIn">
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

        <div className="flex flex-col gap-5 xl:flex-row xl:items-center">
          <div className="flex items-start gap-5 min-w-0 flex-1">
            {logoUrl && !logoFailed ? (
              <img
                src={logoUrl}
                alt={recipe.name}
                className="w-20 h-20 rounded-2xl object-contain bg-surface-high p-2.5 shadow-lg shrink-0"
                onError={() => setLogoFailed(true)}
              />
            ) : (
              <div className="w-20 h-20 rounded-2xl bg-surface-high flex items-center justify-center text-4xl shrink-0">
                {recipe.icon || '◻'}
              </div>
            )}

            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2 mb-2">
                {recipeCategories.map((cat) => (
                  <span key={cat} className="text-[10px] font-label text-secondary bg-secondary/10 px-2 py-0.5 rounded-full">
                    {cat}
                  </span>
                ))}
                {isBuilding && <StatusPill color="primary" pulse>Building...</StatusPill>}
                {isUpdating && <StatusPill color="primary" pulse>Updating...</StatusPill>}
                {!isBusy && recipe.running && isReady && <StatusPill color="success">Running</StatusPill>}
                {!isBusy && recipe.running && !isReady && <StatusPill color="warning" pulse>Starting...</StatusPill>}
                {!isBusy && !recipe.running && recipe.installed && <StatusPill color="dim">Stopped</StatusPill>}
              </div>
              <h1 className="text-2xl font-bold text-text tracking-tight m-0 font-display">{recipe.name}</h1>
              <p className="text-sm text-text-dim mt-1 m-0">{recipe.author}</p>
              <p className="text-sm text-text-muted leading-relaxed mt-3 mb-0 max-w-4xl">{recipe.description}</p>
            </div>
          </div>

          <div className="shrink-0 flex flex-wrap items-center gap-2">
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
                <button
                  disabled={launching || isRemoving}
                  onClick={() => updateRecipe(recipe.slug)}
                  className="px-4 py-2.5 bg-surface-high text-text-muted border-none rounded-xl text-sm font-semibold cursor-pointer transition-all hover:text-primary hover:bg-surface-highest disabled:opacity-50"
                >
                  ↻ Update
                </button>
                <button
                  disabled={isRemoving}
                  onClick={handleRemove}
                  className="px-4 py-2.5 bg-error-surface text-error border-none rounded-xl text-sm font-semibold cursor-pointer transition-all disabled:opacity-50"
                >
                  {isRemoving ? '...' : 'Uninstall'}
                </button>
              </>
            )}
            {recipe.running && (
              <>
                {isReady && (
                  <a
                    href={`http://${location.hostname}:${recipe.ui?.port ?? 8080}${recipe.ui?.path ?? '/'}`}
                    target="_blank"
                    rel="noreferrer"
                    className="btn-primary px-6 py-2.5 text-sm font-bold no-underline inline-block"
                  >
                    Open ↗
                  </a>
                )}
                <button
                  disabled={stopping}
                  onClick={handleStop}
                  className="px-4 py-2.5 bg-surface-high text-text-muted border-none rounded-xl text-sm font-semibold cursor-pointer disabled:opacity-50"
                >
                  {stopping ? '...' : '■ Stop'}
                </button>
                <button
                  disabled={isRemoving}
                  onClick={handleRemove}
                  className="px-4 py-2.5 bg-error-surface text-error border-none rounded-xl text-sm font-semibold cursor-pointer disabled:opacity-50"
                >
                  Uninstall
                </button>
              </>
            )}
          </div>
        </div>
      </div>

      <div className="px-6 py-6 space-y-6">
          <section className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
            <SpecCard icon="🧠" value={`${requirements.min_memory_gb ?? 8} GB`} label="Minimum RAM" />
            <SpecCard icon="⚡" value={`${requirements.recommended_memory_gb ?? requirements.min_memory_gb ?? 8} GB`} label="Recommended RAM" />
            <SpecCard icon="💾" value={`${requirements.disk_gb ?? 10} GB`} label="Disk" />
            <SpecCard icon="⏱" value={`~${buildMinutes} min`} label="Build Time" />
          </section>

          {recipeTags.length > 0 && (
            <section className="flex flex-wrap gap-1.5">
              {recipeTags.map((tag) => (
                <span key={tag} className="bg-surface-high text-text-dim px-2.5 py-1 rounded-full text-[11px] font-label">
                  {tag}
                </span>
              ))}
            </section>
          )}

          <div className="grid gap-6 xl:grid-cols-[minmax(0,1.05fr)_minmax(360px,0.95fr)]">
            <section className="bg-surface-low/50 border border-outline-dim rounded-3xl p-5 space-y-5">
              <div>
                <h2 className="text-sm font-bold text-text font-display m-0 mb-2">About</h2>
                <p className="text-sm text-text-muted leading-relaxed m-0">{recipe.description}</p>
              </div>

              {(recipe.website || recipe.upstream || recipe.fork) && (
                <div>
                  <h2 className="text-sm font-bold text-text font-display m-0 mb-2">Links</h2>
                  <div className="flex flex-wrap gap-2">
                    {recipe.website && (
                      <a
                        href={recipe.website}
                        target="_blank"
                        rel="noreferrer"
                        className="flex items-center gap-2 bg-surface-high rounded-xl px-4 py-2.5 text-sm text-primary no-underline hover:bg-surface-highest transition-colors font-medium"
                      >
                        <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <circle cx="12" cy="12" r="10" />
                          <line x1="2" y1="12" x2="22" y2="12" />
                          <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
                        </svg>
                        Website
                      </a>
                    )}
                    {(recipe.upstream || recipe.fork) && (
                      <a
                        href={recipe.upstream || recipe.fork}
                        target="_blank"
                        rel="noreferrer"
                        className="flex items-center gap-2 bg-surface-high rounded-xl px-4 py-2.5 text-sm text-text-muted no-underline hover:bg-surface-highest transition-colors font-medium"
                      >
                        <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M9 19c-5 1.5-5-2.5-7-3m14 6v-3.87a3.37 3.37 0 0 0-.94-2.61c3.14-.35 6.44-1.54 6.44-7A5.44 5.44 0 0 0 20 4.77 5.07 5.07 0 0 0 19.91 1S18.73.65 16 2.48a13.38 13.38 0 0 0-7 0C6.27.65 5.09 1 5.09 1A5.07 5.07 0 0 0 5 4.77a5.44 5.44 0 0 0-1.5 3.78c0 5.42 3.3 6.61 6.44 7A3.37 3.37 0 0 0 9 18.13V22" />
                        </svg>
                        Source
                      </a>
                    )}
                  </div>
                </div>
              )}
            </section>

            {recipe.integration && (
              <section className="bg-surface-low/50 border border-outline-dim rounded-3xl p-5 space-y-3">
                <h2 className="text-sm font-bold text-text font-display m-0">API Integration</h2>
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
              </section>
            )}
          </div>

          <ComposeEditor slug={recipe.slug} />

          <section className="border border-outline-dim rounded-3xl overflow-hidden">
            <TerminalPanel
              lines={previewLines}
              totalLines={logLines.length}
              isBuilding={isBusy}
              isUpdating={isUpdating}
              isRunning={recipe.running}
              isReady={isReady}
              hasLogs={cLogs.length > 0}
              onExpand={() => setLogsOpen(true)}
            />
          </section>

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

      {logsOpen && (
        <LogsOverlay
          lines={logLines}
          isBuilding={isBusy}
          isUpdating={isUpdating}
          isRunning={recipe.running}
          isReady={isReady}
          onClose={() => setLogsOpen(false)}
        />
      )}
    </div>
  )
}

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

function ComposeEditor({ slug }) {
  const textareaRef = useRef(null)
  const [content, setContent] = useState('')
  const [original, setOriginal] = useState('')
  const [defaultContent, setDefaultContent] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [resetting, setResetting] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false

    const load = async () => {
      setLoading(true)
      setError('')
      try {
        const res = await fetch(`/api/recipes/${slug}/compose`)
        if (!res.ok) throw new Error('load failed')
        const data = await res.json()
        if (cancelled) return
        setContent(data.content)
        setOriginal(data.content)
        setDefaultContent(data.default_content || data.content)
      } catch (e) {
        if (!cancelled) setError('Failed to load docker-compose.yml')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    load()
    return () => { cancelled = true }
  }, [slug])

  const save = async () => {
    setSaving(true)
    setError('')
    try {
      const res = await fetch(`/api/recipes/${slug}/compose`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content }),
      })
      if (!res.ok) throw new Error('save failed')
      setOriginal(content)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch (e) {
      setError('Failed to save docker-compose.yml')
    } finally {
      setSaving(false)
    }
  }

  const resetToDefault = async () => {
    if (!window.confirm('Reset docker-compose.yml to the default recipe version?')) return
    setResetting(true)
    setError('')
    try {
      const res = await fetch(`/api/recipes/${slug}/compose/reset`, { method: 'POST' })
      if (!res.ok) throw new Error('reset failed')
      const data = await res.json()
      setContent(data.content)
      setOriginal(data.content)
      setDefaultContent(data.content)
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    } catch (e) {
      setError('Failed to reset docker-compose.yml')
    } finally {
      setResetting(false)
    }
  }

  const dirty = content !== original
  const canReset = original !== defaultContent || content !== defaultContent
  const showingCustomizedState = !dirty && canReset

  useLayoutEffect(() => {
    const el = textareaRef.current
    if (!el || loading) return
    el.style.height = '0px'
    el.style.height = `${el.scrollHeight}px`
  }, [content, loading])

  return (
    <section className="bg-surface-low/50 border border-outline-dim rounded-3xl p-5">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div>
          <h2 className="text-sm font-bold text-text font-display m-0">docker-compose.yml</h2>
          <p className="text-sm text-text-dim mt-1 mb-0">
            Edit the live recipe config. Reset restores the committed default from this repo.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {dirty ? (
            <button
              disabled={saving || loading}
              onClick={() => setContent(original)}
              className="px-4 py-2 bg-surface-high text-text-muted border-none rounded-xl text-sm font-semibold cursor-pointer transition-all hover:text-text disabled:opacity-40 disabled:cursor-default"
            >
              Revert edits
            </button>
          ) : (
            <button
              disabled={!canReset || resetting || loading}
              onClick={resetToDefault}
              className="px-4 py-2 bg-warning/10 text-warning border-none rounded-xl text-sm font-semibold cursor-pointer transition-all hover:bg-warning/15 disabled:opacity-40 disabled:cursor-default"
            >
              {resetting ? 'Restoring...' : 'Restore default'}
            </button>
          )}
          <button
            disabled={!dirty || saving || loading}
            onClick={save}
            className="px-4 py-2 bg-primary text-white border-none rounded-xl text-sm font-semibold cursor-pointer transition-all disabled:opacity-40 disabled:cursor-default"
          >
            {saving ? 'Saving...' : 'Save'}
          </button>
        </div>
      </div>

      <div className="mt-4">
        {loading ? (
          <div className="h-72 rounded-2xl bg-[#08080F] border border-outline-dim flex items-center justify-center text-sm text-text-dim">
            Loading docker-compose.yml...
          </div>
        ) : (
          <textarea
            ref={textareaRef}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            spellCheck={false}
            className="w-full min-h-[360px] overflow-hidden bg-[#08080F] text-gray-300 font-mono text-[12px] leading-6 p-4 rounded-2xl border border-outline-dim resize-none focus:outline-none focus:border-primary/50"
          />
        )}
      </div>

      <div className="flex flex-wrap items-center gap-3 mt-3 text-xs">
        {saved && <span className="text-success font-label">Saved. Reinstall or relaunch to apply.</span>}
        {dirty && !saved && <span className="text-warning font-label">Unsaved changes</span>}
        {showingCustomizedState && !saved && <span className="text-text-dim font-label">Using a customized compose file.</span>}
        {error && <span className="text-error font-label">{error}</span>}
      </div>
    </section>
  )
}

function SpecCard({ icon, value, label }) {
  return (
    <div className="bg-surface-low/50 border border-outline-dim rounded-2xl p-4">
      <div className="text-base mb-1">{icon}</div>
      <div className="text-lg font-bold text-text font-display">{value}</div>
      <div className="text-[11px] text-text-dim font-label mt-1">{label}</div>
    </div>
  )
}

function Field({ label, value }) {
  const [copied, setCopied] = useState(false)

  const copy = () => {
    const text = String(value)
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text)
        .then(() => {
          setCopied(true)
          setTimeout(() => setCopied(false), 1200)
        })
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
          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="20 6 9 17 4 12" />
          </svg>
        ) : (
          <svg className="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="9" y="9" width="13" height="13" rx="2" />
            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
          </svg>
        )}
      </button>
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
  setTimeout(() => setCopied(false), 1200)
}

function TerminalPanel({ lines, totalLines, isBuilding, isUpdating, isRunning, isReady, hasLogs, onExpand }) {
  const showEmpty = !isBuilding && !isRunning && !hasLogs

  return (
    <div className="bg-[#08080F]">
      <div className="flex items-center justify-between px-4 py-3 bg-[#0C0C14] shrink-0 border-b border-[#1a1a2a]">
        <div className="flex items-center gap-3">
          <div className="flex gap-1.5">
            <span className="w-2.5 h-2.5 rounded-full bg-[#ff5f57]/70" />
            <span className="w-2.5 h-2.5 rounded-full bg-[#febc2e]/70" />
            <span className="w-2.5 h-2.5 rounded-full bg-[#28c840]/70" />
          </div>
          <span className="text-[10px] text-gray-500 font-mono">
            {isUpdating ? 'update' : isBuilding ? 'build' : 'container'} - logs
          </span>
        </div>
        <div className="flex items-center gap-2">
          {isBuilding && <span className="text-[10px] text-primary animate-pulse font-mono">{isUpdating ? '● updating' : '● building'}</span>}
          {!isBuilding && isRunning && isReady && <span className="text-[10px] text-emerald-400 font-mono">● running</span>}
          {!isBuilding && isRunning && !isReady && <span className="text-[10px] text-amber-400 animate-pulse font-mono">● starting</span>}
          <button
            onClick={onExpand}
            className="px-2.5 py-1 bg-surface-high text-text-muted border-none rounded-lg text-[10px] font-semibold cursor-pointer hover:text-text transition-colors"
          >
            Open logs
          </button>
        </div>
      </div>

      <div className="p-4 font-mono text-[11px] leading-5 selection:bg-primary/30">
        {showEmpty && (
          <div className="text-gray-600 italic">No logs. Launch the app to see output here.</div>
        )}
        {!showEmpty && totalLines > lines.length && (
          <div className="text-[10px] text-gray-500 mb-3">
            Showing last {lines.length} of {totalLines} lines.
          </div>
        )}
        {lines.map((line, i) => (
          <div
            key={i}
            className={
              line.includes('[error]') || line.includes('Error') || line.includes('FATAL') || line.includes('Traceback')
                ? 'text-red-400'
                : line.includes('successfully') || line.includes('Started') || line.includes('Running on')
                  ? 'text-emerald-400'
                  : 'text-gray-400'
            }
          >
            {line}
          </div>
        ))}
        {(isBuilding || (isRunning && !isReady)) && lines.length > 0 && (
          <span className="text-primary animate-pulse">▋</span>
        )}
      </div>
    </div>
  )
}

function LogsOverlay({ lines, isBuilding, isUpdating, isRunning, isReady, onClose }) {
  const scrollRef = useRef(null)

  useLayoutEffect(() => {
    const el = scrollRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
  }, [lines.length])

  useEffect(() => {
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = '' }
  }, [])

  return (
    <div className="fixed inset-0 z-50 bg-black/75 backdrop-blur-sm flex items-stretch justify-center p-4 md:p-6">
      <div className="w-full max-w-7xl bg-[#08080F] border border-[#1a1a2a] rounded-3xl overflow-hidden shadow-2xl">
        <div className="flex items-center justify-between px-5 py-4 bg-[#0C0C14] border-b border-[#1a1a2a]">
          <div className="flex items-center gap-3">
            <div className="flex gap-1.5">
              <span className="w-2.5 h-2.5 rounded-full bg-[#ff5f57]/70" />
              <span className="w-2.5 h-2.5 rounded-full bg-[#febc2e]/70" />
              <span className="w-2.5 h-2.5 rounded-full bg-[#28c840]/70" />
            </div>
            <div>
              <div className="text-sm text-white font-semibold font-display">Full Logs</div>
              <div className="text-[10px] text-gray-500 font-mono">
                {isUpdating ? 'update' : isBuilding ? 'build' : 'container'} - {lines.length} lines
              </div>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {isBuilding && <span className="text-[10px] text-primary animate-pulse font-mono">{isUpdating ? '● updating' : '● building'}</span>}
            {!isBuilding && isRunning && isReady && <span className="text-[10px] text-emerald-400 font-mono">● running</span>}
            {!isBuilding && isRunning && !isReady && <span className="text-[10px] text-amber-400 animate-pulse font-mono">● starting</span>}
            <button
              onClick={onClose}
              className="px-3 py-1.5 bg-surface-high text-text-muted border-none rounded-lg text-xs font-semibold cursor-pointer hover:text-text transition-colors"
            >
              Close
            </button>
          </div>
        </div>

        <div
          ref={scrollRef}
          className="h-[calc(100vh-10rem)] overflow-y-auto p-5 font-mono text-[12px] leading-6 selection:bg-primary/30"
        >
          {lines.length === 0 ? (
            <div className="text-gray-600 italic">No logs yet.</div>
          ) : (
            lines.map((line, i) => (
              <div
                key={i}
                className={
                  line.includes('[error]') || line.includes('Error') || line.includes('FATAL') || line.includes('Traceback')
                    ? 'text-red-400'
                    : line.includes('successfully') || line.includes('Started') || line.includes('Running on')
                      ? 'text-emerald-400'
                      : 'text-gray-400'
                }
              >
                {line}
              </div>
            ))
          )}
          {(isBuilding || (isRunning && !isReady)) && lines.length > 0 && (
            <span className="text-primary animate-pulse">▋</span>
          )}
        </div>
      </div>
    </div>
  )
}
