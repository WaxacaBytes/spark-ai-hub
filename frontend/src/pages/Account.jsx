import { useEffect, useState } from 'react'
import { useAuth } from '../auth'
import { copyText } from '../lib/clipboard'

/* Your own account: who you are, how you sign in, and the API key every
 * client on this Hub authenticates with. */
export default function Account() {
  const user = useAuth((s) => s.user)
  const refreshUser = useAuth((s) => s.refreshUser)
  const [me, setMe] = useState(null)

  useEffect(() => {
    let alive = true
    fetch('/api/auth/me')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => alive && d && setMe(d))
    return () => { alive = false }
  }, [])

  if (!me) {
    return <div className="px-6 py-20 text-center text-text-dim text-sm">Loading account…</div>
  }

  return (
    <div className="px-6 py-6 pb-12 max-w-2xl mx-auto animate-fadeIn flex flex-col gap-4">
      <div className="flex items-center gap-4 mb-2">
        <Avatar user={me} size={56} />
        <div className="min-w-0">
          <h1 className="text-2xl font-bold tracking-tight font-display m-0 truncate">
            {me.name || me.email}
          </h1>
          <p className="text-sm text-text-dim m-0 mt-1">
            {me.email}
            {me.role === 'admin' && (
              <span className="ml-2 text-[10px] font-bold font-label uppercase tracking-wide bg-primary/15 text-primary px-2 py-0.5 rounded-md align-middle">
                Admin
              </span>
            )}
          </p>
        </div>
      </div>

      <ApiKeyCard apiKey={me.api_key} onRotated={(k) => setMe({ ...me, api_key: k })} />
      <UsageCard usage={me.usage} />
      <ProfileCard me={me} onSaved={(u) => { setMe({ ...me, ...u }); refreshUser() }} />
      <PasswordCard />
      <SignOutCard user={user} />
    </div>
  )
}

/* ── API key ─────────────────────────────────────────────────────────────── */

function ApiKeyCard({ apiKey, onRotated }) {
  const [shown, setShown] = useState(false)
  const [copied, setCopied] = useState(false)
  const [rotating, setRotating] = useState(false)

  const copy = async () => {
    if (await copyText(apiKey)) {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    }
  }

  const rotate = async () => {
    if (!window.confirm(
      'Generate a new API key?\n\nEvery client still using the old key will stop working until you paste the new one in.'
    )) return
    setRotating(true)
    try {
      const res = await fetch('/api/auth/me/api-key/rotate', { method: 'POST' })
      if (res.ok) {
        const data = await res.json()
        onRotated(data.api_key)
        setShown(true)
      }
    } finally {
      setRotating(false)
    }
  }

  const masked = `${apiKey.slice(0, 8)}${'•'.repeat(24)}${apiKey.slice(-4)}`

  return (
    <Card
      title="Your API key"
      subtitle="One key for every model this Hub serves. Paste it into any OpenAI- or Anthropic-compatible client — it keeps working when the running model changes."
    >
      <div className="flex items-stretch gap-2">
        <code className="flex-1 px-3 py-2.5 rounded-xl bg-surface-low text-text border border-outline-dim text-xs font-mono overflow-x-auto whitespace-nowrap">
          {shown ? apiKey : masked}
        </code>
        <button onClick={() => setShown(!shown)} className={btnGhost} title={shown ? 'Hide' : 'Reveal'}>
          {shown ? 'Hide' : 'Show'}
        </button>
        <button onClick={copy} className={btnPrimary}>
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>

      <div className="mt-4 flex items-center justify-between gap-3 flex-wrap">
        <p className="text-[11px] text-text-dim m-0 leading-relaxed">
          Treat it like a password. Anyone with this key can use the models on this Spark as you.
        </p>
        <button onClick={rotate} disabled={rotating} className={btnDanger}>
          {rotating ? 'Generating…' : 'Generate new key'}
        </button>
      </div>
    </Card>
  )
}

/* ── usage ───────────────────────────────────────────────────────────────── */

function UsageCard({ usage }) {
  return (
    <Card title="Your usage" subtitle="Model calls made with your key or from this browser.">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Stat label="Requests" value={fmt(usage.requests)} />
        <Stat label="Tokens" value={fmt(usage.total_tokens)} />
        <Stat label="Tokens · 30d" value={fmt(usage.tokens_30d)} />
        <Stat label="Tokens · 24h" value={fmt(usage.tokens_24h)} />
      </div>
    </Card>
  )
}

/* ── profile ─────────────────────────────────────────────────────────────── */

function ProfileCard({ me, onSaved }) {
  const [name, setName] = useState(me.name || '')
  const [email, setEmail] = useState(me.email)
  const [currentPassword, setCurrentPassword] = useState('')
  const [state, setState] = useState({ error: null, ok: false, busy: false })

  const emailChanged = email.trim().toLowerCase() !== me.email.toLowerCase()
  const nameChanged = name !== (me.name || '')
  const dirty = emailChanged || nameChanged

  const save = async (e) => {
    e.preventDefault()
    setState({ error: null, ok: false, busy: true })
    const body = { name }
    if (emailChanged) {
      body.email = email
      body.current_password = currentPassword
    }
    const res = await fetch('/api/auth/me', {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      setState({ error: err.detail || 'Could not save.', ok: false, busy: false })
      return
    }
    const updated = await res.json()
    setCurrentPassword('')
    setState({ error: null, ok: true, busy: false })
    onSaved(updated)
  }

  return (
    <Card title="Profile">
      <form onSubmit={save} className="flex flex-col gap-3">
        <Field label="Name" value={name} onChange={setName} placeholder="Optional" />
        <Field label="Email" type="email" value={email} onChange={setEmail} required />
        {/* Only asked for when the email is actually changing — a stolen
            session must not be enough to move the account to a new address. */}
        {emailChanged && (
          <Field
            label="Current password" type="password"
            value={currentPassword} onChange={setCurrentPassword}
            hint="Required to change the email on your account."
            autoComplete="current-password" required
          />
        )}
        <FormFooter
          state={state} dirty={dirty}
          okText="Profile saved." label="Save changes"
        />
      </form>
    </Card>
  )
}

/* ── password ────────────────────────────────────────────────────────────── */

function PasswordCard() {
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [state, setState] = useState({ error: null, ok: false, busy: false })

  const save = async (e) => {
    e.preventDefault()
    if (newPassword !== confirm) {
      setState({ error: 'The two new passwords do not match.', ok: false, busy: false })
      return
    }
    setState({ error: null, ok: false, busy: true })
    const res = await fetch('/api/auth/me', {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      setState({ error: err.detail || 'Could not change the password.', ok: false, busy: false })
      return
    }
    setCurrentPassword(''); setNewPassword(''); setConfirm('')
    setState({ error: null, ok: true, busy: false })
  }

  return (
    <Card title="Password">
      <form onSubmit={save} className="flex flex-col gap-3">
        <Field label="Current password" type="password" value={currentPassword}
               onChange={setCurrentPassword} autoComplete="current-password" required />
        <Field label="New password" type="password" value={newPassword}
               onChange={setNewPassword} autoComplete="new-password"
               hint="At least 8 characters." required />
        <Field label="Confirm new password" type="password" value={confirm}
               onChange={setConfirm} autoComplete="new-password" required />
        <FormFooter
          state={state} dirty={Boolean(currentPassword && newPassword)}
          okText="Password changed." label="Change password"
        />
      </form>
    </Card>
  )
}

function SignOutCard() {
  const logout = useAuth((s) => s.logout)
  return (
    <Card title="Session">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <p className="text-[11px] text-text-dim m-0">
          Signs this browser out. Your API key keeps working.
        </p>
        <button onClick={logout} className={btnGhost}>Sign out</button>
      </div>
    </Card>
  )
}

/* ── shared bits ─────────────────────────────────────────────────────────── */

const btnPrimary = 'shrink-0 px-3.5 rounded-xl bg-primary text-primary-on border-none text-xs font-bold cursor-pointer hover:opacity-90 disabled:opacity-50 transition-opacity whitespace-nowrap'
const btnGhost = 'shrink-0 px-3.5 py-2 rounded-xl bg-surface-high text-text border border-outline-dim text-xs font-semibold cursor-pointer hover:bg-surface-highest transition-colors whitespace-nowrap'
const btnDanger = 'shrink-0 px-3.5 py-2 rounded-xl bg-error-surface text-error border border-error/30 text-xs font-semibold cursor-pointer hover:bg-error/15 disabled:opacity-50 transition-colors whitespace-nowrap'

export function Card({ title, subtitle, children }) {
  return (
    <div className="bg-surface rounded-2xl p-5">
      <h3 className="font-semibold text-sm font-display m-0">{title}</h3>
      {subtitle && <p className="text-[11px] text-text-dim m-0 mt-1 mb-3 leading-relaxed">{subtitle}</p>}
      <div className={subtitle ? '' : 'mt-3'}>{children}</div>
    </div>
  )
}

export function Stat({ label, value }) {
  return (
    <div className="bg-surface-low rounded-xl px-3 py-2.5 border border-outline-dim">
      <div className="text-base font-bold font-display text-text leading-tight">{value}</div>
      <div className="text-[10px] text-text-dim font-label uppercase tracking-wide mt-0.5">{label}</div>
    </div>
  )
}

export function Field({ label, hint, value, onChange, ...rest }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-[11px] font-semibold font-label text-text-dim uppercase tracking-wide">{label}</span>
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

function FormFooter({ state, dirty, okText, label }) {
  return (
    <div className="flex items-center gap-3 flex-wrap">
      <button type="submit" disabled={state.busy || !dirty}
              className={`${btnPrimary} py-2.5`}>
        {state.busy ? 'Saving…' : label}
      </button>
      {state.error && <span className="text-xs text-error">{state.error}</span>}
      {state.ok && <span className="text-xs text-primary">{okText}</span>}
    </div>
  )
}

export function Avatar({ user, size = 36 }) {
  const seed = user.email || ''
  // Deterministic hue from the email so each person keeps the same colour
  // everywhere they appear — here and in the admin user list.
  let h = 0
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) % 360
  const initials = (user.name || user.email || '?').trim().slice(0, 1).toUpperCase()
  return (
    <div
      className="shrink-0 rounded-2xl flex items-center justify-center font-bold font-display"
      style={{
        width: size, height: size, fontSize: size * 0.4,
        background: `hsl(${h} 55% 22%)`, color: `hsl(${h} 80% 72%)`,
      }}
    >
      {initials}
    </div>
  )
}

export function fmt(n) {
  const v = Number(n || 0)
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K`
  return String(v)
}
