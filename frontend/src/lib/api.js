import { useAuth } from '../auth'

// One global place to notice that our session stopped being valid.
//
// The Hub polls /api/recipes every few seconds from App, plus metrics over a
// WebSocket, plus whatever page is open — so rather than teach each of those
// call sites about 401s, wrap fetch once. When an admin revokes an account or
// a session expires, the very next poll bounces the user to the sign-in
// screen instead of leaving them staring at a frozen, empty catalog.
export function installAuthInterceptor() {
  const original = window.fetch.bind(window)

  window.fetch = async (input, init) => {
    const res = await original(input, init)
    if (res.status !== 401) return res

    const url = typeof input === 'string' ? input : input?.url || ''
    // Only our own API speaks for our session; a 401 from anything else
    // (an upstream app's endpoint, say) says nothing about the Hub login.
    const isHubApi = url.startsWith('/api/') || url.includes(`${window.location.origin}/api/`)
    // The sign-in routes 401 on bad credentials — that is the form's error to
    // show, not a reason to tear down a session that never existed.
    const isAuthRoute = /\/api\/auth\/(login|register|state)$/.test(url)
    if (isHubApi && !isAuthRoute) useAuth.getState().clearSession()
    return res
  }
}
