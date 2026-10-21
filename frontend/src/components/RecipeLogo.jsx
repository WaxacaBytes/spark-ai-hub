import { useState } from 'react'
import { useThemedLogo } from '../hooks/useThemedLogo'

// A recipe's logo on a tile, with its state as a dot on the corner: green
// when it is up, amber while it starts, none when stopped.
export default function RecipeLogo({ recipe, className = 'h-10 w-10 rounded-xl p-1.5' }) {
  const logoUrl = useThemedLogo(recipe.logo)
  const [failed, setFailed] = useState(false)
  const dot = recipe.running && recipe.ready
    ? 'bg-primary'
    : recipe.running || recipe.starting ? 'bg-warning animate-pulse' : null
  return (
    <span className="relative shrink-0">
      {logoUrl && !failed ? (
        <img src={logoUrl} alt="" onError={() => setFailed(true)} className={`block bg-surface-high object-contain ${className}`} />
      ) : (
        <span className={`flex items-center justify-center bg-surface-high text-base ${className}`}>{recipe.icon || '◻'}</span>
      )}
      {dot && <span className={`absolute -bottom-0.5 -right-0.5 h-3 w-3 rounded-full ring-2 ring-surface ${dot}`} />}
    </span>
  )
}
