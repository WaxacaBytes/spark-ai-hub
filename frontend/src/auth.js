import { create } from 'zustand'

async function readError(res, fallback) {
  try {
    const body = await res.json()
    return body.detail || body.message || fallback
  } catch {
    return fallback
  }
}

export const useAuth = create((set, get) => ({
  // `loading` covers the first /api/auth/state round-trip only. Until it
  // resolves we render nothing, so the app never flashes the sign-in screen at
  // someone who is in fact already signed in.
  loading: true,
  authEnabled: true,
  needsSetup: false,
  user: null,
  // Set after a successful sign-up that landed in the approval queue, so the
  // screen can explain what happens next instead of just refusing.
  pendingNotice: null,

  isAdmin: () => get().user?.role === 'admin',

  bootstrap: async () => {
    try {
      const res = await fetch('/api/auth/state')
      const data = await res.json()
      set({
        loading: false,
        authEnabled: data.auth_enabled,
        needsSetup: data.needs_setup,
        user: data.user,
      })
    } catch {
      set({ loading: false })
    }
  },

  // Called by the global 401 interceptor when a session expires or is revoked
  // mid-visit — the user is simply returned to the sign-in screen.
  clearSession: () => {
    if (get().user) set({ user: null })
  },

  login: async (email, password) => {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ email, password }),
    })
    if (!res.ok) throw new Error(await readError(res, 'Could not sign in.'))
    const data = await res.json()
    set({ user: data.user, pendingNotice: null, needsSetup: false })
    return data.user
  },

  register: async ({ email, password, name }) => {
    const res = await fetch('/api/auth/register', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ email, password, name }),
    })
    if (!res.ok) throw new Error(await readError(res, 'Could not create the account.'))
    const data = await res.json()
    if (data.status === 'pending') {
      set({ pendingNotice: data.message, needsSetup: false })
      return null
    }
    set({ user: data.user, pendingNotice: null, needsSetup: false })
    return data.user
  },

  logout: async () => {
    try {
      await fetch('/api/auth/logout', { method: 'POST' })
    } finally {
      set({ user: null, pendingNotice: null })
    }
  },

  // Re-read the signed-in account after the user edits it, so the header and
  // the admin nav item reflect a role or email change immediately.
  refreshUser: async () => {
    const res = await fetch('/api/auth/me')
    if (!res.ok) return null
    const data = await res.json()
    set({ user: { ...get().user, ...data } })
    return data
  },
}))
