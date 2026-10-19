import { useEffect, useRef, useState } from 'react'
import { useStore } from '../store'

// The companion to HfTokenModal, for the half of the problem a token cannot
// solve. A gated repo needs the account holding the token to have accepted
// that repo's agreement on HuggingFace, and there is no API to accept one —
// it is a real agreement, so the person has to read it. All this modal can do
// is say which account, link the repos, and check again when they come back.
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
  const [notYet, setNotYet] = useState(false)
  const [failed, setFailed] = useState(false)
  const [checking, setChecking] = useState(false)
  const busy = useRef(false)

  const recipe = recipes.find((r) => r.slug === request.slug)
  const verb = request.action === 'install' ? 'Install' : 'Launch'
  const repos = request.repos || []
  const pending = repos.filter((r) => !r.accessible)
  const buttons = [...new Set(pending.map((r) => r.button || 'Agree and access repository'))]
  const form = pending.some((r) => r.form)
  const manual = pending.some((r) => r.manual)

  // `quiet`: the check that runs on coming back to this tab. It only ever
  // moves things forward; "not yet" is said when the person asked.
  const check = async (quiet) => {
    if (busy.current) return
    busy.current = true
    setChecking(true)
    const result = await recheckHfAccess()
    busy.current = false
    if (result.ok) return
    setChecking(false)
    setFailed(Boolean(result.error))
    if (!quiet) setNotYet(!result.error)
  }

  // Accepting happens in another tab; coming back is the signal to look.
  useEffect(() => {
    const back = () => document.visibilityState === 'visible' && check(true)
    window.addEventListener('focus', back)
    document.addEventListener('visibilitychange', back)
    return () => {
      window.removeEventListener('focus', back)
      document.removeEventListener('visibilitychange', back)
    }
  })

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm animate-fadeIn">
      <div className="max-h-full w-full max-w-lg overflow-y-auto rounded-2xl border border-outline-dim bg-surface-high p-5 shadow-2xl sm:p-6">
        <h3 className="m-0 font-display text-xl font-bold tracking-tight text-text">
          Accept the terms on Hugging Face
        </h3>
        <p className="m-0 mt-1 text-sm text-text-muted">
          {recipe?.name || request.slug} can't download until you do.
        </p>

        <ol className="m-0 mt-6 flex list-none flex-col gap-6 p-0">
          <Step n="1" title={
            request.account
              ? <>Sign in to Hugging Face as <span className="text-primary">{request.account}</span></>
              : 'Sign in to Hugging Face with the account of your saved token'
          } />

          <Step n="2" title={
            <>
              {pending.length > 1 ? 'Open each page' : 'Open the page'}
              {form ? ', fill in the form and press ' : ' and press '}
              {buttons.map((b, i) => (
                <span key={b}>{i > 0 && ' or '}<span className="text-primary">{b}</span></span>
              ))}
            </>
          }>
            <ul className="m-0 flex list-none flex-col gap-2 p-0">
              {repos.map((repo) => (
                <li key={repo.repo_id}><RepoRow repo={repo} /></li>
              ))}
            </ul>
            {notYet && (
              <p className="m-0 mt-2 text-sm font-semibold text-error">
                Not accepted yet{request.account ? ` by ${request.account}` : ''}.
              </p>
            )}
            {failed && (
              <p className="m-0 mt-2 text-sm font-semibold text-error">Couldn't reach Hugging Face. Try again.</p>
            )}
          </Step>

          <Step
            n="3"
            title="Come back here"
            sub={manual
              ? `The owner approves each request by hand, so this can take a while. The ${verb.toLowerCase()} starts once it's approved.`
              : `The ${verb.toLowerCase()} starts by itself.`}
          />
        </ol>

        <div className="mt-6 flex flex-wrap justify-end gap-3">
          <button
            onClick={cancelHfAccess}
            className="cursor-pointer rounded-xl border border-outline-dim bg-transparent px-4 py-2.5 text-sm font-semibold text-text-muted transition-colors hover:text-text"
          >
            Cancel
          </button>
          <button
            onClick={() => check(false)}
            disabled={checking}
            className="cursor-pointer rounded-xl border-none bg-primary px-5 py-2.5 text-sm font-bold text-primary-on disabled:cursor-default disabled:opacity-40"
          >
            {checking ? 'Checking…' : `I accepted — ${verb}`}
          </button>
        </div>
      </div>
    </div>
  )
}

function Step({ n, title, sub, children }) {
  return (
    <li className="flex gap-4">
      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/15 font-display text-sm font-bold text-primary ring-1 ring-primary/40">
        {n}
      </span>
      <div className="min-w-0 flex-1 pt-0.5">
        <div className="text-base font-semibold text-text">{title}</div>
        {sub && <p className="m-0 mt-0.5 text-sm leading-6 text-text-muted">{sub}</p>}
        {children && <div className="mt-3">{children}</div>}
      </div>
    </li>
  )
}

// One gated repo: a big link to its page until it opens, then a tick.
function RepoRow({ repo }) {
  const name = (
    <span className="min-w-0 flex-1 break-all font-mono text-sm">{repo.repo_id}</span>
  )
  if (repo.accessible) {
    return (
      <div className="flex items-center gap-3 rounded-xl border border-outline-dim bg-surface-low px-4 py-3 text-text-muted">
        {name}
        <span className="shrink-0 text-sm font-bold text-success">✓ Accepted</span>
      </div>
    )
  }
  return (
    <a
      href={repo.url}
      target="_blank"
      rel="noreferrer"
      className="flex items-center gap-3 rounded-xl border border-primary/50 bg-primary/10 px-4 py-3 text-text no-underline transition-colors hover:bg-primary/20"
    >
      {name}
      <span className="shrink-0 text-sm font-bold text-primary">Open ↗</span>
    </a>
  )
}
