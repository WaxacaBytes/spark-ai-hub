// Grouping the model catalog into one row per model, many builds beneath it.
//
// The catalog carries 73 model recipes, but they are not 73 decisions. Seven of
// them are Qwen3.6-27B — the same weights at BF16, FP8, NVFP4, each with and
// without DFlash. Choosing between *models* and choosing between *builds of one
// model* are different questions, and listing them flat asks both at once.
//
// So builds are collapsed under the model they build. The group key is the slug
// with its engine prefix and its trailing build modifiers stripped; everything
// that survives is the model itself, which means a distinct finetune (AEON
// Ultimate, Heretic) correctly keeps a row of its own.

// Trailing slug tokens that describe *how* a model was built, never which model
// it is: quantization formats, per-tensor precision overrides, and
// speculative-decoding drafters. `bf16head` is the last of those — a checkpoint
// that leaves the output projection dense while the body stays quantized is a
// build of the model, not a different model.
const BUILD_TOKENS = new Set([
  'bf16', 'fp8', 'nvfp4', 'int4', 'mxfp4', 'awq', 'gptq',
  'q8', 'q4', 'iq2m', 'iq1m', 'q3ks', 'q2kxl', 'q4km', 'exl3',
  // `k2` is EXL3's rate label (2-bit trellis), the same kind of fact as q2kxl.
  // It keeps vllm-glm53-flash-exl3-k2 in the GLM-5.3-Flash model row rather
  // than splitting off a row of its own.
  'k2',
  'bf16head',
  'dflash', 'dflash2', 'dspark', 'mtp', 'eagle',
  // `abliterated` earns its place here for the same reason `bf16head` does:
  // the EXL3 DeepSeek-V4-Flash pair ships one checkpoint, and the uncensored
  // build differs only by a refusal direction projected out at runtime. Same
  // weights, same decode path, same speed — a build of the model, not another
  // model. A finetune that ships genuinely abliterated *weights* would be a
  // row of its own and should not use this token in its slug.
  'abliterated',
  // `ple` marks the Qwen3.8-Flash-Next build whose PLE n-gram table is packed
  // to NVFP4 and decoded CPU-side. Same checkpoint lineage and same decode
  // path as a plain NVFP4 build — it is what makes the model fit, not a
  // different model.
  'ple',
  // `mmap` marks the Qwen3.8-Flash-Next build whose packed PLE table is
  // memory-mapped rather than resident. Same checkpoint lineage, same decode
  // path — a build of the model, not another model.
  'mmap',
  // A prepared package for the Spark GPU is another build of the same model.
  'gb10',
  // vcruz305's EXL3 packs of DeepSeek-V4.1-Flash-1017: SAGE is the quantizer,
  // DERISKED its later pack with Blackfrost-AI. Both are quantizations of the
  // same weights, so both are builds in the DeepSeek-V4.1-Flash-1017 row.
  'sage', 'derisked',
  // `video` marks the Qwen3.8-Flash-Next NVFP4 build kept on TensorFold's
  // previous engine for video input. Same checkpoint, same row.
  'video',
])

// ── Kinds ─────────────────────────────────────────────────────────────
// Three kinds of thing on this Spark: apps (their own UI), the language
// models an agent talks to at /v1, and the media models it calls as tools
// through /mcp.

// A model has no interface of its own: the Hub serves it as an API (/v1 for
// the language models, /mcp for the rest). Apps bring their own UI. This is
// what the old list of engine slug prefixes approximated, so a new engine
// needs no change here.
export function isModel(recipe) {
  return recipe.ui?.type === 'api-only'
}

// A model an agent drives: an OpenAI-compatible server the Hub's /v1 routes
// to. Mirrors Recipe.is_llm on the daemon.
export function isAgentModel(recipe) {
  return recipe.category === 'llm' && recipe.ui?.type === 'api-only'
}

// A LoRA: an add-on a media model loads when a render asks for it. Nothing of
// it runs, so it installs and uninstalls but never launches.
export function isLora(recipe) {
  return Boolean(recipe.lora)
}

// Every other model: image, video, music and decision models, which agents
// call as tools through the Hub's MCP server rather than talk to -- and the
// LoRAs that load into them.
export function isMediaModel(recipe) {
  return (isModel(recipe) && !isAgentModel(recipe)) || isLora(recipe)
}

// What a media model makes, from the capabilities the daemon derives off its
// tags. Render times only compare within one kind: 8 s is fast for a 3D model
// and impossible for a 10 s clip.
// What a recipe is to whoever uses it: an app they open, a language model
// agents talk to at /v1, or a media model agents call through /mcp.
export function kindOf(recipe) {
  if (isAgentModel(recipe)) return 'llm'
  if (isModel(recipe)) return 'media'
  return 'app'
}

export function mediaKind(recipe) {
  const caps = recipe.media_capabilities || []
  if (caps.some((c) => c.endsWith('-to-video'))) return 'video'
  if (caps.includes('image-to-3d')) return '3D model'
  if (caps.includes('image-generation') || caps.includes('image-editing')) return 'image'
  if (caps.includes('music-generation')) return 'song'
  return null
}

// A media model's main arena score: the first the recipe lists (its main
// task). Elo only compares within one board, so callers group by `board`.
export function primaryArena(recipe) {
  return recipe.arena?.[0] || null
}

// "2.9 s", "45 s", "2.2 min", "1.4 h" — one figure, its own unit, no range.
export function formatDuration(seconds) {
  if (seconds == null) return null
  if (seconds < 10) return `${seconds.toFixed(1)} s`
  if (seconds < 90) return `${Math.round(seconds)} s`
  if (seconds < 5400) return `${(seconds / 60).toFixed(seconds < 600 ? 1 : 0)} min`
  return `${(seconds / 3600).toFixed(1)} h`
}

const ENGINE_PREFIX = /^(vllm|vllmomni|sglang|llamacpp|exllamav3|atlas|tensorfold|hy3dgen|ltxpipelines|sol|diffusers|fastvideo|sparc3d)-/

export function modelKey(recipe) {
  const parts = (recipe.slug || '').replace(ENGINE_PREFIX, '').split('-')
  while (parts.length > 1 && BUILD_TOKENS.has(parts[parts.length - 1])) parts.pop()
  return parts.join('-')
}

// The group heading is the longest whole-word prefix the members' names share —
// "Qwen3.6-27B" out of "Qwen3.6-27B FP8 DFlash" and "Qwen3.6-27B NVFP4". Recipe
// names are written by hand and consistently, so this beats prettifying a slug.
function commonPrefix(names) {
  const first = names[0].split(' ')
  let end = first.length
  for (const name of names.slice(1)) {
    const words = name.split(' ')
    let i = 0
    while (i < end && i < words.length && words[i] === first[i]) i++
    end = i
  }
  return end > 0 ? first.slice(0, end).join(' ') : names[0]
}

// ── Naming ────────────────────────────────────────────────────────────
// Two ways of not printing the same fact twice. `buildLabel` drops what the
// group heading above the row already says; `displayName` drops what the Quant
// column beside it already says. Both exist to buy room for what is left.

// The full model name, minus the quantization the Quant column carries.
// Matched on a whole token with `-` counting as part of the word — otherwise
// "INT4" would eat the tail of "GPTQ-Int4".
export function displayName(recipe) {
  const quant = recipe.quantization
  if (!quant) return recipe.name
  const token = quant.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  return (recipe.name || '')
    .replace(new RegExp(`(?<![\\w-])\\(?${token}\\)?(?![\\w-])`, 'i'), '')
    .replace(/\(\s*\)/g, '')
    .replace(/\s{2,}/g, ' ')
    .trim() || recipe.name
}

// One number cannot describe a model that drafts speculatively. A drafter
// proposes tokens the target model verifies in a single pass, so throughput
// tracks how much of the output is copied from the prompt rather than invented.
// Qwen3.8-27B NVFP4 DSpark writes at 19.5 tok/s and edits at 58.0 — both
// sustained over thousands of tokens, neither a spike. Averaging them reports
// 19.5 for a build that genuinely does 58 on editing work, and ranks it below a
// slower non-speculative sibling.
//
// Measured across four kinds of edit, the kind of text barely matters (prose
// 57.4, markdown 56.3, code 58.1), so this is an editing rate rather than a
// coding one. `tokens_per_second` stays the baseline every recipe has, and
// stays what the default sort uses.
export function speedRange(recipe) {
  const base = recipe.tokens_per_second
  if (base == null) return null
  const editing = recipe.tokens_per_second_editing
  return editing != null && editing > base ? `${base}–${editing}` : `${base}`
}

export function speedLabel(recipe) {
  const range = speedRange(recipe)
  return range == null ? null : `${range} tok/s`
}

// What this build is, told only through quantization and speculative drafter
// — "NVFP4 DFlash", "BF16 DSpark", "FP8". Everything else that could
// distinguish a build (engine, MoE-ness, a finetune's name) is already shown
// elsewhere — the group heading carries the finetune, other columns carry the
// engine — so it stays out of here rather than being said twice.
// Keyed by `recipe.speculative_method` — parsed straight off each recipe's
// docker-compose.yml command (--speculative-config / --speculative-algorithm)
// by tools/sync_speculative_method.py, never from tags or the slug. Both of
// those have been wrong: a slug named "dspark" whose active algorithm is
// EAGLE, and drafters running with no matching tag at all. Only the literal
// runtime flag can be trusted.
const DRAFTER_LABELS = { dflash: 'DFlash', dspark: 'DSpark', mtp: 'MTP', eagle: 'EAGLE' }

// "INT4" alone hides which quantization algorithm actually produced it —
// GPTQ and AutoRound W4A16 are different builds, not the same one. The
// signal for which is inconsistently placed (sometimes a tag, sometimes only
// in the free-text name), so both are checked. AutoRound is named "W4A16"
// rather than "Int4" because that's the term its own recipe uses throughout
// (description: "AutoRound W4A16") — inventing "AutoRound-Int4" would be a
// term this recipe never uses about itself.
function resolvedQuant(recipe) {
  const quant = recipe.quantization || ''
  if (quant !== 'INT4') return quant
  const tags = recipe.tags || []
  if (tags.includes('autoround') && tags.includes('w4a16')) return 'AutoRound W4A16'
  if (tags.includes('gptq') || /gptq/i.test(recipe.name || '')) return 'GPTQ-Int4'
  return quant
}

// Who published the weights a build serves: the orgs of the Hugging Face
// repos its build downloads (the daemon reads them off the compose file), in
// download order. Two builds of one model differ most in this -- NVIDIA's
// NVFP4 against a community INT4 -- and nothing else on a row says it.
export function publishers(recipe) {
  return [...new Set((recipe.weights || []).map((w) => w.repo.split('/')[0]))].join(', ')
}

// ── Why a build is kept ───────────────────────────────────────────────
// A model with several builds keeps each one for a reason: speed, size (room
// beside the media models), throughput with several requests at once, a
// capability the others lack, or quality. All but quality are compared here,
// live, among the builds on screen, so adding or removing a build moves the
// marks with it and nothing written down goes stale. Quality comes from the
// recipe's own `keep_for`, one short fact.

// Tags that name something a build can do, and the word a chip uses for each.
const CAPABILITIES = {
  video: 'Video', vision: 'Images', audio: 'Audio',
  uncensored: 'Uncensored', abliterated: 'Uncensored',
  'tool-use': 'Tool calls', 'structured-output': 'JSON schema', reasoning: 'Reasoning',
  'image-edit': 'Image editing', 'image-to-video': 'Image to video', 'last-frame': 'Last frame',
  'multi-reference': 'Several references', 'video-audio': 'Sound', rgba: 'Transparency', pbr: 'PBR textures',
}

// Values this close to the top count as the top: 156.6 against 156.8 is not a
// reason to keep one build over the other.
const TIE = 0.03

// Bits a weight, read off the quantization: what a build gives up in quality
// to be smaller or faster. Unknown or mixed formats stay out of the comparison.
function bitsOf(recipe) {
  const q = (recipe.quantization || '').toUpperCase()
  const bpw = q.match(/(\d+(?:\.\d+)?)\s*BPW/)
  if (bpw) return parseFloat(bpw[1])
  if (q.includes(',')) return null
  if (/^(BF16|FP16|FP32)/.test(q)) return 16
  if (/^(FP8|INT8|Q8)/.test(q)) return 8
  if (/^(NVFP4|MXFP4|INT4)$/.test(q)) return 4
  if (/K2$|^IQ2/.test(q)) return 2
  if (/^IQ1/.test(q)) return 1.75
  return null
}

function topOf(recipe, siblings, value) {
  const vals = siblings.map(value).filter((x) => x != null)
  const mine = value(recipe)
  if (mine == null || vals.length < 2) return false
  const best = Math.max(...vals)
  // Nothing to tell apart when every build sits within the tie.
  if (Math.min(...vals) >= best * (1 - TIE)) return false
  return mine >= best * (1 - TIE)
}

export function whyKept(recipe, siblings) {
  if (siblings.length < 2) return []
  const out = []
  if (topOf(recipe, siblings, (r) => r.tokens_per_second)) out.push('Fastest writing')
  if (topOf(recipe, siblings, (r) => r.tokens_per_second_editing)) out.push('Fastest editing')
  // Only builds measured with the same number of requests are compared.
  const n = recipe.concurrent?.requests
  if (n && topOf(recipe, siblings, (r) => (r.concurrent?.requests === n ? r.concurrent.tokens_per_second : null))) {
    out.push(`Most with ${n} at once`)
  }
  // Smallest by a clear margin: what lets it run beside an image or video
  // model. Builds tied at the smallest size all count.
  const sizes = siblings.map((r) => r.memory_gb).filter((x) => x > 0).sort((a, b) => a - b)
  const larger = sizes.find((x) => x > sizes[0] * (1 + TIE))
  if (larger && sizes[0] <= larger * 0.85 && recipe.memory_gb > 0 && recipe.memory_gb <= sizes[0] * (1 + TIE)) {
    out.push(`Smallest · ${Math.round(recipe.memory_gb)} GB`)
  }
  if (topOf(recipe, siblings, bitsOf)) out.push('Most precise')
  const has = (r, tag) => (r.tags || []).some((t) => CAPABILITIES[t] === CAPABILITIES[tag])
  // A build missing something the model itself can do carries `no-<tag>`,
  // e.g. `no-video` on a Flash-Next build whose engine refuses video. Then
  // that capability is the model's own, not a reason to keep the builds
  // that have it: only the build without it is marked.
  const lacked = new Set(siblings.flatMap((r) => (r.tags || []).map((t) => t.startsWith('no-') && CAPABILITIES[t.slice(3)])))
  const caps = new Set()
  for (const tag of recipe.tags || []) {
    const lacks = tag.startsWith('no-') && CAPABILITIES[tag.slice(3)]
    if (lacks) caps.add(`No ${lacks.toLowerCase()}`)
    else if (CAPABILITIES[tag] && !lacked.has(CAPABILITIES[tag]) && siblings.some((r) => !has(r, tag))) caps.add(CAPABILITIES[tag])
  }
  out.push(...caps)
  if (recipe.keep_for) out.push(recipe.keep_for)
  return out
}

export function buildLabel(recipe) {
  const quant = resolvedQuant(recipe)
  const drafter = DRAFTER_LABELS[recipe.speculative_method] || ''
  const label = [quant, drafter].filter(Boolean).join(' ')
  return label || recipe.engine || 'Default build'
}

// Groups `recipes` by model. `comparator` ranks the groups against each other;
// `buildComparator` ranks the builds within one, and is separate because the
// catalog's default sort ("AI Lab") ranks nothing — under it the groups are
// shelved by vendor, but the build on show still has to be the best one.
export function groupModels(recipes, comparator, buildComparator = comparator) {
  const groups = new Map()
  for (const recipe of recipes) {
    const key = modelKey(recipe)
    if (!groups.has(key)) groups.set(key, { key, items: [] })
    groups.get(key).items.push(recipe)
  }

  const out = []
  for (const group of groups.values()) {
    group.items.sort(buildComparator)
    group.label = commonPrefix(group.items.map((r) => r.name || ''))
    group.lead = group.items[0]
    out.push(group)
  }
  // A group ranks where its best build ranks — you pick the model by what the
  // best build of it can do, then pick the build.
  out.sort((a, b) => comparator(a.lead, b.lead))
  return out
}

// What a star on a build means, for the bubble beside it.
export function starredBy(recipe) {
  const by = recipe.favorite?.by
  return by ? `Recommended on this Spark · starred by ${by}` : 'Recommended on this Spark'
}

// "Starred by Abel": who picked a set of builds, for a shelf's subtitle.
export function starredByLine(recipes) {
  const names = [...new Set(recipes.map((r) => r.favorite?.by).filter(Boolean))]
  return names.length ? `Starred by ${names.join(' and ')}` : null
}

// "35B-A3B" for MoE (total + active), plain "27B" for dense.
export function formatParams(recipe) {
  const b = (n) => (Number.isInteger(n) ? n : n.toFixed(1))
  const total = `${b(recipe.params_b)}B`
  return recipe.active_params_b != null ? `${total}-A${b(recipe.active_params_b)}B` : total
}
