import { useEffect, useMemo, useRef, useState } from 'react'
import { useStore } from '../store'
import { useAuth } from '../auth'
import { useApiKey } from '../hooks/useApiKey'
import { useThemedLogo } from '../hooks/useThemedLogo'
import { copyText } from '../lib/clipboard'
import { hasSecret, maskIn } from '../lib/secret'
import { hubOrigin } from '../lib/urls'
import { formatParams } from '../components/RecipeCard'
import { groupModels, isAgentModel, isMediaModel, speedLabel } from '../models'
import { formatContext } from '../components/ModelList'
import ModelShelves from '../components/ModelShelves'

// Everything an agent needs from this Spark, on one page: what is serving
// right now, the two ways to connect (sah, or plain OpenAI/Anthropic
// settings), and the catalog to pick from -- the language models an agent
// talks to, then the media models it calls as tools, kept apart. The page answers "how do I
// use these models with my agent" before it asks you to choose one.

const KIND_LABEL = {
  // "origin" is the address this page was actually loaded from. The daemon
  // only lists it when it is not one of the names the box can see on itself
  // -- i.e. when a tunnel or reverse proxy is in front.
  origin: 'This address',
  mdns: 'mDNS name',
  tailscale: 'Tailscale',
  ip: 'LAN IP',
}

// The Hub's MCP tools, as an agent sees them (daemon/routers/mcp.py).
const MCP_TOOLS = ['Web search', 'Web fetch', 'Images', 'Video', 'Music', 'Decisions', 'File links']

const AGENT_STORAGE_KEY = 'spark-ai-hub-agent'

function savedAgent() {
  try {
    return localStorage.getItem(AGENT_STORAGE_KEY)
  } catch {
    return null
  }
}

function saveAgent(id) {
  try {
    localStorage.setItem(AGENT_STORAGE_KEY, id)
  } catch {
    /* private window: the pick just isn't remembered */
  }
}

// What /v1/models answers right now: the running LLMs, default (largest)
// first. Refetched whenever the set of running models changes.
function useServedModels(runningKey) {
  const [served, setServed] = useState(null)
  useEffect(() => {
    let alive = true
    fetch('/v1/models')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => alive && setServed(d?.data || []))
      .catch(() => alive && setServed([]))
    return () => { alive = false }
  }, [runningKey])
  return served
}

function useConnectInfo() {
  const [info, setInfo] = useState(null)
  useEffect(() => {
    let alive = true
    fetch('/api/system/connect')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => alive && setInfo(d))
      .catch(() => {})
    return () => { alive = false }
  }, [])
  return info
}

export default function Agents({ search = '' }) {
  const recipes = useStore((s) => s.recipes)
  const authEnabled = useAuth((s) => s.authEnabled)
  const apiKey = useApiKey()
  const info = useConnectInfo()

  const models = useMemo(() => recipes.filter(isAgentModel), [recipes])
  const media = useMemo(() => recipes.filter(isMediaModel), [recipes])
  const matches = useMemo(() => {
    const q = search.toLowerCase()
    return (r) => !q || r.name.toLowerCase().includes(q) || r.tags.some((t) => t.includes(q))
  }, [search])
  const visible = useMemo(() => models.filter(matches), [models, matches])
  const mediaVisible = useMemo(() => media.filter(matches), [media, matches])

  const runningKey = models
    .filter((r) => r.running || r.starting)
    .map((r) => `${r.slug}:${r.ready ? 1 : 0}`)
    .sort()
    .join(',')
  const served = useServedModels(runningKey)
  const starting = models.filter((r) => r.starting || (r.running && !r.ready))

  // The key the snippets carry. Copy hands over the real one; the screen
  // shows it masked behind a Show.
  const key = apiKey || (authEnabled ? 'YOUR_HUB_API_KEY' : 'not-needed')
  const modelId = served?.[0]?.id || 'default'

  const tools = media.filter((r) => r.running || r.starting)

  const scrollToModels = () => document.getElementById('agent-models')?.scrollIntoView({ behavior: 'smooth' })

  return (
    <div className="pb-14 animate-fadeIn">
      {/* ─── Hero: what this is, and what is serving right now ─── */}
      <section className="relative overflow-hidden border-b border-outline-dim">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 opacity-70"
          style={{
            background: 'radial-gradient(60% 90% at 0% 0%, color-mix(in srgb, var(--secondary) 22%, transparent), transparent 70%),'
              + 'radial-gradient(50% 80% at 100% 100%, color-mix(in srgb, var(--primary) 16%, transparent), transparent 70%)',
          }}
        />
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 opacity-[0.35]"
          style={{
            backgroundImage: 'linear-gradient(var(--outline-dim) 1px, transparent 1px), linear-gradient(90deg, var(--outline-dim) 1px, transparent 1px)',
            backgroundSize: '32px 32px',
            maskImage: 'linear-gradient(to bottom, black, transparent 85%)',
            WebkitMaskImage: 'linear-gradient(to bottom, black, transparent 85%)',
          }}
        />
        <div className="relative grid grid-cols-1 items-start gap-6 px-4 pb-8 pt-7 sm:px-6 sm:pt-9 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)]">
          <div className="min-w-0">
            <span className="inline-flex items-center gap-2 rounded-full border border-primary/30 bg-primary/10 px-3 py-1 font-label text-[11px] font-bold uppercase tracking-[0.14em] text-primary">
              <AgentIcon className="h-3.5 w-3.5" />
              Agents
            </span>
            <h1 className="m-0 mt-4 font-display text-[clamp(28px,3.4vw,44px)] font-bold leading-[1.08] tracking-tight text-text">
              Your Spark, behind
              <br />
              <span className="bg-gradient-to-r from-primary to-tertiary bg-clip-text text-transparent">every coding agent.</span>
            </h1>
            <p className="m-0 mt-4 max-w-xl text-[15px] leading-7 text-text-muted">
              Every model here speaks the OpenAI and Anthropic APIs at one address, signed with
              your key. Wire an agent with a single <code className="font-mono text-text">sah</code>{' '}
              command, or paste three settings into anything else. Launch a different model and
              connected agents pick it up without being touched.
            </p>
          </div>

          <ServingPanel
            served={served}
            starting={starting}
            tools={tools}
            recipes={recipes}
            onBrowse={scrollToModels}
          />
        </div>
      </section>

      {/* ─── Connect: one question, then the steps for the answer ─── */}
      <ConnectAgent info={info} apiKey={apiKey} keyValue={key} modelId={modelId} hasModel={Boolean(served?.length)} />

      {/* ─── The catalog: what agents talk to, then what they call ─── */}
      <CatalogNav
        sections={[
          { id: 'agent-models', label: 'Language models', count: groupModels(visible, () => 0).length, where: '/v1' },
          { id: 'media-models', label: 'Media models', count: groupModels(mediaVisible, () => 0).length, where: '/mcp' },
        ]}
      />
      <div className="pt-8">
        <ModelShelves
          id="agent-models"
          models={models}
          visible={visible}
          eyebrow="The brain · /v1"
          title="Language models"
        />
      </div>
      <div className="mt-14 border-t border-outline-dim pt-10">
        <ModelShelves
          id="media-models"
          models={media}
          visible={mediaVisible}
          eyebrow="The tools · /mcp"
          title="Media models"
          subtitle="Image, video, music and decision models. Agents call them through the Hub's MCP tools, and can start them on demand."
          sorts={['lab', 'release', 'params', 'size']}
        />
      </div>
    </div>
  )
}

/* ─── Serving now ─── */

function ServingPanel({ served, starting, tools, recipes, onBrowse }) {
  const bySlug = useMemo(() => new Map(recipes.map((r) => [r.slug, r])), [recipes])
  const live = served || []
  const liveSlugs = new Set(live.map((m) => m.recipe))
  const pending = starting.filter((r) => !liveSlugs.has(r.slug))

  return (
    <div className="rounded-2xl border border-outline-dim bg-surface/80 p-4 shadow-xl shadow-black/10 backdrop-blur-md">
      <div className="mb-3 flex items-center gap-2">
        <span className="relative flex h-2.5 w-2.5">
          {live.length > 0 && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-60" />}
          <span className={`relative inline-flex h-2.5 w-2.5 rounded-full ${live.length > 0 ? 'bg-primary' : 'bg-text-dim'}`} />
        </span>
        <h2 className="m-0 font-display text-sm font-bold tracking-tight text-text">Serving now</h2>
        <code className="ml-auto rounded-md bg-surface-high px-2 py-0.5 font-mono text-[11px] text-text-muted">/v1</code>
      </div>

      {served === null ? (
        <p className="m-0 py-6 text-center text-sm text-text-dim">Asking the Hub…</p>
      ) : live.length === 0 && pending.length === 0 ? (
        <div className="rounded-xl border border-dashed border-outline px-4 py-6 text-center">
          <p className="m-0 font-display text-sm font-semibold text-text">No model is running yet</p>
          <p className="m-0 mx-auto mt-1 max-w-sm text-xs leading-5 text-text-dim">
            Launch one below. Agents you have already connected pick it up as it comes up,
            with nothing to change on their side.
          </p>
          <button
            onClick={onBrowse}
            className="mt-4 cursor-pointer rounded-xl border-none bg-primary px-4 py-2 text-xs font-bold text-primary-on transition-opacity hover:opacity-90"
          >
            Choose a model ↓
          </button>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          {live.map((m, i) => (
            <ServedRow key={m.id} model={m} recipe={bySlug.get(m.recipe)} isDefault={i === 0} />
          ))}
          {pending.map((r) => (
            <ServedRow key={r.slug} recipe={r} pending />
          ))}
          <p className="m-0 mt-1 text-[11px] leading-5 text-text-dim">
            {live.length > 1
              ? 'A request goes to the model it names. Anything else goes to the default, the largest one up.'
              : 'Requests go here whatever model they name. Launch more and they run side by side when they fit.'}
          </p>
        </div>
      )}

      {tools.length > 0 && <ToolModels tools={tools} />}
    </div>
  )
}

// Media models that are up, so an agent's image/video/music tool calls will
// not have to wait for one to start.
function ToolModels({ tools }) {
  const selectRecipe = useStore((s) => s.selectRecipe)
  return (
    <div className="mt-3 border-t border-outline-dim pt-3">
      <div className="mb-2 flex items-center gap-2">
        <span className="font-label text-[10px] font-bold uppercase tracking-[0.14em] text-text-muted">Ready as tools</span>
        <code className="ml-auto rounded-md bg-surface-high px-2 py-0.5 font-mono text-[11px] text-text-muted">/mcp</code>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {tools.map((r) => (
          <button
            key={r.slug}
            type="button"
            onClick={() => selectRecipe(r.slug)}
            className="flex cursor-pointer items-center gap-1.5 rounded-full border border-outline-dim bg-surface-high/60 px-2.5 py-1 text-[11px] font-semibold text-text-muted transition-colors hover:border-text-dim hover:text-text"
          >
            <span className={`h-1.5 w-1.5 rounded-full ${r.running && r.ready ? 'bg-primary' : 'bg-warning animate-pulse'}`} />
            {r.name}
          </button>
        ))}
      </div>
    </div>
  )
}

// Two catalogs on one page: a bar that stays put while you scroll and says
// which one you are in.
function CatalogNav({ sections }) {
  const [active, setActive] = useState(sections[0].id)
  const ids = sections.map((x) => x.id).join()
  useEffect(() => {
    const order = ids.split(',')
    const els = order.map((id) => document.getElementById(id)).filter(Boolean)
    if (!els.length || typeof IntersectionObserver === 'undefined') return
    const seen = new Map()
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) seen.set(e.target.id, e.isIntersecting)
      const current = order.filter((id) => seen.get(id)).pop()
      if (current) setActive(current)
    }, { rootMargin: '-15% 0px -70% 0px' })
    els.forEach((el) => io.observe(el))
    return () => io.disconnect()
  }, [ids])

  return (
    <nav className="sticky top-0 z-20 mt-10 border-y border-outline-dim bg-bg/85 px-4 py-2.5 backdrop-blur-md sm:px-6">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="mr-1 hidden font-label text-[10px] font-bold uppercase tracking-[0.14em] text-text-dim sm:inline">Catalog</span>
        {sections.map((x) => (
          <button
            key={x.id}
            type="button"
            onClick={() => document.getElementById(x.id)?.scrollIntoView({ behavior: 'smooth' })}
            className={`flex cursor-pointer items-center gap-2 rounded-xl border px-3 py-1.5 text-xs font-semibold transition-all ${
              active === x.id
                ? 'border-primary/40 bg-primary/10 text-text'
                : 'border-transparent bg-transparent text-text-muted hover:text-text'
            }`}
          >
            {x.label}
            <span className="rounded-md bg-surface-high px-1.5 py-0.5 font-label text-[10px] tabular-nums text-text-dim">{x.count}</span>
            <code className="hidden font-mono text-[10px] text-text-dim sm:inline">{x.where}</code>
          </button>
        ))}
      </div>
    </nav>
  )
}

const CAPABILITY_LABEL = { tools: 'Tools', vision: 'Vision', thinking: 'Thinking', video: 'Video' }

function ServedRow({ model, recipe, isDefault = false, pending = false }) {
  const selectRecipe = useStore((s) => s.selectRecipe)
  const logoUrl = useThemedLogo(recipe?.logo)
  const [logoFailed, setLogoFailed] = useState(false)
  const caps = (model?.capabilities || []).filter((c) => CAPABILITY_LABEL[c])
  const meta = [
    recipe?.params_b != null && formatParams(recipe),
    recipe && speedLabel(recipe),
    model?.max_model_len && `${formatContext(model.max_model_len)} ctx`,
  ].filter(Boolean)

  return (
    <button
      type="button"
      onClick={() => recipe && selectRecipe(recipe.slug)}
      className={`flex w-full cursor-pointer items-center gap-3 rounded-xl border bg-surface-high/60 px-3 py-2.5 text-left transition-colors hover:border-text-dim ${
        isDefault ? 'border-primary/40' : 'border-outline-dim'
      }`}
    >
      {logoUrl && !logoFailed ? (
        <img src={logoUrl} alt="" onError={() => setLogoFailed(true)} className="h-8 w-8 shrink-0 rounded-lg bg-surface p-1 object-contain" />
      ) : (
        <span className="h-8 w-8 shrink-0 rounded-lg bg-surface" />
      )}
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-semibold text-text">{recipe?.name || model?.id}</span>
          {isDefault && (
            <span className="shrink-0 rounded bg-primary px-1.5 py-0.5 font-label text-[9px] font-bold uppercase tracking-wide text-primary-on">
              Default
            </span>
          )}
          {pending && (
            <span className="shrink-0 rounded bg-warning/15 px-1.5 py-0.5 font-label text-[9px] font-bold uppercase tracking-wide text-warning">
              Starting
            </span>
          )}
        </div>
        <div className="mt-0.5 truncate font-mono text-[11px] text-text-dim">{model?.id || recipe?.slug}</div>
      </div>
      <div className="hidden shrink-0 flex-col items-end gap-1 sm:flex">
        {meta.length > 0 && <span className="font-label text-[10px] tabular-nums text-text-muted">{meta.join(' · ')}</span>}
        {caps.length > 0 && (
          <span className="flex gap-1">
            {caps.map((c) => (
              <span key={c} className="rounded-full bg-surface px-1.5 py-0.5 font-label text-[9px] text-text-muted">
                {CAPABILITY_LABEL[c]}
              </span>
            ))}
          </span>
        )}
      </div>
    </button>
  )
}

/* ─── Way 1: sah ─── */

// One question first -- which agent do you use? -- answered by picking its
// logo, then numbered steps for that one agent and nothing else. Two cards of
// small print (a CLI way and a manual way, each with sub-steps) made people
// read everything to find the two lines that applied to them. The manual
// OpenAI-compatible setup is just one more answer, "Any other app", and so is
// the Hub's MCP server for apps that only want its tools.

const OTHER = { id: 'other', name: 'Any other app', kind: 'Other' }
const MCP = { id: 'mcp', name: 'MCP tools', kind: 'Other' }
const EXTRAS = [OTHER, MCP]

function ConnectAgent({ info, apiKey, keyValue, modelId, hasModel }) {
  const agents = info?.agents || []
  const [picked, setPicked] = useState(savedAgent)
  const choice = EXTRAS.find((x) => x.id === picked)
    || agents.find((a) => a.id === picked) || agents.find((a) => a.id === 'claude') || agents[0]
  const stepsRef = useRef(null)

  const pick = (id) => {
    setPicked(id)
    saveAgent(id)
    // Stacked layout: the steps are below the tiles, so bring them into view.
    if (window.matchMedia('(max-width: 1023px)').matches) {
      requestAnimationFrame(() => stepsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
    }
  }

  const terminal = agents.filter((a) => a.kind !== 'Desktop')
  const desktop = agents.filter((a) => a.kind === 'Desktop')

  return (
    <section className="px-4 pt-10 sm:px-6">
      <div className="font-label text-[11px] font-bold uppercase tracking-[0.14em] text-primary">Connect an agent</div>
      <h2 className="m-0 mt-1 font-display text-2xl font-bold tracking-tight text-text sm:text-3xl">
        Which agent do you want to use?
      </h2>

      {!info ? (
        <p className="m-0 mt-6 text-sm text-text-dim">Loading…</p>
      ) : (
        <div className="mt-6 grid grid-cols-1 items-start gap-6 lg:grid-cols-2">
          <div className="flex flex-col gap-5">
            <TileGroup label="In the terminal" agents={terminal} selected={choice?.id} onPick={pick} />
            {desktop.length > 0 && (
              <TileGroup label="Desktop apps" agents={desktop} selected={choice?.id} onPick={pick} />
            )}
            <TileGroup label="Something else" agents={EXTRAS} selected={choice?.id} onPick={pick} />
          </div>

          <div ref={stepsRef} className="scroll-mt-4 lg:sticky lg:top-4">
            {choice?.id === OTHER.id ? (
              <OtherAppSteps info={info} apiKey={apiKey} keyValue={keyValue} modelId={modelId} hasModel={hasModel} />
            ) : choice?.id === MCP.id ? (
              <McpSteps info={info} apiKey={apiKey} keyValue={keyValue} />
            ) : choice ? (
              <SahSteps agent={choice} info={info} apiKey={apiKey} />
            ) : null}
          </div>
        </div>
      )}
    </section>
  )
}

function TileGroup({ label, agents, selected, onPick }) {
  return (
    <div>
      <div className="mb-2 text-sm font-semibold text-text-muted">{label}</div>
      <div className="grid grid-cols-3 gap-2 sm:grid-cols-4 xl:grid-cols-5">
        {agents.map((a) => (
          <button
            key={a.id}
            type="button"
            onClick={() => onPick(a.id)}
            aria-pressed={selected === a.id}
            className={`flex h-[96px] cursor-pointer flex-col items-center justify-center gap-2 rounded-2xl border px-2 text-center transition-all ${
              selected === a.id
                ? 'border-primary bg-primary/10 ring-2 ring-primary/40'
                : 'border-outline-dim bg-surface hover:border-text-dim hover:bg-surface-high'
            }`}
          >
            <AgentLogo agent={a} size={36} />
            <span className={`line-clamp-2 text-[12px] font-semibold leading-tight ${selected === a.id ? 'text-text' : 'text-text-muted'}`}>
              {a.name}
            </span>
          </button>
        ))}
      </div>
    </div>
  )
}

function StepsCard({ agent, title, children }) {
  return (
    <div className="rounded-2xl border border-outline-dim bg-surface p-5 sm:p-6">
      <div className="mb-6 flex items-center gap-3">
        <AgentLogo agent={agent} size={44} />
        <h3 className="m-0 font-display text-xl font-bold tracking-tight text-text">{title}</h3>
      </div>
      <ol className="m-0 flex list-none flex-col gap-6 p-0">{children}</ol>
    </div>
  )
}

function BigStep({ n, title, sub, children }) {
  const done = n === '✓'
  return (
    <li className="flex gap-4">
      <span
        className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full font-display text-sm font-bold ${
          done ? 'bg-primary text-primary-on' : 'bg-primary/15 text-primary ring-1 ring-primary/40'
        }`}
      >
        {n}
      </span>
      <div className="min-w-0 flex-1 pt-0.5">
        <div className="text-base font-semibold text-text">{title}</div>
        {sub && <p className="m-0 mt-0.5 text-sm leading-6 text-text-muted">{sub}</p>}
        {children && <div className="mt-3">{children}</div>}
      </div>
    </li>
  )
}

function SahSteps({ agent, info, apiKey }) {
  const isDesktop = agent.kind === 'Desktop'
  const installOnly = agent.command.endsWith('--install')
  return (
    <StepsCard agent={agent} title={`Use ${agent.name} with this Spark`}>
      <BigStep n="1" title="Install sah on your computer" sub="Only once per computer. sah is the small helper that connects your agents to this Spark.">
        <CopyCode text={info.commands.install} secret={apiKey} large />
        <p className="m-0 mt-2 text-xs leading-5 text-text-dim">
          It includes your personal key, so run it only on your own computers.
        </p>
        <details className="group mt-2">
          <summary className="cursor-pointer list-none text-xs font-semibold text-text-dim hover:text-text">
            <span className="inline-block transition-transform group-open:rotate-90">›</span>{' '}
            Already have sah? Point it at this Spark instead
          </summary>
          <div className="mt-2 flex flex-col gap-2">
            <CopyCode text={info.commands.set_hub} />
            {info.commands.set_key && <CopyCode text={info.commands.set_key} secret={apiKey} />}
          </div>
        </details>
      </BigStep>

      <BigStep
        n="2"
        title={installOnly ? `Connect ${agent.name}` : `Start ${agent.name}`}
        sub={installOnly
          ? `Run this once, then quit and reopen ${agent.name}.`
          : `Run this whenever you want to use ${agent.name}${isDesktop ? '' : ' — in any folder you want it to work in'}.`}
      >
        <CopyCode text={agent.command} large />
      </BigStep>

      <BigStep
        n="✓"
        title="That's it"
        sub={`${agent.name} now runs on the models of this Spark. Every running model shows up in ${agent.name}'s own model menu, the largest first.`}
      >
        <p className="m-0 text-xs leading-5 text-text-dim">{agentNote(agent)}</p>
      </BigStep>
    </StepsCard>
  )
}

function agentNote(agent) {
  const restore = `sah ${agent.id} --restore`
  if (agent.command.endsWith('--install') || agent.writes_config) {
    return `sah adds this Spark to ${agent.name}'s settings; ${restore} takes it back out.`
  }
  return `Nothing in ${agent.name}'s own settings is changed: quit it and it is back to normal.`
}

// Each vendor's own mark, from its own site (public/logos/agents/), or the
// lab's logo the Hub already ships. `fill`: the icon carries its own
// background and takes the whole chip; otherwise it sits padded on white.
const AGENT_LOGOS = {
  // The Model Context Protocol's own mark (modelcontextprotocol.io favicon).
  mcp: { src: '/logos/agents/mcp.png', fill: true },
  codex: { src: '/logos/openai.png' },
  chatgpt: { src: '/logos/openai.png' },
  qwen: { src: '/logos/qwen.png' },
  dsh: { src: '/logos/deepseek.png' },
  muse: { src: '/logos/meta.png' },
  pool: { src: '/logos/poolside.png' },
  hermes: { src: '/logos/agents/hermes.png', fill: true },
  'hermes-desktop': { src: '/logos/agents/hermes.png', fill: true },
  openclaw: { src: '/logos/agents/openclaw.png' },
  opencode: { src: '/logos/agents/opencode.png', fill: true },
  'opencode-desktop': { src: '/logos/agents/opencode.png', fill: true },
  omp: { src: '/logos/agents/omp.png', fill: true },
  cline: { src: '/logos/agents/cline.png' },
  droid: { src: '/logos/agents/droid.png', fill: true },
  pi: { src: '/logos/agents/pi.png' },
  kimi: { src: '/logos/agents/kimi.png', fill: true },
}

function AgentLogo({ agent, size = 20 }) {
  const box = { width: size, height: size }
  const inner = { width: Math.round(size * 0.66), height: Math.round(size * 0.66) }
  const shell = `flex shrink-0 items-center justify-center overflow-hidden ${size >= 32 ? 'rounded-xl' : 'rounded-md'}`
  if (agent.id === OTHER.id) {
    return (
      <span className={`${shell} bg-surface-highest text-text-muted`} style={box}>
        <PlugIcon style={inner} />
      </span>
    )
  }
  if (agent.id.startsWith('claude')) {
    return <span className={`${shell} bg-white`} style={box}><ClaudeMark style={inner} className="text-[#D97757]" /></span>
  }
  if (agent.id === 'copilot') {
    return <span className={`${shell} bg-white`} style={box}><GitHubMark style={inner} className="text-[#1F2328]" /></span>
  }
  const logo = AGENT_LOGOS[agent.id]
  if (logo?.fill) {
    return <span className={shell} style={box}><img src={logo.src} alt="" className="h-full w-full object-cover" /></span>
  }
  if (logo) {
    return <span className={`${shell} bg-white`} style={box}><img src={logo.src} alt="" style={inner} className="object-contain" /></span>
  }
  // A client sah gains later shows its initial until its mark is added.
  return (
    <span className={`${shell} bg-surface-highest font-display font-bold text-text`} style={{ ...box, fontSize: size * 0.45 }}>
      {agent.name[0]}
    </span>
  )
}

/* ─── Way 2: plain OpenAI / Anthropic settings ─── */

const SNIPPETS = [
  {
    id: 'curl',
    label: 'curl',
    body: ({ base, key, model }) => `curl ${base}/v1/chat/completions \\
  -H "Authorization: Bearer ${key}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "${model}",
    "messages": [{"role": "user", "content": "Hello from my agent"}]
  }'`,
  },
  {
    id: 'python',
    label: 'Python',
    body: ({ base, key, model }) => `from openai import OpenAI

client = OpenAI(base_url="${base}/v1", api_key="${key}")
reply = client.chat.completions.create(
    model="${model}",
    messages=[{"role": "user", "content": "Hello from my agent"}],
)
print(reply.choices[0].message.content)`,
  },
  {
    id: 'env',
    label: 'Env vars',
    body: ({ base, key }) => `# OpenAI-compatible clients
export OPENAI_BASE_URL=${base}/v1
export OPENAI_API_KEY=${key}

# Anthropic-compatible clients (Messages API)
export ANTHROPIC_BASE_URL=${base}
export ANTHROPIC_AUTH_TOKEN=${key}`,
  },
]

const mcpJson = ({ base, key }) => `{
  "mcpServers": {
    "sah": {
      "type": "http",
      "url": "${base}/mcp",
      "headers": { "Authorization": "Bearer ${key}" }
    }
  }
}`

// Where this Spark can be reached. The page's own origin first: it is the one
// address known to work from where you are sitting. The others are what the
// Spark answers on besides.
function useAddresses(info) {
  const origin = hubOrigin()
  const addresses = useMemo(() => {
    const out = [{ url: origin, label: 'This page', note: 'The address you opened the Hub on.' }]
    for (const c of info?.candidates || []) {
      if (out.some((a) => a.url === c.url)) continue
      out.push({ url: c.url, label: KIND_LABEL[c.kind] || c.kind, note: c.note })
    }
    return out
  }, [info, origin])
  const [base, setBase] = useState(origin)
  return [addresses, addresses.find((a) => a.url === base) || addresses[0], setBase]
}

function AddressPicker({ addresses, address, onPick }) {
  if (addresses.length < 2) return null
  return (
    <div className="mt-3 flex flex-wrap items-center gap-2">
      <span className="text-xs text-text-dim">Using it from somewhere else? Address:</span>
      <div className="flex flex-wrap gap-1 rounded-xl border border-outline-dim bg-surface-high p-0.5">
        {addresses.map((a) => (
          <button
            key={a.url}
            type="button"
            onClick={() => onPick(a.url)}
            title={a.note}
            className={`cursor-pointer rounded-lg px-2.5 py-1 text-[11px] font-semibold transition-all ${
              address.url === a.url ? 'bg-primary text-primary-on' : 'bg-transparent text-text-muted hover:text-text'
            }`}
          >
            {a.label}
          </button>
        ))}
      </div>
    </div>
  )
}

// One-click installs for the MCP clients that take one. Every link is the
// app's own URL scheme, so the config -- key included -- goes straight from
// this page to the app on this computer. The https "redirect" variants some of
// them also offer (vscode.dev, cursor.com) would carry the key through a third
// party's server, so they are not used.
//   Cursor     cursor.com/docs/mcp/install-links   base64 of the bare server object
//   VS Code    VS Code MCP developer guide          URL-encoded JSON with name + type
//   LM Studio  lmstudio.ai/docs/app/mcp/deeplink    base64 of the bare server object
//   Goose      block/goose deeplink.ts              url + header=KEY=VALUE params
const b64 = (obj) => encodeURIComponent(btoa(JSON.stringify(obj)))
const MCP_CLIENTS = [
  {
    id: 'cursor',
    name: 'Cursor',
    link: ({ url, key }) => `cursor://anysphere.cursor-deeplink/mcp/install?name=sah&config=${
      b64({ url, headers: { Authorization: `Bearer ${key}` } })}`,
  },
  {
    id: 'vscode',
    name: 'VS Code',
    link: ({ url, key }) => `vscode:mcp/install?${encodeURIComponent(JSON.stringify({
      name: 'sah', type: 'http', url, headers: { Authorization: `Bearer ${key}` },
    }))}`,
  },
  {
    id: 'lmstudio',
    name: 'LM Studio',
    link: ({ url, key }) => `lmstudio://add_mcp?name=sah&config=${
      b64({ url, headers: { Authorization: `Bearer ${key}` } })}`,
  },
  {
    id: 'goose',
    name: 'Goose',
    link: ({ url, key }) => `goose://extension?name=${encodeURIComponent('Spark AI Hub')}`
      + `&url=${encodeURIComponent(url)}&timeout=900`
      + `&header=${encodeURIComponent(`Authorization=Bearer ${key}`)}`,
  },
]

// Claude's connectors are called from Anthropic's cloud, so its link only works
// with an address the internet can reach over HTTPS -- the Hub's tunnel, when
// the page was opened through it. The link carries only the name and URL and
// no key: Claude signs in through the Hub's OAuth (routers/oauth.py), and the
// person approves it on the Hub's "Allow Claude?" page.
function claudeConnectorLink(url) {
  return 'https://claude.ai/customize/connectors?modal=add-custom-connector'
    + `&connectorName=${encodeURIComponent('Spark AI Hub')}&connectorUrl=${encodeURIComponent(url)}`
}

function McpSteps({ info, apiKey, keyValue }) {
  const [addresses, address, setBase] = useAddresses(info)
  const [opened, setOpened] = useState(null)
  const mcpUrl = `${address.url}/mcp`
  const publicUrl = info?.external_origin && window.location.protocol === 'https:'
    ? `${hubOrigin()}/mcp`
    : null

  const open = (client) => {
    setOpened(client.id)
    // Navigated from code rather than an <a href>, so the key-bearing link
    // never shows in the browser's status bar.
    window.location.assign(client.link({ url: mcpUrl, key: keyValue }))
  }

  return (
    <StepsCard agent={MCP} title="Add the Hub's tools to your app">
      <BigStep n="1" title="One click, if your app is here" sub="It opens the app with everything filled in, your key included.">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          {MCP_CLIENTS.map((c) => (
            <button
              key={c.id}
              type="button"
              onClick={() => open(c)}
              className={`flex cursor-pointer items-center gap-2.5 rounded-xl border px-3 py-2.5 text-left text-sm font-semibold transition-all ${
                opened === c.id
                  ? 'border-primary bg-primary/10 text-text'
                  : 'border-outline-dim bg-surface-high/50 text-text-muted hover:border-text-dim hover:text-text'
              }`}
            >
              <img src={`/logos/agents/${c.id}.png`} alt="" className="h-7 w-7 shrink-0 rounded-lg object-contain" />
              <span className="min-w-0">
                <span className="block truncate">{c.name}</span>
                <span className="block text-[11px] font-normal text-text-dim">Add to {c.name}</span>
              </span>
            </button>
          ))}
          <button
            type="button"
            disabled={!publicUrl}
            onClick={() => { setOpened('claude'); window.open(claudeConnectorLink(publicUrl), '_blank', 'noopener') }}
            className={`flex items-center gap-2.5 rounded-xl border px-3 py-2.5 text-left text-sm font-semibold transition-all ${
              !publicUrl
                ? 'cursor-not-allowed border-outline-dim bg-transparent text-text-dim opacity-60'
                : opened === 'claude'
                  ? 'cursor-pointer border-primary bg-primary/10 text-text'
                  : 'cursor-pointer border-outline-dim bg-surface-high/50 text-text-muted hover:border-text-dim hover:text-text'
            }`}
          >
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-white">
              <ClaudeMark style={{ width: 18, height: 18 }} className="text-[#D97757]" />
            </span>
            <span className="min-w-0">
              <span className="block truncate">Claude</span>
              <span className="block text-[11px] font-normal text-text-dim">{publicUrl ? 'Add connector' : 'Needs public HTTPS'}</span>
            </span>
          </button>
        </div>

        {opened && opened !== 'claude' && (
          <p className="m-0 mt-3 text-xs leading-5 text-text-dim">
            Nothing happened? The app isn't installed on this computer, or it is too old for
            one-click installs. Add it by hand below.
          </p>
        )}
        {opened === 'claude' && publicUrl && (
          <div className="mt-3 rounded-xl border border-outline-dim bg-surface-high/40 p-3">
            <div className="text-sm font-semibold text-text">In Claude: Add, then Connect</div>
            <p className="m-0 mt-1 text-xs leading-5 text-text-muted">
              Keep the form's defaults and click <strong>Add</strong>, then <strong>Connect</strong>.
              Claude opens this Hub: sign in if asked and click <strong>Allow</strong>. No key to
              paste. It works in Claude Desktop too, on the same account.
            </p>
          </div>
        )}
        {!publicUrl && (
          <p className="m-0 mt-3 text-xs leading-5 text-text-dim">
            Claude connects from Anthropic's cloud, so it needs the Hub's public HTTPS address.
            Open this page through that address to add it.
          </p>
        )}
      </BigStep>

      <BigStep n="2" title="Any other app: add it by hand" sub="In its “MCP servers”, “Connectors” or “Tools” settings.">
        <div className="overflow-hidden rounded-xl border border-outline-dim">
          <SettingRow label="URL" value={mcpUrl} />
          <SettingRow label="Header" value={`Authorization: Bearer ${keyValue}`} secret={apiKey} />
          <SettingRow label="Transport" value="Streamable HTTP" />
        </div>
        <AddressPicker addresses={addresses} address={address} onPick={setBase} />
        <div className="mt-4 mb-2 text-xs font-semibold text-text-muted">Or paste it as JSON</div>
        <CopyCode text={mcpJson({ base: address.url, key: keyValue })} secret={apiKey} multiline />
      </BigStep>

      <BigStep n="✓" title="That's it" sub="Your app can now use:">
        <div className="flex flex-wrap gap-1.5">
          {MCP_TOOLS.map((t) => (
            <span key={t} className="rounded-full bg-surface-high px-3 py-1 text-xs font-semibold text-text-muted">{t}</span>
          ))}
        </div>
        <p className="m-0 mt-3 text-xs leading-5 text-text-dim">
          Agents set up with sah already have these. Give tool calls at least 900 seconds: a video render takes minutes.
        </p>
      </BigStep>
    </StepsCard>
  )
}

function OtherAppSteps({ info, apiKey, keyValue, modelId, hasModel }) {
  const [addresses, address, setBase] = useAddresses(info)
  const [snippetId, setSnippetId] = useState('curl')
  const snippet = SNIPPETS.find((s) => s.id === snippetId)
  const values = { base: address.url, key: keyValue, model: modelId }

  return (
    <StepsCard agent={OTHER} title="Use any OpenAI-compatible app">
      <BigStep
        n="1"
        title="Open your app's model settings"
        sub="Look for “OpenAI-compatible”, “Custom provider” or “Base URL”. No installer needed."
      />

      <BigStep n="2" title="Paste these three settings">
        <div className="overflow-hidden rounded-xl border border-outline-dim">
          <SettingRow label="Base URL" value={`${address.url}/v1`} />
          <SettingRow label="API key" value={keyValue} secret={apiKey} />
          <SettingRow
            label="Model"
            value={modelId}
            hint={hasModel ? 'The model running now.' : 'Any name works: the Hub uses the largest model running.'}
          />
        </div>
        <AddressPicker addresses={addresses} address={address} onPick={setBase} />
      </BigStep>

      <BigStep n="✓" title="That's it" sub="The app now talks to the models running on this Spark.">
        <details className="group">
          <summary className="cursor-pointer list-none text-xs font-semibold text-text-dim hover:text-text">
            <span className="inline-block transition-transform group-open:rotate-90">›</span>{' '}
            More: Anthropic apps and code examples
          </summary>
          <div className="mt-3 overflow-hidden rounded-xl border border-outline-dim">
            <SettingRow label="Anthropic" value={address.url} hint="Base URL for apps that speak Anthropic's Messages API." />
          </div>
          <div className="mt-3 mb-2 flex flex-wrap gap-1">
            {SNIPPETS.map((s) => (
              <button
                key={s.id}
                type="button"
                onClick={() => setSnippetId(s.id)}
                className={`cursor-pointer rounded-lg border px-2.5 py-1 text-[11px] font-semibold transition-all ${
                  snippetId === s.id
                    ? 'border-text-dim bg-surface-highest text-text'
                    : 'border-transparent bg-transparent text-text-dim hover:text-text'
                }`}
              >
                {s.label}
              </button>
            ))}
          </div>
          <CopyCode text={snippet.body(values)} secret={apiKey} multiline />
        </details>
      </BigStep>
    </StepsCard>
  )
}

function SettingRow({ label, value, secret, hint }) {
  return (
    <div className="flex items-start gap-3 border-b border-outline-dim bg-surface-high/40 px-3 py-3 last:border-b-0">
      <span className="w-20 shrink-0 pt-1 text-xs font-semibold text-text-muted">{label}</span>
      <div className="min-w-0 flex-1">
        <CopyCode text={value} secret={secret} bare />
        {hint && <p className="m-0 mt-1 text-xs leading-5 text-text-dim">{hint}</p>}
      </div>
    </div>
  )
}

// Copy-to-clipboard code with the viewer's key masked on screen. Copy always
// takes the real text; if the clipboard is unavailable the text is revealed
// and selected so Ctrl/Cmd+C hands over the working value, not the dots.
function CopyCode({ text, secret, multiline = false, bare = false, large = false }) {
  const [state, setState] = useState('idle') // idle | copied | failed
  const [shown, setShown] = useState(false)
  const codeRef = useRef(null)
  const carries = hasSecret(text, secret)
  const display = maskIn(text, secret, shown)

  const copy = async () => {
    if (await copyText(text)) {
      setState('copied')
      setTimeout(() => setState('idle'), 1500)
      return
    }
    setShown(true)
    const el = codeRef.current
    if (el) {
      const range = document.createRange()
      range.selectNodeContents(el)
      const sel = window.getSelection()
      sel.removeAllRanges()
      sel.addRange(range)
    }
    setState('failed')
    setTimeout(() => setState('idle'), 3000)
  }

  const label = state === 'copied' ? 'Copied' : state === 'failed' ? '⌘/Ctrl+C' : 'Copy'
  const small = 'shrink-0 cursor-pointer rounded-lg border px-2 py-1 font-label text-[10px] font-semibold transition-colors'

  if (bare) {
    return (
      <div className="flex items-center gap-1.5">
        <code ref={codeRef} className="min-w-0 flex-1 truncate font-mono text-[13px] text-text" title={shown || !carries ? text : undefined}>
          {display}
        </code>
        {carries && (
          <button onClick={() => setShown(!shown)} className={`${small} border-outline-dim bg-transparent text-text-dim hover:text-primary`}>
            {shown ? 'Hide' : 'Show'}
          </button>
        )}
        <button onClick={copy} className={`${small} border-outline-dim bg-surface text-text-muted hover:border-primary hover:text-primary`}>
          {label}
        </button>
      </div>
    )
  }

  return (
    <div className={`relative rounded-xl border border-outline-dim bg-surface-low ${multiline ? 'flex flex-col-reverse sm:block' : 'flex items-center'}`}>
      <code
        ref={codeRef}
        className={`block min-w-0 flex-1 overflow-x-auto px-3 font-mono text-text ${large ? 'py-3.5 text-sm' : 'py-2.5 text-xs'} ${
          multiline ? 'whitespace-pre leading-5 sm:pr-28' : 'whitespace-nowrap'
        }`}
      >
        {display}
      </code>
      <div className={`flex shrink-0 gap-1.5 ${multiline ? 'justify-end border-b border-outline-dim px-2 py-1.5 sm:absolute sm:right-2 sm:top-2 sm:border-0 sm:p-0' : 'pr-2'}`}>
        {carries && (
          <button
            onClick={() => setShown(!shown)}
            title={shown ? 'Hide your API key' : 'Reveal your API key'}
            className={`${small} border-outline-dim bg-surface-low text-text-dim hover:text-primary`}
          >
            {shown ? 'Hide' : 'Show'}
          </button>
        )}
        <button onClick={copy} className={`${small} border-transparent bg-primary text-primary-on hover:opacity-90 ${large ? 'px-3 py-1.5 text-xs' : ''}`}>
          {label}
        </button>
      </div>
    </div>
  )
}

/* ─── Marks ─── */

export function AgentIcon({ className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="4" y="7" width="16" height="12" rx="3" />
      <path d="M12 3v4" />
      <circle cx="12" cy="3" r="0.5" fill="currentColor" />
      <path d="M9 12v1.5M15 12v1.5" />
      <path d="M1.5 12v3M22.5 12v3" />
    </svg>
  )
}

function PlugIcon({ style }) {
  return (
    <svg style={style} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-label="Any app">
      <path d="M9 2v6M15 2v6" />
      <path d="M6 8h12v4a6 6 0 0 1-12 0z" />
      <path d="M12 18v4" />
    </svg>
  )
}

// Anthropic/Claude-style radial burst mark.
function ClaudeMark({ className, style }) {
  return (
    <svg className={className} style={style} viewBox="0 0 24 24" fill="currentColor" aria-label="Claude">
      <g transform="translate(12 12)">
        {[0, 45, 90, 135, 180, 225, 270, 315].map((deg) => (
          <rect key={deg} x="-1" y="-10" width="2" height="9" rx="1" transform={`rotate(${deg})`} />
        ))}
      </g>
    </svg>
  )
}

function GitHubMark({ className, style }) {
  return (
    <svg className={className} style={style} viewBox="0 0 24 24" fill="currentColor" aria-label="GitHub">
      <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0 0 24 12c0-6.63-5.37-12-12-12z" />
    </svg>
  )
}
