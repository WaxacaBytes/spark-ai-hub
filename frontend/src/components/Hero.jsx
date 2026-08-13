import { useEffect, useState } from 'react'
import { useStore } from '../store'
import { useThemedLogo } from '../hooks/useThemedLogo'
import { backdropFor } from '../covers'
import { formatParams } from './RecipeCard'

const ROTATE_MS = 9000

// Full-bleed cinematic header that cycles through a handful of featured
// titles. Rotation pauses while the pointer is over the hero.
export default function Hero({ picks }) {
  const [index, setIndex] = useState(0)
  const [paused, setPaused] = useState(false)
  const selectRecipe = useStore((s) => s.selectRecipe)
  const installRecipe = useStore((s) => s.installRecipe)

  // Keep the index valid when the pick list changes length.
  useEffect(() => { setIndex((i) => (picks.length ? i % picks.length : 0)) }, [picks.length])

  useEffect(() => {
    if (paused || picks.length < 2) return
    const t = setInterval(() => setIndex((i) => (i + 1) % picks.length), ROTATE_MS)
    return () => clearInterval(t)
  }, [paused, picks.length])

  const recipe = picks[index]
  const logoUrl = useThemedLogo(recipe?.logo)
  if (!recipe) return null

  const meta = [
    recipe.author,
    recipe.params_b != null && formatParams(recipe),
    recipe.quantization,
    recipe.tokens_per_second != null && `${recipe.tokens_per_second} tok/s`,
  ].filter(Boolean)

  const openUrl = `http://${location.hostname}:${recipe.ui?.port ?? 8080}${recipe.ui?.path ?? '/'}`

  return (
    // Tall enough that a 2.56:1 backdrop is only lightly cropped — at 46vh
    // the band was ~3.4:1 and sliced the subject's head off.
    <div
      className="relative h-[clamp(420px,62vh,620px)] w-full overflow-hidden"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
      {/* Backdrops are stacked and cross-faded so the swap never flashes. */}
      {picks.map((r, i) => (
        <img
          key={r.slug}
          src={backdropFor(r)}
          alt=""
          aria-hidden={i !== index}
          className={`absolute inset-0 h-full w-full object-cover transition-opacity duration-1000 ${
            i === index ? 'opacity-100' : 'opacity-0'
          }`}
        />
      ))}

      {/* Blend the art into the app background on the left and bottom. */}
      <div className="absolute inset-0" style={{ background: 'var(--hero-overlay-left)' }} />
      <div className="absolute inset-x-0 bottom-0 h-32 bg-gradient-to-t from-bg to-transparent" />

      <div className="relative flex h-full max-w-3xl flex-col justify-center gap-3 px-10">
        <div className="flex items-center gap-3">
          {logoUrl && (
            <img
              src={logoUrl}
              alt=""
              className="h-14 w-14 rounded-xl bg-black/35 object-contain p-2 ring-1 ring-white/15 backdrop-blur-md"
            />
          )}
          <span className="rounded-full bg-primary px-2.5 py-0.5 font-label text-[10px] font-bold uppercase tracking-wider text-primary-on">
            {recipe.running || recipe.starting ? 'Now Running' : 'Featured'}
          </span>
        </div>

        {/* Wraps freely — hero titles are never truncated. */}
        <h1 className="m-0 font-display text-[clamp(1.6rem,3.4vw,2.6rem)] font-bold leading-tight tracking-tight text-text drop-shadow-lg">
          {recipe.name}
        </h1>

        <p className="m-0 font-label text-xs text-text-muted drop-shadow">{meta.join('  ·  ')}</p>

        <p className="m-0 max-w-xl text-sm leading-relaxed text-text-muted line-clamp-2 drop-shadow">
          {recipe.description}
        </p>

        <div className="mt-2 flex items-center gap-3">
          {recipe.running && recipe.ready ? (
            <a href={openUrl} target="_blank" rel="noreferrer" className="btn-primary px-6 py-2 text-sm font-bold no-underline">
              ▶  Open
            </a>
          ) : !recipe.installed && !recipe.starting ? (
            <button onClick={() => installRecipe(recipe.slug)} className="btn-primary px-6 py-2 text-sm font-bold">
              Install
            </button>
          ) : (
            <button onClick={() => selectRecipe(recipe.slug)} className="btn-primary px-6 py-2 text-sm font-bold">
              View Details
            </button>
          )}
          <button
            onClick={() => selectRecipe(recipe.slug)}
            className="cursor-pointer rounded-xl border border-glass-border bg-surface/40 px-5 py-2 text-sm font-medium text-text backdrop-blur transition-all hover:bg-surface/70"
          >
            More Info
          </button>
        </div>
      </div>

      {picks.length > 1 && (
        <div className="absolute bottom-5 right-8 flex items-center gap-1.5">
          {picks.map((r, i) => (
            <button
              key={r.slug}
              onClick={() => setIndex(i)}
              aria-label={`Show ${r.name}`}
              className={`h-1.5 cursor-pointer rounded-full border-none transition-all ${
                i === index ? 'w-6 bg-primary' : 'w-1.5 bg-text-dim/60 hover:bg-text-dim'
              }`}
            />
          ))}
        </div>
      )}
    </div>
  )
}
