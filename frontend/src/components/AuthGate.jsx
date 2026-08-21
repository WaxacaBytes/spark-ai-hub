import { useEffect } from 'react'
import { useAuth } from '../auth'
import Login from '../pages/Login'

/* Decides whether the Hub itself is allowed to render.
 *
 * Wrapping App rather than living inside it matters: App polls /api/recipes,
 * opens the metrics WebSocket and fetches connect info on mount, and none of
 * that should fire — or 401 — before anyone is signed in.
 */
export default function AuthGate({ children }) {
  const loading = useAuth((s) => s.loading)
  const authEnabled = useAuth((s) => s.authEnabled)
  const user = useAuth((s) => s.user)
  const bootstrap = useAuth((s) => s.bootstrap)

  useEffect(() => { bootstrap() }, [bootstrap])

  // A blank frame, not a spinner: the state usually resolves in one local
  // round-trip, and a flashed spinner reads worse than nothing.
  if (loading) return <div className="bg-bg min-h-screen" />

  if (!authEnabled || user) return children

  return <Login />
}
