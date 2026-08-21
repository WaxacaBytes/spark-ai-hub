import { useCallback, useEffect, useState } from 'react'
import { useAuth } from '../auth'
import { Avatar, Card, Field, Stat, fmt } from './Account'

/* Admin-only: who has access to this Hub, and what they have been using.
 *
 * This is the only page in the Hub that is not open to every approved
 * account — everything else (installing, launching, stopping) stays available
 * to any active user. */
export default function Users() {
  const me = useAuth((s) => s.user)
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [creating, setCreating] = useState(false)

  const load = useCallback(async () => {
    const res = await fetch('/api/admin/users')
    if (!res.ok) {
      setError(res.status === 403 ? 'Only an administrator can view this page.' : 'Could not load users.')
      return
    }
    setData(await res.json())
    setError(null)
  }, [])

  useEffect(() => { load() }, [load])

  if (error) return <div className="px-6 py-20 text-center text-text-dim text-sm">{error}</div>
  if (!data) return <div className="px-6 py-20 text-center text-text-dim text-sm">Loading users…</div>

  const pending = data.users.filter((u) => u.status === 'pending')
  const rest = data.users.filter((u) => u.status !== 'pending')

  return (
    <div className="px-6 py-6 pb-12 max-w-4xl mx-auto animate-fadeIn flex flex-col gap-4">
      <div className="flex items-end justify-between gap-3 flex-wrap mb-1">
        <div>
          <h1 className="text-2xl font-bold tracking-tight font-display m-0">Users</h1>
          <p className="text-sm text-text-dim m-0 mt-1">
            Approve access and see how the Spark is being used.
          </p>
        </div>
        <button
          onClick={() => setCreating(true)}
          className="px-3.5 py-2 rounded-xl bg-primary text-primary-on border-none text-xs font-bold cursor-pointer hover:opacity-90 transition-opacity"
        >
          Add user
        </button>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Stat label="Users" value={fmt(data.totals.users)} />
        <Stat label="Awaiting approval" value={fmt(data.totals.pending)} />
        <Stat label="Requests" value={fmt(data.totals.requests)} />
        <Stat label="Tokens" value={fmt(data.totals.total_tokens)} />
      </div>

      {pending.length > 0 && (
        <Card
          title={`Waiting for approval · ${pending.length}`}
          subtitle="These people signed up and cannot sign in until you let them."
        >
          <div className="flex flex-col gap-2">
            {pending.map((u) => (
              <UserRow key={u.id} user={u} me={me} onChanged={load} />
            ))}
          </div>
        </Card>
      )}

      <Card title="Accounts" subtitle="Sorted by how much of the Spark each account has used.">
        <div className="flex flex-col gap-2">
          {rest.map((u) => (
            <UserRow key={u.id} user={u} me={me} onChanged={load} />
          ))}
          {rest.length === 0 && (
            <p className="text-sm text-text-dim m-0">No approved accounts yet.</p>
          )}
        </div>
      </Card>

      {creating && (
        <CreateUserModal onClose={() => setCreating(false)} onCreated={() => { setCreating(false); load() }} />
      )}
    </div>
  )
}

/* ── one account ─────────────────────────────────────────────────────────── */

const STATUS_STYLE = {
  pending: 'bg-warning/15 text-warning',
  active: 'bg-primary/15 text-primary',
  rejected: 'bg-error-surface text-error',
}

function UserRow({ user, me, onChanged }) {
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(false)
  const isSelf = user.id === me?.id

  const patch = async (body, confirmText) => {
    if (confirmText && !window.confirm(confirmText)) return
    setBusy(true)
    try {
      const res = await fetch(`/api/admin/users/${user.id}`, {
        method: 'PATCH',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        window.alert(err.detail || 'Could not update the account.')
      }
      await onChanged()
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    if (!window.confirm(
      `Delete ${user.email}?\n\nTheir API key stops working immediately and their usage history is removed. This cannot be undone.`
    )) return
    setBusy(true)
    try {
      const res = await fetch(`/api/admin/users/${user.id}`, { method: 'DELETE' })
      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        window.alert(err.detail || 'Could not delete the account.')
      }
      await onChanged()
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="bg-surface-low rounded-xl border border-outline-dim overflow-hidden">
      <div className="flex items-center gap-3 px-3 py-2.5 flex-wrap">
        <Avatar user={user} size={36} />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-text truncate">
            {user.name || user.email}
            {isSelf && <span className="ml-2 text-[10px] text-text-dim font-label">you</span>}
          </div>
          <div className="text-[11px] text-text-dim truncate">{user.email}</div>
        </div>

        <div className="hidden sm:flex items-center gap-4 text-right">
          <Metric label="requests" value={fmt(user.usage.requests)} />
          <Metric label="tokens" value={fmt(user.usage.total_tokens)} />
          <Metric label="30d" value={fmt(user.usage.tokens_30d)} />
        </div>

        <span className={`text-[10px] font-bold font-label uppercase tracking-wide px-2 py-1 rounded-md ${STATUS_STYLE[user.status]}`}>
          {user.status}
        </span>
        {user.role === 'admin' && (
          <span className="text-[10px] font-bold font-label uppercase tracking-wide px-2 py-1 rounded-md bg-surface-highest text-text-muted">
            admin
          </span>
        )}

        {user.status === 'pending' ? (
          <div className="flex gap-2">
            <button disabled={busy} onClick={() => patch({ status: 'active' })} className={btnApprove}>
              Approve
            </button>
            <button
              disabled={busy}
              onClick={() => patch({ status: 'rejected' }, `Reject ${user.email}? They will not be able to sign in.`)}
              className={btnReject}
            >
              Reject
            </button>
          </div>
        ) : (
          <button onClick={() => setOpen(!open)} className={btnQuiet}>
            {open ? 'Close' : 'Manage'}
          </button>
        )}
      </div>

      {open && (
        <div className="px-3 pb-3 pt-1 border-t border-outline-dim flex flex-col gap-3">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-3">
            <Stat label="Requests" value={fmt(user.usage.requests)} />
            <Stat label="Tokens in" value={fmt(user.usage.prompt_tokens)} />
            <Stat label="Tokens out" value={fmt(user.usage.completion_tokens)} />
            <Stat label="Tokens · 24h" value={fmt(user.usage.tokens_24h)} />
          </div>
          <div className="text-[11px] text-text-dim">
            Joined {shortDate(user.created_at)}
            {user.last_login_at && ` · last signed in ${shortDate(user.last_login_at)}`}
            {user.usage.last_used_at && ` · last model call ${shortDate(user.usage.last_used_at)}`}
          </div>

          <div className="flex gap-2 flex-wrap">
            {user.status === 'active' ? (
              <button
                disabled={busy}
                onClick={() => patch({ status: 'rejected' }, `Revoke access for ${user.email}? They are signed out immediately and their API key stops working.`)}
                className={btnReject}
              >
                Revoke access
              </button>
            ) : (
              <button disabled={busy} onClick={() => patch({ status: 'active' })} className={btnApprove}>
                Restore access
              </button>
            )}
            <button
              disabled={busy}
              onClick={() => patch(
                { role: user.role === 'admin' ? 'user' : 'admin' },
                user.role === 'admin'
                  ? `Remove administrator rights from ${user.email}?`
                  : `Make ${user.email} an administrator? They will be able to approve and remove users, including you.`
              )}
              className={btnQuiet}
            >
              {user.role === 'admin' ? 'Remove admin' : 'Make admin'}
            </button>
            <ResetPassword userId={user.id} email={user.email} />
            {!isSelf && (
              <button disabled={busy} onClick={remove} className={btnReject}>Delete</button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function ResetPassword({ userId, email }) {
  const [value, setValue] = useState('')
  const [open, setOpen] = useState(false)
  const [msg, setMsg] = useState(null)

  if (!open) {
    return <button onClick={() => setOpen(true)} className={btnQuiet}>Set password</button>
  }

  const save = async () => {
    const res = await fetch(`/api/admin/users/${userId}`, {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ password: value }),
    })
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      setMsg(err.detail || 'Could not set the password.')
      return
    }
    setValue(''); setOpen(false); setMsg(null)
    window.alert(`Password updated. ${email} has been signed out and must sign in again with the new password.`)
  }

  return (
    <span className="flex items-center gap-2">
      <input
        type="text" value={value} onChange={(e) => setValue(e.target.value)}
        placeholder="New password" autoComplete="off"
        className="px-3 py-1.5 bg-surface-high rounded-lg text-text text-xs outline-none border border-outline-dim focus:border-primary/40 w-40"
      />
      <button onClick={save} disabled={value.length < 8} className={btnApprove}>Set</button>
      <button onClick={() => { setOpen(false); setMsg(null) }} className={btnQuiet}>Cancel</button>
      {msg && <span className="text-[11px] text-error">{msg}</span>}
    </span>
  )
}

/* ── create ──────────────────────────────────────────────────────────────── */

function CreateUserModal({ onClose, onCreated }) {
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState('user')
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e) => {
    e.preventDefault()
    setBusy(true); setError(null)
    const res = await fetch('/api/admin/users', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ name, email, password, role, status: 'active' }),
    })
    setBusy(false)
    if (!res.ok) {
      const err = await res.json().catch(() => ({}))
      setError(err.detail || 'Could not create the account.')
      return
    }
    onCreated()
  }

  return (
    <div
      className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center px-6"
      onClick={onClose}
    >
      <div className="w-full max-w-sm bg-surface rounded-2xl p-6" onClick={(e) => e.stopPropagation()}>
        <h3 className="font-semibold text-sm font-display m-0 mb-1">Add a user</h3>
        <p className="text-[11px] text-text-dim m-0 mb-4 leading-relaxed">
          The account is active right away — no approval step. Send them the email and
          password you set here; they can change both from their Account page.
        </p>
        <form onSubmit={submit} className="flex flex-col gap-3">
          <Field label="Name" value={name} onChange={setName} placeholder="Optional" />
          <Field label="Email" type="email" value={email} onChange={setEmail} required />
          <Field label="Password" type="text" value={password} onChange={setPassword}
                 hint="At least 8 characters." autoComplete="off" required />
          <label className="flex flex-col gap-1.5">
            <span className="text-[11px] font-semibold font-label text-text-dim uppercase tracking-wide">Role</span>
            <select
              value={role} onChange={(e) => setRole(e.target.value)}
              className="w-full px-3.5 py-2.5 bg-surface-high rounded-xl text-text text-sm outline-none border border-outline-dim focus:border-primary/40 transition-all"
            >
              <option value="user">User — full access to apps and models</option>
              <option value="admin">Admin — can also manage users</option>
            </select>
          </label>
          {error && (
            <div className="text-xs text-error bg-error-surface rounded-xl px-3 py-2.5">{error}</div>
          )}
          <div className="flex gap-2 mt-1">
            <button type="submit" disabled={busy}
                    className="flex-1 py-2.5 rounded-xl bg-primary text-primary-on border-none text-sm font-bold cursor-pointer hover:opacity-90 disabled:opacity-50 transition-opacity">
              {busy ? 'Creating…' : 'Create user'}
            </button>
            <button type="button" onClick={onClose}
                    className="px-4 py-2.5 rounded-xl bg-surface-high text-text border border-outline-dim text-sm font-semibold cursor-pointer hover:bg-surface-highest transition-colors">
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

/* ── bits ────────────────────────────────────────────────────────────────── */

const btnApprove = 'px-3 py-1.5 rounded-lg bg-primary text-primary-on border-none text-xs font-bold cursor-pointer hover:opacity-90 disabled:opacity-50 transition-opacity whitespace-nowrap'
const btnReject = 'px-3 py-1.5 rounded-lg bg-error-surface text-error border border-error/30 text-xs font-semibold cursor-pointer hover:bg-error/15 disabled:opacity-50 transition-colors whitespace-nowrap'
const btnQuiet = 'px-3 py-1.5 rounded-lg bg-surface-high text-text border border-outline-dim text-xs font-semibold cursor-pointer hover:bg-surface-highest transition-colors whitespace-nowrap'

function Metric({ label, value }) {
  return (
    <div>
      <div className="text-sm font-bold font-display text-text leading-tight">{value}</div>
      <div className="text-[10px] text-text-dim font-label uppercase tracking-wide">{label}</div>
    </div>
  )
}

function shortDate(s) {
  if (!s) return ''
  // SQLite hands back "YYYY-MM-DD HH:MM:SS" in UTC with no zone marker; say so
  // explicitly or the browser reads it as local time and shifts every date.
  const d = new Date(s.replace(' ', 'T') + 'Z')
  if (Number.isNaN(d.getTime())) return s
  return d.toLocaleDateString([], { year: 'numeric', month: 'short', day: 'numeric' })
}
