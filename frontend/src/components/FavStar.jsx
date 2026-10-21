import { useAuth } from '../auth'
import { useStore } from '../store'
import { isModel, starredBy } from '../models'
import Hint from './Hint'

// The star an admin puts on a build to tell everyone "use this one". Admins
// get a button that sets and clears it; everyone else sees the star where
// there is one, and what it means on hover or tap. `slot` keeps the space
// when there is no star, so the labels of a table's rows stay aligned.

export default function FavStar({ recipe, slot = false, className = 'text-[12px]', tap = true }) {
  const isAdmin = useAuth((s) => s.user?.role === 'admin')
  const setFavorite = useStore((s) => s.setFavorite)
  const on = !!recipe.favorite

  if (isAdmin) {
    return (
      <button
        type="button"
        aria-pressed={on}
        aria-label={on ? `Unstar ${recipe.name}` : `Star ${recipe.name} as recommended`}
        onClick={(e) => {
          e.preventDefault()
          e.stopPropagation()
          setFavorite(recipe.slug, !on)
        }}
        className={`w-3 shrink-0 cursor-pointer border-none bg-transparent p-0 text-center leading-none transition-colors ${className} ${
          on ? 'text-fav' : 'text-text-dim/40 hover:text-fav'
        }`}
      >
        {on ? '★' : '☆'}
      </button>
    )
  }
  if (!on) return slot ? <span className="w-3 shrink-0" /> : null
  return (
    <Hint text={starredBy(recipe)} tap={tap} className={`w-3 shrink-0 text-center leading-none text-fav ${className}`}>
      ★
    </Hint>
  )
}

// The same star as a labelled pill, for a recipe's own page.
export function FavBadge({ recipe }) {
  const isAdmin = useAuth((s) => s.user?.role === 'admin')
  const setFavorite = useStore((s) => s.setFavorite)
  const on = !!recipe.favorite
  const pill = 'inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 font-label text-[11px] font-bold'
  const bubble = `${starredBy(recipe)}.${isModel(recipe) ? ' Agents use it unless they ask for another model.' : ''}`

  if (isAdmin) {
    return (
      <Hint text={on ? `${bubble} Tap to remove the star.` : 'Star it to recommend it to everyone on this Spark.'} tap={false}>
        <button
          type="button"
          aria-pressed={on}
          onClick={() => setFavorite(recipe.slug, !on)}
          className={`${pill} cursor-pointer border transition-colors ${
            on ? 'border-fav/40 bg-fav/15 text-fav' : 'border-outline bg-transparent text-text-muted hover:border-fav hover:text-fav'
          }`}
        >
          {on ? '★ Recommended' : '☆ Recommend'}
        </button>
      </Hint>
    )
  }
  if (!on) return null
  return (
    <Hint text={bubble} className={`${pill} cursor-default bg-fav/15 text-fav`}>★ Recommended</Hint>
  )
}
