import { useState } from 'react'
import { useStore } from '../store'

// Only one model can hold the GPU and port 9001 at a time, so launching from a
// catalog tile has to evict whatever is already up. Stopping a running model
// is not something to discover after the fact, so it is confirmed first.
export default function LaunchConflictModal() {
  const request = useStore((s) => s.launchRequest)
  const confirmLaunch = useStore((s) => s.confirmLaunch)
  const cancelLaunch = useStore((s) => s.cancelLaunch)
  const recipes = useStore((s) => s.recipes)
  const [swapping, setSwapping] = useState(false)

  if (!request) return null

  const nameOf = (slug) => recipes.find((r) => r.slug === slug)?.name || slug
  const incoming = nameOf(request.slug)
  const outgoing = request.blockers.map(nameOf)

  const confirm = async () => {
    setSwapping(true)
    await confirmLaunch()
    setSwapping(false)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm animate-fadeIn">
      <div className="w-full max-w-md rounded-2xl border border-outline-dim bg-surface-high p-6 shadow-2xl">
        <h3 className="m-0 font-display text-lg font-bold text-text">Swap running model?</h3>
        <p className="m-0 mt-1 text-sm text-text-dim">Only one runs at a time.</p>

        {/* The swap, drawn rather than described — the names appear once, and
            the shape of the trade reads before the words do. */}
        <div className="mt-4 flex items-center gap-3 rounded-xl bg-surface-low p-3">
          <Endpoint label="Stopping" name={outgoing.join(' and ')} dot="bg-text-dim" tone="text-text-muted" />
          <span aria-hidden className="shrink-0 text-lg leading-none text-text-dim">→</span>
          <Endpoint label="Starting" name={incoming} dot="bg-primary" tone="text-text" />
        </div>

        <div className="mt-5 flex justify-end gap-3">
          <button
            onClick={cancelLaunch}
            disabled={swapping}
            className="cursor-pointer rounded-xl border border-outline-dim bg-transparent px-4 py-2 text-sm font-semibold text-text-muted transition-colors hover:text-text disabled:opacity-40"
          >
            Cancel
          </button>
          <button
            onClick={confirm}
            disabled={swapping}
            className="cursor-pointer rounded-xl border-none bg-primary px-5 py-2 text-sm font-bold text-primary-on disabled:cursor-default disabled:opacity-40"
          >
            {swapping ? 'Swapping…' : 'Stop & Launch'}
          </button>
        </div>
      </div>
    </div>
  )
}

function Endpoint({ label, name, dot, tone }) {
  return (
    <div className="min-w-0 flex-1">
      <p className="m-0 flex items-center gap-1.5 font-label text-[10px] font-bold uppercase tracking-wider text-text-dim">
        <span className={`h-1.5 w-1.5 rounded-full ${dot}`} />
        {label}
      </p>
      <p className={`m-0 mt-0.5 truncate text-sm font-semibold ${tone}`} title={name}>{name}</p>
    </div>
  )
}
