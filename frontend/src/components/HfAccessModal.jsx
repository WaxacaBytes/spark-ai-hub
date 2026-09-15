import { useState } from 'react'
import { useStore } from '../store'

// The companion to HfTokenModal, for the half of the problem a token cannot
// solve. A gated repo needs the account holding the token to have accepted
// that repo's agreement on HuggingFace, and there is no API to accept one —
// it is a real agreement, so the person has to read it. All this modal can do
// is name the repos, link them, and check again afterwards.
export default function HfAccessModal() {
  const request = useStore((s) => s.hfAccessRequest)
  // Keyed on the slug so a second app's gate opens with a clean dialog rather
  // than the previous one's error still on screen — a remount instead of an
  // effect that resets state on the way back in.
  return request ? <Dialog key={request.slug} request={request} /> : null
}

function Dialog({ request }) {
  const recheckHfAccess = useStore((s) => s.recheckHfAccess)
  const cancelHfAccess = useStore((s) => s.cancelHfAccess)
  const recipes = useStore((s) => s.recipes)
  const [error, setError] = useState('')
  const [checking, setChecking] = useState(false)

  const recipe = recipes.find((r) => r.slug === request.slug)
  const verb = request.action === 'install' ? 'install' : 'launch'
  const repos = request.repos || []

  const recheck = async () => {
    setChecking(true)
    setError('')
    const result = await recheckHfAccess()
    if (!result.ok) {
      setError(result.error || 'Access check failed')
      setChecking(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm animate-fadeIn">
      <div className="bg-surface-high rounded-2xl p-6 w-full max-w-md shadow-2xl border border-outline-dim">
        <h3 className="text-lg font-bold text-text font-display m-0">HuggingFace Access Required</h3>
        <p className="text-sm text-text-dim mt-2 mb-4 leading-relaxed">
          {recipe?.name || request.slug} needs weights behind a terms gate. Your token is saved, but the
          account holding it has not accepted the agreement yet, so the download would fail. Open the
          {repos.length > 1 ? ' pages' : ' page'} below, accept the terms, then check again to {verb} it.
        </p>
        <ul className="list-none p-0 m-0 flex flex-col gap-2">
          {repos.map((repo) => (
            <li key={repo.repo_id}>
              <a
                href={repo.url}
                target="_blank"
                rel="noreferrer"
                className="block px-4 py-2.5 rounded-xl bg-surface-low border border-outline-dim text-sm font-mono text-primary hover:underline break-all"
              >
                {repo.repo_id}
              </a>
            </li>
          ))}
        </ul>
        {error && <p className="text-xs text-error mt-2 m-0">{error}</p>}
        <div className="flex justify-end gap-3 mt-4">
          <button
            onClick={cancelHfAccess}
            className="px-4 py-2 bg-transparent text-text-muted border border-outline-dim rounded-xl text-sm font-semibold cursor-pointer hover:text-text transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={recheck}
            disabled={checking}
            className="px-5 py-2 bg-primary text-white border-none rounded-xl text-sm font-bold cursor-pointer disabled:opacity-40 disabled:cursor-default"
          >
            {checking ? 'Checking...' : `Check & ${verb.charAt(0).toUpperCase() + verb.slice(1)}`}
          </button>
        </div>
      </div>
    </div>
  )
}
