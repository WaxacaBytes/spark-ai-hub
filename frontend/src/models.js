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
// it is: quantization formats and speculative-decoding drafters.
const BUILD_TOKENS = new Set([
  'bf16', 'fp8', 'nvfp4', 'int4', 'mxfp4', 'awq', 'gptq',
  'q8', 'q4', 'iq2m', 'iq1m', 'q3ks',
  'dflash', 'dflash2', 'dspark', 'mtp', 'eagle',
])

const ENGINE_PREFIX = /^(vllm|sglang|llamacpp|atlas)-/

function modelKey(recipe) {
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
