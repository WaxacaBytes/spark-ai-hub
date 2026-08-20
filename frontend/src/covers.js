// Cover art lookup.
//
// Which picture a recipe uses is declared by the recipe itself, in its
// `cover:` block (see registry/recipes/<slug>/recipe.yaml), not decided
// here. Recipes share a picture by naming the same image — that is how every
// build of a model at a given parameter size ends up looking alike — and a
// recipe contributed from outside brings its own image with no code change.
//
// tools/build_covers.py renders each declared image into the two shapes the
// UI needs, both from the same source so a card and its hero always match:
//
//     /covers/<stem>-poster.jpg     600x900
//     /covers/<stem>-backdrop.jpg   2560x1000

const FALLBACK = 'generic'

function stem(recipe) {
  const image = recipe?.cover?.image
  if (!image) return FALLBACK
  return image.replace(/\.[^./]+$/, '')
}

export function posterFor(recipe) {
  return `/covers/${stem(recipe)}-poster.jpg`
}

export function backdropFor(recipe) {
  return `/covers/${stem(recipe)}-backdrop.jpg`
}

// The unmodified source image, served straight out of registry/covers/.
// Everything under /covers is a cropped, graded, scrimmed derivative — this
// is the only way to see the picture as the recipe shipped it.
export function originalFor(recipe) {
  const image = recipe?.cover?.image
  return image ? `/cover-sources/${image}` : null
}

// The recipe carries its own description and credit, so "About this image"
// needs no separate manifest.
export function coverInfo(recipe) {
  return recipe?.cover?.image ? recipe.cover : null
}

// ── Shelf grouping ────────────────────────────────────────────────────
// Only used to decide which shelf a model appears on, never to pick art.

export function isModel(recipe) {
  return /^(vllm|llamacpp|atlas)-/.test(recipe?.slug || '')
}

// A model belongs to the lab that *created* it, never to whoever quantized,
// packaged or re-hosted it. `recipe.author` credits the whole chain
// ("NVIDIA + Atlas Inference" on a Gemma build), so the model family in the
// slug is the authority and the author string is only a last resort.
const FAMILY = [
  ['diffusiongemma', 'google'], ['gemma', 'google'], ['qwen', 'alibaba'],
  ['nemotron', 'nvidia'], ['phi4', 'microsoft'], ['gpt-oss', 'openai'],
  ['muse-glimmer', 'meta'], ['llama', 'meta'], ['deepseek', 'deepseek'],
  ['glm', 'zai'], ['hy3', 'tencent'], ['hunyuan', 'tencent'],
  ['inkling', 'thinking-machines'], ['minimax', 'minimax'], ['laguna', 'poolside'],
  ['ling3', 'inclusion'], ['seed-oss', 'bytedance'], ['minicpm', 'openbmb'],
  ['mistral', 'mistral'], ['mimo', 'xiaomi'],
]

const BY_AUTHOR = {
  'Thinking Machines': 'thinking-machines', 'inclusionAI': 'inclusion',
  'DeepSeek AI': 'deepseek', 'MiniMax AI': 'minimax', 'Alibaba Cloud': 'alibaba',
  'ByteDance': 'bytedance', 'Microsoft': 'microsoft', 'Red Hat AI': 'redhat',
  'poolside': 'poolside', 'OpenBMB': 'openbmb', 'NVIDIA': 'nvidia',
  'Tencent': 'tencent', 'OpenAI': 'openai', 'Google': 'google', 'Meta': 'meta',
  'Qwen': 'alibaba', 'Z.AI': 'zai', 'Xiaomi': 'xiaomi',
}

const AUTHOR_KEYS = Object.keys(BY_AUTHOR).sort((a, b) => b.length - a.length)

export function vendorKey(recipe) {
  if (!recipe) return 'generic'
  // The engine prefix is stripped first: otherwise every `llamacpp-*` recipe
  // contains "llama" and the whole llama.cpp shelf lands on Meta.
  const family = (recipe.slug || '').replace(/^(vllm|llamacpp|atlas)-/, '')
  for (const [needle, key] of FAMILY) {
    if (family.includes(needle)) return key
  }
  const author = recipe.author || ''
  for (const key of AUTHOR_KEYS) {
    if (author.includes(key)) return BY_AUTHOR[key]
  }
  return 'generic'
}

// Shelf headings — the lab that created the models, not the packager.
export const VENDOR_LABELS = {
  alibaba: 'Alibaba Cloud · Qwen',
  google: 'Google · Gemma',
  nvidia: 'NVIDIA · Nemotron',
  microsoft: 'Microsoft · Phi',
  meta: 'Meta AI',
  openai: 'OpenAI · GPT-OSS',
  tencent: 'Tencent · Hunyuan',
  bytedance: 'ByteDance · Seed',
  'thinking-machines': 'Thinking Machines · Inkling',
  redhat: 'Red Hat AI',
  deepseek: 'DeepSeek',
  zai: 'Z.AI · GLM',
  minimax: 'MiniMax',
  poolside: 'poolside · Laguna',
  inclusion: 'inclusionAI · Ling',
  openbmb: 'OpenBMB',
  mistral: 'Mistral AI',
  xiaomi: 'Xiaomi · MiMo',
  generic: 'More models',
}

export function vendorLabel(key) {
  return VENDOR_LABELS[key] || key
}
