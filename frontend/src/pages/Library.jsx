import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { useStore } from '../store'
import { buildLabel, displayName, formatDuration, isAgentModel, isLora, isModel, kindOf, publishers, starredByLine } from '../models'
import { openUrl } from '../lib/urls'
import { copyText } from '../lib/clipboard'
import FavoriteTiles from '../components/FavoriteTiles'
import FavStar from '../components/FavStar'
import PosterCard from '../components/PosterCard'
import KindTag from '../components/KindTag'
import RecipeLogo from '../components/RecipeLogo'

// Everything on this Spark, in the order you ask about it: what is up right
// now, what the admin recommends launching, and everything installed with
// what it costs on disk and when it was last used.
//
// It replaced the Running page, whose one wide card per running app said
// only the name, and whose 90-odd identical "Stopped" cards said nothing at
// all -- not the build, not the size, and offered LoRAs a Launch button
// though a LoRA never runs on its own.

// The daemon's timestamps are UTC "YYYY-MM-DD HH:MM:SS".
function parseUtc(at) {
  return at ? new Date(`${at.replace(' ', 'T')}Z`) : null
}

const DAY = 86400e3

function lastUsedLabel(at, now) {
  const when = parseUtc(at)
  if (!when) return { text: '—', old: false }
  const today = new Date(now); today.setHours(0, 0, 0, 0)
  const days = Math.ceil((today - when) / DAY)
  const text = days <= 0 ? 'today'
    : days === 1 ? 'yesterday'
      : days < 60 ? `${days} days ago`
        : `${Math.round(days / 30)} months ago`
  return { text, old: days > 30 }
}

function uptime(at, now) {
  const when = parseUtc(at)
  if (!when) return null
  const min = Math.max(0, Math.floor((now - when) / 60e3))
  if (min < 60) return `${min} min`
  if (min < 48 * 60) return `${Math.floor(min / 60)} h ${min % 60} m`
  return `${Math.floor(min / 1440)} d`
}

function gb(value) {
  if (value == null) return null
  return value >= 100 ? `${Math.round(value)} GB` : `${value.toFixed(1)} GB`
}

// When each recipe was last used and since when each running one is up.
// Read here only, every 30 s and whenever what runs changes.
function useActivity(runningKey) {
  const [activity, setActivity] = useState({ last_used: {}, up_since: {} })
  useEffect(() => {
    let live = true
    const load = () => fetch('/api/recipes/activity')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (live && d) setActivity(d) })
      .catch(() => {})
    load()
    const timer = setInterval(load, 30000)
    return () => { live = false; clearInterval(timer) }
  }, [runningKey])
  return activity
}

// The id each running LLM answers to at /v1, to copy into an agent.
function useServedIds(runningKey) {
  const [ids, setIds] = useState({})
  useEffect(() => {
    let live = true
    fetch('/v1/models')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!live) return
        const out = {}
        for (const m of d?.data || []) if (m.recipe && !out[m.recipe]) out[m.recipe] = m.id
        setIds({ ...out, _default: d?.data?.[0]?.recipe })
      })
      .catch(() => {})
    return () => { live = false }
  }, [runningKey])
  return ids
}

// A clock for uptimes and start progress: every second while something is
// starting, every minute otherwise.
function useNow(fast) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), fast ? 1000 : 60000)
    return () => clearInterval(timer)
  }, [fast])
  return now
}

export default function Library({ search = '' }) {
  const recipes = useStore((s) => s.recipes)
  const installing = useStore((s) => s.installing)
  const updating = useStore((s) => s.updating)
  const metrics = useStore((s) => s.metrics)

  const busy = (r) => !!(installing[r.slug] || updating[r.slug])
  const up = recipes.filter((r) => busy(r) || r.running || r.starting)
  const runningKey = up.map((r) => `${r.slug}:${r.ready ? 1 : 0}${r.favorite ? ':star' : ''}`).sort().join(',')
  const activity = useActivity(runningKey)
  const served = useServedIds(runningKey)
  const now = useNow(up.some((r) => r.starting || (r.running && !r.ready)))

  const installed = recipes.filter((r) => r.installed)
  const starred = recipes.filter((r) => r.favorite && !(r.running || r.starting || busy(r)))
  const starredModels = starred.filter(isModel)
  const starredApps = starred.filter((r) => !isModel(r))
  const onDisk = installed.reduce((n, r) => n + (r.weights_gb || 0), 0)
  const memFree = metrics?.ram_total_gb ? Math.max(0, metrics.ram_total_gb - metrics.ram_used_gb) : null

  const order = { llm: 0, media: 1, app: 2 }
  const upSorted = [...up].sort((a, b) => order[kindOf(a)] - order[kindOf(b)] || a.name.localeCompare(b.name))

  return (
    <div className="space-y-10 px-4 py-6 pb-14 sm:px-6">
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h1 className="m-0 font-display text-2xl font-bold tracking-tight text-text">Library</h1>
        <span className="font-label text-xs text-text-dim">
          {[
            `${installed.length} installed`,
            onDisk > 0 && `${onDisk >= 1000 ? `${(onDisk / 1000).toFixed(1)} TB` : `${Math.round(onDisk)} GB`} of models`,
            metrics?.disk_free_gb != null && `${Math.round(metrics.disk_free_gb)} GB disk free`,
          ].filter(Boolean).join(' · ')}
        </span>
      </header>

      <section className="space-y-3">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 className="m-0 font-display text-lg font-bold tracking-tight text-text">Running now</h2>
          <span className="font-label text-xs text-text-dim">
            {[up.length > 0 && `${up.length}`, memFree != null && `${Math.round(memFree)} GB memory free`].filter(Boolean).join(' · ')}
          </span>
        </div>
        {upSorted.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-outline px-4 py-6 text-center">
            <p className="m-0 font-display text-sm font-semibold text-text">Nothing is running</p>
            <p className="m-0 mt-1 text-xs text-text-dim">Launch something from the list below.</p>
          </div>
        ) : (
          <div className="space-y-2">
            {upSorted.map((r) => (
              <RunningRow
                key={r.slug}
                recipe={r}
                building={busy(r)}
                updatingNow={!!updating[r.slug]}
                upSince={activity.up_since[r.slug]}
                servedId={served[r.slug]}
                isDefault={served._default === r.slug}
                now={now}
              />
            ))}
          </div>
        )}
      </section>

      {starred.length > 0 && (
        <section className="space-y-3">
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <h2 className="m-0 font-display text-lg font-bold tracking-tight text-text">
              <span className="text-fav">★</span> Recommended on this Spark
            </h2>
            {starredByLine(starred) && <span className="font-label text-xs text-text-dim">{starredByLine(starred)}</span>}
          </div>
          {/* Each kind as it looks everywhere else: models as the tiles of
              Agents, apps as the posters of the Store. */}
          {starredModels.length > 0 && <FavoriteTiles recipes={starredModels} />}
          {starredApps.length > 0 && (
            <div className="flex flex-wrap gap-3">
              {starredApps.map((r) => <PosterCard key={r.slug} recipe={r} />)}
            </div>
          )}
        </section>
      )}

      <Installed recipes={installed} lastUsed={activity.last_used} now={now} busy={busy} search={search} />
    </div>
  )
}

/* ─── Running now ─── */

function RunningRow({ recipe, building, updatingNow, upSince, servedId, isDefault, now }) {
  const stopRecipe = useStore((s) => s.stopRecipe)
  const [stopping, setStopping] = useState(false)
  const [copied, setCopied] = useState(false)
  const kind = kindOf(recipe)
  const ready = recipe.running && recipe.ready
  const starting = !building && !ready
  const model = isModel(recipe)
  const sub = model ? [buildLabel(recipe), recipe.engine, publishers(recipe)].filter(Boolean).join(' · ') : recipe.author

  // How far along a start is, against how long this model took to get ready
  // when it was measured. Capped short of full: a start can run long.
  const elapsed = upSince ? (now - parseUtc(upSince)) / 1000 : null
  const progress = starting && recipe.load_seconds && elapsed != null
    ? Math.min(95, Math.max(4, (elapsed / recipe.load_seconds) * 100))
    : null
  const left = progress != null ? Math.max(0, recipe.load_seconds - elapsed) : null

  const stop = async () => {
    setStopping(true)
    await stopRecipe(recipe.slug)
    setStopping(false)
  }
  const copy = async () => {
    if (await copyText(servedId || recipe.slug)) {
      setCopied(true)
      setTimeout(() => setCopied(false), 1400)
    }
  }

  return (
    <div className={`grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-4 gap-y-3 rounded-2xl border bg-surface px-4 py-3 lg:grid-cols-[auto_minmax(0,1fr)_auto_auto] ${
      isDefault ? 'border-primary/40' : 'border-outline-dim'
    }`}>
      <Link to={`/app/${recipe.slug}`} className="no-underline">
        <RecipeLogo recipe={recipe} className="h-11 w-11 rounded-xl p-1.5" />
      </Link>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <FavStar recipe={recipe} className="text-[14px]" />
          <Link to={`/app/${recipe.slug}`} className="font-display text-[15px] font-bold leading-snug text-text no-underline hover:underline">
            {model ? displayName(recipe) : recipe.name}
          </Link>
          <KindTag recipe={recipe} specific />
          {isDefault && <span className="font-label text-[10px] font-bold text-primary">/v1 default</span>}
        </div>
        <div className="mt-0.5 font-label text-[11px] text-text-dim">{sub}</div>
        {progress != null && (
          <div className="mt-2 h-1.5 max-w-xs overflow-hidden rounded-full bg-surface-highest">
            <div className="h-full rounded-full bg-warning transition-[width] duration-1000" style={{ width: `${progress}%` }} />
          </div>
        )}
      </div>

      <div className="col-span-2 flex flex-wrap items-end gap-x-5 gap-y-2 font-label tabular-nums lg:col-span-1 lg:justify-end">
        {building ? (
          <Figure value={updatingNow ? 'Updating…' : 'Building…'} label="see its page for the log" tone="text-secondary" />
        ) : starting ? (
          <Figure
            value={left != null ? (left > 0 ? `~${formatDuration(left)}` : 'any moment') : 'Starting…'}
            label={left != null ? `left of ${formatDuration(recipe.load_seconds)}` : 'loading'}
            tone="text-warning"
          />
        ) : (
          isAgentModel(recipe) && recipe.tokens_per_second != null && (
            <Figure value={recipe.tokens_per_second} label="tok/s writing" tone="text-primary" />
          )
        )}
        {recipe.memory_gb > 0 && <Figure value={gb(recipe.memory_gb)} label="holds" />}
        {ready && uptime(upSince, now) && <Figure value={uptime(upSince, now)} label="up" />}
      </div>

      <div className="col-span-2 flex flex-wrap items-center gap-2 lg:col-span-1 lg:justify-end">
        {ready && kind === 'app' && (
          <a href={openUrl(recipe)} target="_blank" rel="noreferrer" className="btn-primary px-3.5 py-1.5 text-xs font-bold no-underline">
            Open ↗
          </a>
        )}
        {ready && kind === 'llm' && (
          <button type="button" onClick={copy}
            className="cursor-pointer rounded-xl border border-outline bg-transparent px-3 py-1.5 font-label text-xs font-semibold text-text-muted hover:border-text-dim hover:text-text">
            {copied ? 'Copied ✓' : 'Copy model id'}
          </button>
        )}
        {!building && (
          <button type="button" onClick={stop} disabled={stopping}
            className="cursor-pointer rounded-xl border border-outline-dim bg-surface-high px-3 py-1.5 font-label text-xs font-medium text-text-muted hover:border-error hover:text-error disabled:opacity-50">
            {stopping ? 'Stopping…' : 'Stop'}
          </button>
        )}
      </div>
    </div>
  )
}

function Figure({ value, label, tone = 'text-text' }) {
  return (
    <div className="flex flex-col lg:items-end">
      <span className={`whitespace-nowrap font-display text-[16px] font-bold ${tone}`}>{value}</span>
      <span className="whitespace-nowrap text-[10px] uppercase tracking-wider text-text-dim">{label}</span>
    </div>
  )
}

/* ─── Installed ─── */

const FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'llm', label: 'Language' },
  { id: 'media', label: 'Media' },
  { id: 'app', label: 'Apps' },
]

const SORTS = [
  { id: 'used', label: 'Last used' },
  { id: 'disk', label: 'On disk' },
  { id: 'name', label: 'Name' },
]

function nameOf(r) {
  return isModel(r) ? displayName(r) : r.name
}

function Installed({ recipes, lastUsed, now, busy, search }) {
  const [filter, setFilter] = useState('all')
  const [sort, setSort] = useState('used')

  // A LoRA sits on the row of the model it loads into: it never runs on its
  // own, so it gets no row, no Launch and no count of its own. One whose
  // models are all uninstalled keeps a row, or it would vanish from view.
  const { rows, lorasOf } = useMemo(() => {
    const slugs = new Set(recipes.map((r) => r.slug))
    const lorasOf = {}
    const rows = []
    for (const r of recipes) {
      if (isLora(r)) {
        const bases = (r.lora?.bases || []).filter((b) => slugs.has(b))
        bases.forEach((b) => { (lorasOf[b] ||= []).push(r) })
        if (bases.length === 0) rows.push(r)
      } else {
        rows.push(r)
      }
    }
    return { rows, lorasOf }
  }, [recipes])

  const counts = useMemo(() => {
    const c = { all: rows.length, llm: 0, media: 0, app: 0 }
    rows.forEach((r) => { c[kindOf(r)] += 1 })
    return c
  }, [rows])

  const shown = useMemo(() => {
    const q = search.trim().toLowerCase()
    const out = rows.filter((r) => (filter === 'all' || kindOf(r) === filter)
      && (!q || nameOf(r).toLowerCase().includes(q) || r.slug.includes(q)
        || (lorasOf[r.slug] || []).some((l) => l.name.toLowerCase().includes(q))))
    const used = (r) => lastUsed[r.slug] || ''
    const byName = (a, b) => nameOf(a).localeCompare(nameOf(b))
    return out.sort(
      sort === 'disk' ? (a, b) => (b.weights_gb ?? -1) - (a.weights_gb ?? -1) || byName(a, b)
        : sort === 'name' ? byName
          : (a, b) => used(b).localeCompare(used(a)) || byName(a, b),
    )
  }, [rows, lorasOf, filter, sort, lastUsed, search])

  if (recipes.length === 0) {
    return (
      <section className="space-y-3">
        <h2 className="m-0 font-display text-lg font-bold tracking-tight text-text">Installed</h2>
        <p className="m-0 text-sm text-text-dim">
          Nothing installed yet. Pick an app in the <Link to="/" className="text-primary">Store</Link> or a model on <Link to="/agents" className="text-primary">Agents</Link>.
        </p>
      </section>
    )
  }

  return (
    <section className="space-y-3">
      <h2 className="m-0 font-display text-lg font-bold tracking-tight text-text">Installed</h2>
      <div className="flex flex-wrap items-center gap-2">
        <Segmented options={FILTERS.map((f) => ({ ...f, label: `${f.label} ${counts[f.id]}` }))} value={filter} onChange={setFilter} quiet />
        <div className="flex items-center gap-2 sm:ml-auto">
          <span className="font-label text-[10px] uppercase tracking-wider text-text-dim">Sort</span>
          <Segmented options={SORTS} value={sort} onChange={setSort} />
        </div>
      </div>

      <div>
        <div className={`${ROW_GRID} border-b border-outline-dim px-2 pb-1.5 font-label text-[9px] font-semibold uppercase tracking-wider text-text-dim`}>
          <span>Build</span>
          <span className="hidden sm:block">Kind</span>
          <span className="hidden sm:block">Last used</span>
          <span className="hidden text-right sm:block">On disk</span>
          <span />
          <span />
        </div>
        {shown.length === 0 && (
          <p className="m-0 px-2 py-4 text-sm text-text-dim">Nothing installed matches “{search}”.</p>
        )}
        {shown.map((r) => (
          <InstalledRow
            key={r.slug}
            recipe={r}
            loras={lorasOf[r.slug] || []}
            used={lastUsedLabel(lastUsed[r.slug], now)}
            building={busy(r)}
          />
        ))}
      </div>
    </section>
  )
}

// Phones keep the name, the action and the menu; kind, last use and size
// move under the name.
const ROW_GRID = 'grid grid-cols-[minmax(0,1fr)_auto_28px] items-center gap-x-3 sm:grid-cols-[minmax(0,1fr)_64px_100px_72px_84px_28px]'

function Segmented({ options, value, onChange, quiet = false }) {
  return (
    <div className="row-scroller flex max-w-full items-center gap-0.5 overflow-x-auto rounded-xl border border-outline-dim bg-surface-high p-0.5">
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          aria-pressed={value === o.id}
          onClick={() => onChange(o.id)}
          className={`shrink-0 cursor-pointer whitespace-nowrap rounded-lg border-none px-2.5 py-1 font-label text-[11px] font-semibold transition-colors ${
            value === o.id
              ? quiet ? 'bg-surface-highest text-text' : 'bg-primary text-primary-on'
              : 'bg-transparent text-text-muted hover:text-text'
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

function InstalledRow({ recipe, loras, used, building }) {
  const requestLaunch = useStore((s) => s.requestLaunch)
  const removeRecipe = useStore((s) => s.removeRecipe)
  const removing = useStore((s) => !!s.removing?.[recipe.slug])
  const [confirming, setConfirming] = useState(false)
  const model = isModel(recipe)
  const name = nameOf(recipe)
  const sub = model ? [buildLabel(recipe), recipe.engine, publishers(recipe)].filter(Boolean).join(' · ') : recipe.author
  const disk = gb(recipe.weights_gb)
  const phoneFacts = [used.text !== '—' && used.text, disk].filter(Boolean)

  const action = building ? (
    <span className="font-label text-[11px] font-semibold text-secondary">Building…</span>
  ) : removing ? (
    <span className="font-label text-[11px] text-text-dim">Removing…</span>
  ) : recipe.running && recipe.ready ? (
    <span className="flex items-center justify-end gap-1.5 font-label text-[11px] font-bold text-primary">
      <span className="h-1.5 w-1.5 rounded-full bg-primary" />Running
    </span>
  ) : recipe.running || recipe.starting ? (
    <span className="font-label text-[11px] font-semibold text-warning">Starting…</span>
  ) : isLora(recipe) ? (
    <span className="font-label text-[11px] text-text-dim">LoRA</span>
  ) : (
    <button type="button" onClick={() => requestLaunch(recipe.slug)} className="btn-primary w-full px-3 py-1.5 text-[11px] font-bold">
      Launch
    </button>
  )

  return (
    <div className={`border-b border-outline-dim px-2 py-2.5 ${recipe.favorite ? 'bg-fav/5' : ''}`}>
      <div className={ROW_GRID}>
        <div className="flex min-w-0 items-center gap-3">
          <Link to={`/app/${recipe.slug}`} className="no-underline">
            <RecipeLogo recipe={recipe} className="h-8 w-8 rounded-lg p-1" />
          </Link>
          <div className="min-w-0">
            <div className="flex min-w-0 items-center gap-1.5">
              <FavStar recipe={recipe} />
              <Link to={`/app/${recipe.slug}`} className="min-w-0 font-label text-[13px] font-bold text-text no-underline hover:underline">
                {name}
              </Link>
            </div>
            <div className="font-label text-[10.5px] text-text-dim">
              {sub}
              {/* What the wide columns say, on a phone. */}
              {phoneFacts.length > 0 && (
                <span className="sm:hidden">{sub ? ' · ' : ''}{phoneFacts.join(' · ')}</span>
              )}
            </div>
          </div>
        </div>
        <span className="hidden sm:block"><KindTag recipe={recipe} specific /></span>
        <span className={`hidden font-label text-[12px] sm:block ${used.old ? 'text-warning' : 'text-text-muted'}`}>{used.text}</span>
        <span className="hidden text-right font-label text-[12px] tabular-nums text-text-muted sm:block">{disk ?? '—'}</span>
        <span className="flex justify-end">{action}</span>
        <button
          type="button"
          aria-label={`More for ${name}`}
          aria-expanded={confirming}
          onClick={() => setConfirming(!confirming)}
          className="flex h-7 w-7 cursor-pointer items-center justify-center rounded-lg border border-outline-dim bg-transparent text-[15px] leading-none text-text-dim hover:border-text-dim hover:text-text"
        >
          ⋯
        </button>
      </div>

      {loras.length > 0 && (
        <div className="mt-2 flex flex-wrap items-center gap-1.5 sm:pl-11">
          <span className="mr-0.5 font-label text-[9px] font-bold uppercase tracking-wider text-text-dim">LoRAs {loras.length}</span>
          {loras.map((l) => (
            <Link key={l.slug} to={`/app/${l.slug}`}
              className="rounded-md border border-outline-dim bg-surface px-2 py-0.5 font-label text-[10.5px] text-text-muted no-underline hover:border-text-dim hover:text-text">
              {loraLabel(l, recipe)}
            </Link>
          ))}
        </div>
      )}

      {confirming && (
        <div className="mt-2 flex flex-wrap items-center gap-2 rounded-xl border border-error/30 bg-error/10 px-3 py-2 text-[12.5px] text-text">
          <span className="min-w-0">
            Uninstall <b>{name}</b>?{disk && <> Frees <b>{disk}</b>.</>}
            {recipe.running && ' It stops first.'}
            {loras.length > 0 && ` Its ${loras.length} LoRA${loras.length > 1 ? 's' : ''} stay installed.`}
          </span>
          <span className="flex gap-2">
            <button type="button" disabled={removing}
              onClick={async () => { await removeRecipe(recipe.slug); setConfirming(false) }}
              className="cursor-pointer rounded-lg border-none bg-error px-3 py-1 font-label text-xs font-bold text-white disabled:opacity-50">
              Uninstall
            </button>
            <button type="button" onClick={() => setConfirming(false)}
              className="cursor-pointer rounded-lg border border-outline bg-transparent px-3 py-1 font-label text-xs font-semibold text-text-muted hover:text-text">
              Keep
            </button>
          </span>
        </div>
      )}
    </div>
  )
}

// "LTX-2.5 Day-to-Night IC-LoRA" under LTX-2.5 reads as "Day-to-Night IC-LoRA":
// the row already names the model.
function loraLabel(lora, base) {
  const words = base.name.split(/\s+/)
  let name = lora.name
  for (let n = words.length; n > 0; n -= 1) {
    const prefix = words.slice(0, n).join(' ')
    if (name.startsWith(`${prefix} `)) { name = name.slice(prefix.length + 1); break }
  }
  return name
}
