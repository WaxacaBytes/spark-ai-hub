import { useStore } from '../store'

/* "This app is handing out links to another address."
 *
 * An app that publishes its own port is told where the Hub is once, when it
 * starts (SAH_HUB_URL, see daemon/services/docker_service.py). Everything it
 * makes carries links built on that address, because the Hub builds every URL
 * it prints from the address it was asked on. Read the Hub somewhere else --
 * mDNS name instead of IP, Tailscale, a tunnel -- and those links point
 * somewhere you are not, which is silent and baffling unless it is said out
 * loud. Starting the app again is what re-tells it.
 *
 * Shown wherever an app is opened from: its card, its page, and Running. */
export default function HubAddressNotice({ recipe, className = '' }) {
  const launchRecipe = useStore((s) => s.launchRecipe)
  if (!recipe.running || !recipe.launch_origin) return null
  if (recipe.launch_origin === window.location.origin) return null

  const relaunch = (e) => {
    e.preventDefault()
    e.stopPropagation()
    launchRecipe(recipe.slug)
  }

  return (
    <div className={`flex items-center gap-2 flex-wrap text-[11px] text-warning bg-warning/10 rounded-xl px-3 py-2 ${className}`}>
      <span>
        Started from <b>{recipe.launch_origin.replace(/^https?:\/\//, '')}</b>, so what it makes
        links back there, not to the address you are reading this on.
      </span>
      <button
        onClick={relaunch}
        className="ml-auto px-2.5 py-1 rounded-lg bg-surface-high text-text border border-outline-dim text-[11px] font-semibold cursor-pointer hover:bg-surface-highest transition-colors whitespace-nowrap"
      >
        Restart for this address
      </button>
    </div>
  )
}
