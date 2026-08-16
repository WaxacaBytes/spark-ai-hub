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
  'dflash', 'dspark', 'mtp',
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

// What this build is, with the part every sibling shares taken away: "NVFP4
// DFlash". A build whose name *is* the group name is the plain one, and is
// labelled by its quantization instead of left blank.
export function buildLabel(recipe, groupLabel) {
  const name = recipe.name || ''
  const tail = (name.startsWith(groupLabel) ? name.slice(groupLabel.length) : name)
    .trim()
    // A tail that was parenthetical in the full name — "… (BF16)" — is the
    // headline once the shared part is gone, so it loses its brackets.
    .replace(/^\((.*)\)$/, '$1')
  const quant = recipe.quantization || ''
  if (!tail) return quant || recipe.engine || 'Default build'
  // Most tails already name the quantization ("NVFP4 DFlash"), but some name
  // only the drafter or the architecture ("DFlash", "MoE"). Every row has to
  // say what precision it runs at, so the missing ones get it appended.
  if (quant && !tail.toLowerCase().includes(quant.toLowerCase())) return `${tail} · ${quant}`
  return tail
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
