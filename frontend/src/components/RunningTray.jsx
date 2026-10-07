import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useStore } from '../store'
import { useThemedLogo } from '../hooks/useThemedLogo'
import { buildLabel, displayName, isAgentModel, isModel } from '../models'
import { openUrl } from './RecipeCard'

// What is running on this Spark, in the header of every page.
//
// A count on a sidebar icon said how many things were up, never which; the
// Running page said which, but only once you went there. Here each one is a
// pill that names itself the way you would: its kind (an app you open, a
// language model agents talk to at /v1, a media model they call through
// /mcp), the model, and for models the build. The memory gauge sits at the
// end because on a Spark that is what decides whether the next launch fits.

const MAX_PILLS = 4

const KIND = {
  app: { label: 'App', className: 'bg-surface-highest text-text-muted' },
  llm: { label: 'LLM', className: 'bg-primary/15 text-primary' },
  media: { label: 'Media', className: 'bg-tertiary/15 text-tertiary' },
}

function kindOf(recipe) {
  if (isAgentModel(recipe)) return 'llm'
  if (isModel(recipe)) return 'media'
  return 'app'
}

// The /v1 default is the largest LLM up -- the same order the daemon's
// router uses (params, then memory held).
function bySize(a, b) {
  return (b.params_b || 0) - (a.params_b || 0) || (b.memory_gb || 0) - (a.memory_gb || 0)
}

const KIND_ORDER = { llm: 0, media: 1, app: 2 }

// `scroll`: phones, where the tray has a row of its own that scrolls
// sideways — every pill keeps its full width and none is folded into "+N".
export default function RunningTray({ scroll = false }) {
  const recipes = useStore((s) => s.recipes)
  const installing = useStore((s) => s.installing)
  const updating = useStore((s) => s.updating)
  const metrics = useStore((s) => s.metrics)

  const building = recipes.filter((r) => installing[r.slug] || updating[r.slug])
  const up = recipes
    .filter((r) => (r.running || r.starting) && !installing[r.slug] && !updating[r.slug])
    .sort((a, b) => KIND_ORDER[kindOf(a)] - KIND_ORDER[kindOf(b)] || bySize(a, b))
  const defaultLlm = up.find((r) => kindOf(r) === 'llm' && r.running && r.ready)

  const items = [...building, ...up]
  const shown = scroll ? items : items.slice(0, MAX_PILLS)
  const hidden = items.length - shown.length

  return (
    <div className={`flex items-center gap-1.5 ${scroll ? 'w-max' : 'min-w-0 flex-1'}`}>
      {items.length === 0 ? (
        <Link to="/running" className="flex items-center gap-2 rounded-xl px-2 py-1 font-label text-[11px] text-text-dim no-underline hover:text-text">
          <span className="h-1.5 w-1.5 rounded-full bg-text-dim" />
          Nothing running
        </Link>
      ) : (
        <>
          {shown.map((r) => (
            <Pill
              key={r.slug}
              recipe={r}
              building={!!(installing[r.slug] || updating[r.slug])}
              isDefault={r === defaultLlm}
              scroll={scroll}
            />
          ))}
          {hidden > 0 && (
            <Link
              to="/running"
              title="Everything running on this Spark"
              className="shrink-0 rounded-xl border border-outline-dim px-2.5 py-2 font-label text-[11px] font-bold text-text-muted no-underline hover:border-text-dim hover:text-text"
            >
              +{hidden}
            </Link>
          )}
        </>
      )}
      {metrics?.ram_total_gb > 0 && <MemoryGauge used={metrics.ram_used_gb} total={metrics.ram_total_gb} />}
    </div>
  )
}

function Pill({ recipe, building, isDefault, scroll }) {
  const selectRecipe = useStore((s) => s.selectRecipe)
  const stopRecipe = useStore((s) => s.stopRecipe)
  const logoUrl = useThemedLogo(recipe.logo)
  const [logoFailed, setLogoFailed] = useState(false)
  const kind = kindOf(recipe)
  const ready = recipe.running && recipe.ready

  const state = building
    ? { dot: 'bg-secondary animate-pulse', label: 'Building' }
    : ready
      ? { dot: 'bg-primary', label: 'Running' }
      : { dot: 'bg-warning animate-pulse', label: 'Starting' }

  // Models are told apart by build; apps by who made them.
  const detail = kind === 'app'
    ? recipe.author
    : [buildLabel(recipe), recipe.engine].filter(Boolean).join(' · ')
  const memory = recipe.memory_gb ? `${Math.round(recipe.memory_gb)} GB` : null
  const name = kind === 'app' ? recipe.name : displayName(recipe)

  return (
    <div
      className={`group relative flex min-w-0 ${scroll ? 'max-w-[250px] shrink-0' : 'max-w-[290px] shrink'} items-center gap-2 rounded-xl border bg-surface py-1 pl-1.5 pr-2 transition-colors hover:border-text-dim ${
        isDefault ? 'border-primary/40' : 'border-outline-dim'
      }`}
      title={`${recipe.name} — ${state.label}${memory ? ` · holds ${memory}` : ''}${isDefault ? ' · /v1 default' : ''}`}
    >
      <button
        type="button"
        onClick={() => selectRecipe(recipe.slug)}
        className="flex min-w-0 flex-1 cursor-pointer items-center gap-2 border-none bg-transparent p-0 text-left"
      >
        <span className="relative shrink-0">
          {logoUrl && !logoFailed ? (
            <img src={logoUrl} alt="" onError={() => setLogoFailed(true)} className="h-7 w-7 rounded-lg bg-surface-high object-contain p-1" />
          ) : (
            <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-surface-high text-xs">{recipe.icon || '◻'}</span>
          )}
          <span className={`absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full ring-2 ring-surface ${state.dot}`} />
        </span>
        <span className="flex min-w-0 flex-col leading-tight">
          <span className="truncate text-[12px] font-semibold text-text">{name}</span>
          <span className="flex min-w-0 items-center gap-1 font-label text-[10px] text-text-dim">
            <span className={`shrink-0 rounded px-1 py-px text-[9px] font-bold uppercase tracking-wide ${KIND[kind].className}`}>
              {KIND[kind].label}
            </span>
            {isDefault && <span className="shrink-0 font-bold text-primary">default</span>}
            <span className="truncate">{building ? 'building…' : !ready ? 'starting…' : detail}</span>
          </span>
        </span>
      </button>

      {/* Actions appear on hover so the resting tray reads as a list. */}
      <span className="hidden shrink-0 items-center gap-1 group-hover:flex">
        {kind === 'app' && ready && (
          <a
            href={openUrl(recipe)}
            target="_blank"
            rel="noreferrer"
            title={`Open ${recipe.name}`}
            className="rounded-md bg-primary px-1.5 py-0.5 font-label text-[10px] font-bold text-primary-on no-underline hover:opacity-90"
          >
            Open ↗
          </a>
        )}
        {!building && (
          <button
            type="button"
            onClick={() => stopRecipe(recipe.slug)}
            title={`Stop ${recipe.name}`}
            className="flex h-5 w-5 cursor-pointer items-center justify-center rounded-md border border-outline-dim bg-transparent text-[13px] leading-none text-text-dim hover:border-error hover:text-error"
          >
            ×
          </button>
        )}
      </span>
    </div>
  )
}

function MemoryGauge({ used, total }) {
  const pct = Math.min(100, Math.round((used / total) * 100))
  const color = pct >= 90 ? 'var(--error)' : pct >= 75 ? 'var(--warning)' : 'var(--tertiary)'
  return (
    <Link
      to="/system"
      title={`${used.toFixed(1)} of ${total.toFixed(0)} GB memory in use — open System Monitor`}
      className="ml-1 flex shrink-0 flex-col gap-1 rounded-xl px-2 py-1 no-underline hover:bg-surface-high"
    >
      <span className="font-label text-[10px] tabular-nums text-text-muted">
        <span className="font-bold text-text">{Math.round(used)}</span>/{Math.round(total)} GB
      </span>
      <span className="h-1 w-16 overflow-hidden rounded-full bg-surface-highest">
        <span className="block h-full rounded-full" style={{ width: `${pct}%`, background: color }} />
      </span>
    </Link>
  )
}
