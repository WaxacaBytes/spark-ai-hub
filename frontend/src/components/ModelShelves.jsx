import { useMemo, useState } from 'react'
import CardRow from './CardRow'
import Hint from './Hint'
import ModelList, { ModelBlock } from './ModelList'
import { vendorKey, vendorLabel } from '../covers'
import { groupModels } from '../models'

// The model catalog as shelves. The Agents page draws two of them: the LLMs
// agents talk to at /v1, and the media models they call as tools through
// /mcp. Same shelves, same sorts; each passes its subset and the sorts that
// mean something for it.

// Every sort mode draws shelves. "AI Lab" (the default) shelves by vendor;
// the rest rank all models against each other and shelve them into bands of
// the sorted value, so the rows themselves read top-to-bottom in rank order.
// `key` returns a number to sort descending — null/undefined always sinks.

// Builds a band labeller from descending [floor, label] pairs.
function bands(defs, unknown) {
  return (value) => {
    if (value == null) return unknown
    for (const [floor, label] of defs) {
      if (value >= floor) return label
    }
    return defs[defs.length - 1][1]
  }
}

// Quarters for the current year, then whole years — month-by-month shelves
// would leave a dozen rows holding two tiles each.
function releaseBand(value) {
  if (!value) return 'Undated'
  const [year, month] = value.split('-')
  if (year !== String(new Date().getFullYear())) return year
  return `Q${Math.floor((Number(month) - 1) / 3) + 1} ${year}`
}

const MODEL_SORTS = [
  { id: 'lab', label: 'AI Lab', key: null },
  {
    id: 'release',
    label: 'Newest',
    key: null,
    band: (r) => releaseBand(r.release_date),
  },
  {
    id: 'params',
    label: 'Params',
    key: (r) => r.params_b,
    band: (r) => bands([[100, '100B+'], [30, '30–100B'], [10, '10–30B'], [0, 'Under 10B']],
      'Size unlisted')(r.params_b),
  },
  {
    id: 'size',
    label: 'Size on disk',
    key: (r) => r.weights_gb,
    band: (r) => bands([[80, '80 GB+'], [50, '50–80 GB'], [20, '20–50 GB'], [0, 'Under 20 GB']],
      'Unmeasured')(r.weights_gb),
  },
  // The two speed sorts are named after the work, not after a superlative:
  // "Speed" never said speed at what, and both figures are sustained rates over
  // thousands of tokens, so calling either one a peak would misdescribe it.
  {
    id: 'speed',
    label: 'Writing',
    key: (r) => r.tokens_per_second,
    note: 'Ranked on writing new text: three prompts — a quicksort with comments, TCP vs '
      + 'UDP, a Kyoto itinerary — averaged, 512 tokens each at temperature 0, with thinking '
      + 'left on as these models ship it. Nothing in the answer can be copied from the '
      + 'question, so a speculative drafter has little to work with and every build is '
      + 'close to its plain decode speed. This is the figure every recipe carries.',
    band: (r) => bands([[100, '100+ tok/s writing'], [50, '50–100 tok/s writing'],
      [25, '25–50 tok/s writing'], [10, '10–25 tok/s writing'], [0, 'Under 10 tok/s writing']],
      'Not benchmarked')(r.tokens_per_second),
  },
  // Where a speculative drafter's value actually shows up. Under Writing those
  // builds rank below plainer, slower ones, because writing is the workload a
  // drafter cannot help with. Only builds measured on an edit can be ranked, so
  // the rest fall to one band at the bottom rather than being silently ordered
  // by a number they do not have.
  {
    id: 'editing',
    label: 'Editing',
    key: (r) => r.tokens_per_second_editing,
    note: 'Ranked on reproducing a document with a small change applied — here a 45-class '
      + 'Python module with one method added to every class, sent with thinking off '
      + '(enable_thinking: false), temperature 0 and a 3000-token cap. This is a sustained '
      + 'rate over those 3000 tokens, not a burst. Nearly all of the output already exists '
      + 'in the prompt, so a speculative drafter\'s proposals are almost always accepted and '
      + 'the server commits several tokens per forward pass. The kind of text barely matters '
      + '— the same recipe measured 57.4 editing prose and 56.3 editing a markdown document '
      + 'against 58.1 on this code — but how much new material the edit introduces does: an '
      + 'edit that writes a fresh docstring for every function fell to 48.5. Nothing else '
      + 'differs from the Writing run: same recipe, same flags, prefix caching on. Builds '
      + 'with no drafter gain little here, and builds not yet measured on an edit are last.',
    band: (r) => bands([[100, '100+ tok/s editing'], [50, '50–100 tok/s editing'],
      [25, '25–50 tok/s editing'], [0, 'Under 25 tok/s editing']],
      'Not measured on an edit')(r.tokens_per_second_editing),
  },
  // A capability score, not a speed one — the only sort here that isn't
  // measured on this Spark. Published by Artificial Analysis per base model,
  // so every quant/drafter build of one model shares the same score; a
  // community finetune with no independent evaluation has none.
  {
    id: 'aa-index',
    label: 'AA Index',
    key: (r) => r.artificial_analysis_index,
    note: 'Ranked on the Artificial Analysis Intelligence Index (artificialanalysis.ai), a '
      + 'published capability score for the base model — quantization and speculative '
      + 'drafter do not change it, so every build of one model shares its score. Community '
      + 'finetunes and models with no independent evaluation have none and sort last.',
    band: (r) => bands([[40, '40+ AA Index'], [25, '25–40 AA Index'], [10, '10–25 AA Index'],
      [0, 'Under 10 AA Index']], 'No published score')(r.artificial_analysis_index),
  },
]

export function byRelease(a, b) {
  const dateOrder = (b.release_date || '').localeCompare(a.release_date || '')
  if (dateOrder !== 0) return dateOrder
  return (a.name || '').localeCompare(b.name || '')
}

// Used where the lab shelves cannot be drawn (the search/filter results grid).
export function byLab(a, b) {
  return vendorLabel(vendorKey(a)).localeCompare(vendorLabel(vendorKey(b)))
    || byRelease(a, b)
}

function makeComparator(sort) {
  if (sort?.id === 'lab') return byLab
  if (!sort?.key) return byRelease
  return (a, b) => {
    const av = sort.key(a)
    const bv = sort.key(b)
    if (av == null && bv == null) return byRelease(a, b)
    if (av == null) return 1
    if (bv == null) return -1
    return bv - av || byRelease(a, b)
  }
}

// "6 models · 19 builds" — the second number is the one the old shelves showed,
// and on its own it overstated how many things there are to choose between.
function shelfSubtitle(groups) {
  const builds = groups.reduce((n, g) => n + g.items.length, 0)
  const models = `${groups.length} model${groups.length > 1 ? 's' : ''}`
  return builds === groups.length ? models : `${models} · ${builds} builds`
}

// `models` is the whole set this shelf owns; `visible` is what search leaves of
// it. The frontier is computed over `models` so the badge means the same thing
// whatever is being searched for.
export default function ModelShelves({
  models, visible = models, eyebrow = null, title, subtitle, sorts = null, actions = null, id,
}) {
  const [sortId, setSortId] = useState('lab')
  const offered = sorts ? MODEL_SORTS.filter((s) => sorts.includes(s.id)) : MODEL_SORTS

  // A build is on the Pareto frontier of speed vs. capability if no other
  // build both writes faster and scores higher on the AA Index — i.e. there
  // is no strictly better choice on the two things that actually trade off
  // against each other. Builds missing either number can't be placed on
  // a frontier and never get the badge.
  const frontier = useMemo(() => {
    const points = models.filter(
      (r) => r.tokens_per_second != null && r.artificial_analysis_index != null,
    )
    const set = new Set()
    for (const a of points) {
      const dominated = points.some((b) => b !== a
        && b.tokens_per_second >= a.tokens_per_second
        && b.artificial_analysis_index >= a.artificial_analysis_index
        && (b.tokens_per_second > a.tokens_per_second || b.artificial_analysis_index > a.artificial_analysis_index))
      if (!dominated) set.add(a.slug)
    }
    return set
  }, [models])

  // Only offered sorts have a button, so sortId is always one of them.
  const sort = MODEL_SORTS.find((s) => s.id === sortId) || MODEL_SORTS[0]
  const comparator = useMemo(() => makeComparator(sort), [sort])
  const groupByLab = sort.id === 'lab'
  // Which build of a model leads its row. A ranking sort answers that itself;
  // "AI Lab" and "Newest" do not, and there the fastest build leads — that is
  // the one you would have picked anyway.
  const buildComparator = useMemo(
    () => (sort.key ? comparator : makeComparator(MODEL_SORTS.find((s) => s.id === 'speed'))),
    [sort, comparator],
  )

  const shelves = useMemo(() => {
    // Models are collapsed to one entry per model — Qwen3.6-27B is a single
    // choice with seven builds under it, not seven entries competing for the
    // same slot in your attention. A group ranks and bands by its best build.
    //
    // On the "AI Lab" sort, models are split per vendor. Every other sort
    // ranks the whole catalog in one shelf instead — ordering inside a lab
    // answers the wrong question when you asked for the fastest model.
    if (groupByLab) {
      const byVendor = new Map()
      for (const g of groupModels(visible, comparator, buildComparator)) {
        const key = vendorKey(g.lead)
        if (!byVendor.has(key)) byVendor.set(key, [])
        byVendor.get(key).push(g)
      }
      return [...byVendor.entries()]
        .map(([key, items]) => ({ key, items: [...items].sort((a, b) => byRelease(a.lead, b.lead)) }))
        .sort((a, b) => b.items.length - a.items.length || a.key.localeCompare(b.key))
    }

    // Ranked modes band individual *builds*, not models. Banding a model by
    // its best build put four sub-40 tok/s rows under a "100+ tok/s" heading;
    // a band heading has to be true of every row beneath it. The catalog is
    // already in rank order and the bands are monotonic, so first-seen order
    // gives the shelves their ranking for free (and sinks unknowns last).
    const out = []
    const byBand = new Map()
    for (const r of [...visible].sort(comparator)) {
      const key = sort.band(r)
      if (!byBand.has(key)) {
        byBand.set(key, { key, items: [] })
        out.push(byBand.get(key))
      }
      byBand.get(key).items.push(r)
    }
    return out
  }, [visible, comparator, buildComparator, sort, groupByLab])

  if (models.length === 0) return null

  return (
    <div id={id} className="space-y-7 scroll-mt-16">
      <div className="space-y-2 px-6">
        <div className="flex flex-wrap items-end gap-x-3 gap-y-2">
          <div>
            {eyebrow && (
              <div className="mb-1 font-label text-[10px] font-bold uppercase tracking-[0.14em] text-primary">{eyebrow}</div>
            )}
            <h2 className="m-0 font-display text-xl font-bold tracking-tight text-text">{title}</h2>
            {subtitle && <p className="m-0 mt-0.5 text-xs text-text-dim">{subtitle}</p>}
          </div>
          <div className="flex min-w-0 max-w-full flex-wrap items-center gap-2 sm:ml-auto">
            {offered.length > 1 && (
              <div className="row-scroller flex max-w-full items-center gap-1 overflow-x-auto rounded-xl border border-outline-dim bg-surface-high p-0.5">
                <span className="shrink-0 px-2 font-label text-[10px] text-text-dim">Sort</span>
                {/* What a sort ranks on, in full, on hover. A tap sorts. */}
                {offered.map((s) => (
                  <Hint key={s.id} text={s.note} tap={false} className="shrink-0">
                    <button
                      onClick={() => setSortId(s.id)}
                      className={`cursor-pointer whitespace-nowrap rounded-lg px-2.5 py-1 text-[11px] font-semibold transition-all ${
                        sort.id === s.id ? 'bg-primary text-primary-on' : 'bg-transparent text-text-muted hover:text-text'
                      }`}
                    >
                      {s.label}
                    </button>
                  </Hint>
                ))}
              </div>
            )}
            {actions}
          </div>
        </div>
      </div>

      {visible.length === 0 && (
        <p className="m-0 px-6 text-sm text-text-dim">No models match this search.</p>
      )}

      {/* Two shapes for two questions. Browsing by lab, you are choosing a
          model, so builds sit grouped under theirs. On a ranked sort you are
          comparing builds, so they are one flat list in rank order. Either way
          every build is on screen. */}
      {groupByLab
        ? shelves.map(({ key, items }) => (
            <CardRow key={key} title={vendorLabel(key)} subtitle={shelfSubtitle(items)} wrap>
              {/* Columns, not a grid: the blocks are different heights
                  because models have different numbers of builds, and a grid
                  reserves the tallest block's height for its whole row —
                  leaving holes the size of a block. */}
              <div className="w-full" style={{ columnWidth: '470px', columnGap: '12px' }}>
                {items.map((g) => (
                  <div key={g.key} className="mb-3 break-inside-avoid">
                    <ModelBlock group={g} frontier={frontier} />
                  </div>
                ))}
              </div>
            </CardRow>
          ))
        : shelves.map(({ key, items }) => (
            <CardRow
              key={key}
              title={key}
              subtitle={`${items.length} build${items.length > 1 ? 's' : ''}`}
              wrap
            >
              <div className="w-full">
                <ModelList items={items} variant="ranked" highlight={sort.id} frontier={frontier} />
              </div>
            </CardRow>
          ))}
    </div>
  )
}
