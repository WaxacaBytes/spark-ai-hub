import { useState } from 'react'
import { useAuth } from '../auth'

/* The one screen an unauthenticated visitor can reach.
 *
 * It has three faces, chosen by what the Hub reports:
 *   - setup:    no accounts exist yet, so this visitor claims it as admin
 *   - sign in:  the normal case
 *   - request:  sign-up, which creates a pending account for an admin to approve
 */
export default function Login() {
  const needsSetup = useAuth((s) => s.needsSetup)
  const pendingNotice = useAuth((s) => s.pendingNotice)
  const login = useAuth((s) => s.login)
  const register = useAuth((s) => s.register)

  const [mode, setMode] = useState(needsSetup ? 'setup' : 'signin')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const isSetup = needsSetup || mode === 'setup'
  const isRegister = isSetup || mode === 'register'

  const submit = async (e) => {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      if (isRegister) await register({ email, password, name })
      else await login(email, password)
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  if (pendingNotice) {
    return (
      <Shell>
        <div className="text-center">
          <div className="w-12 h-12 rounded-2xl bg-warning/15 text-warning flex items-center justify-center mx-auto mb-4">
            <ClockIcon className="w-6 h-6" />
          </div>
          <h2 className="text-lg font-bold font-display m-0 mb-2">Waiting for approval</h2>
          <p className="text-sm text-text-muted m-0 leading-relaxed">{pendingNotice}</p>
          <button
            onClick={() => window.location.reload()}
            className="mt-6 w-full py-2.5 rounded-xl bg-surface-high text-text border border-outline-dim text-sm font-semibold cursor-pointer hover:bg-surface-highest transition-colors"
          >
            Back to sign in
          </button>
        </div>
      </Shell>
    )
  }

  return (
    <Shell>
      <h2 className="text-lg font-bold font-display m-0 mb-1">
        {isSetup ? 'Create the administrator account' : isRegister ? 'Request access' : 'Sign in'}
      </h2>
      <p className="text-sm text-text-dim m-0 mb-6 leading-relaxed">
        {isSetup
          ? 'This Hub has no accounts yet. The first one you create owns it — it can approve everyone who signs up after you.'
          : isRegister
            ? 'Your account will be created straight away, but an administrator has to approve it before you can sign in.'
            : 'Sign in to use the apps and models on this Spark.'}
      </p>

      <form onSubmit={submit} className="flex flex-col gap-3">
        {isRegister && (
          <Field label="Name" value={name} onChange={setName} autoComplete="name" placeholder="Optional" />
        )}
        <Field
          label="Email" type="email" value={email} onChange={setEmail}
          autoComplete="username" required
        />
        <Field
          label="Password" type="password" value={password} onChange={setPassword}
          autoComplete={isRegister ? 'new-password' : 'current-password'}
          hint={isRegister ? 'At least 8 characters.' : null}
          required
        />

        {error && (
          <div className="text-xs text-error bg-error-surface rounded-xl px-3 py-2.5 leading-relaxed">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={busy}
          className="mt-1 w-full py-2.5 rounded-xl bg-primary text-primary-on border-none text-sm font-bold cursor-pointer hover:opacity-90 disabled:opacity-50 disabled:cursor-default transition-opacity"
        >
          {busy ? 'Working…' : isSetup ? 'Create account & sign in' : isRegister ? 'Request access' : 'Sign in'}
        </button>
      </form>

      {!needsSetup && (
        <button
          onClick={() => { setMode(isRegister ? 'signin' : 'register'); setError(null) }}
          className="mt-4 w-full text-xs text-text-dim bg-transparent border-none cursor-pointer hover:text-text transition-colors font-label"
        >
          {isRegister ? 'Already have an account? Sign in' : "Don't have an account? Request access"}
        </button>
      )}
    </Shell>
  )
}

function Shell({ children }) {
  return (
    <div className="bg-bg text-text min-h-screen flex items-center justify-center px-6 animate-fadeIn">
      <div className="w-full max-w-sm">
        <div className="flex items-center gap-3 mb-6">
          <img
            src="/brand/spark-ai-hub-mark.svg"
            alt=""
            className="w-11 h-11 rounded-2xl bg-gradient-to-br from-[#152608] to-[#0A1404] p-1.5"
          />
          <div>
            <div className="text-base font-bold tracking-tight font-display">Spark AI Hub</div>
            <div className="text-[11px] text-text-dim font-label">NVIDIA DGX Spark</div>
          </div>
        </div>
        <div className="bg-surface rounded-2xl p-6">{children}</div>
      </div>
    </div>
  )
}

function Field({ label, hint, value, onChange, ...rest }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-[11px] font-semibold font-label text-text-dim uppercase tracking-wide">
        {label}
      </span>
      <input
        {...rest}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full px-3.5 py-2.5 bg-surface-high rounded-xl text-text text-sm outline-none border border-outline-dim focus:border-primary/40 focus:ring-2 focus:ring-primary/10 placeholder:text-text-dim transition-all"
      />
      {hint && <span className="text-[11px] text-text-dim">{hint}</span>}
    </label>
  )
}

function ClockIcon(props) {
  return (
    <svg {...props} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </svg>
  )
}
