import { useEffect, useState } from 'react'

/* The viewer's own Hub API key.
 *
 * Snippets and install one-liners have to carry a working key -- they are meant
 * to be pasted into a shell on another machine with nothing set up -- so more
 * than one component needs it. Fetched once and shared, rather than each place
 * calling /api/auth/me and inventing its own handling. Always render it through
 * lib/secret's maskIn, behind a Show. */
let cached = null
let inflight = null

export function useApiKey() {
  const [key, setKey] = useState(cached)
  useEffect(() => {
    if (cached) return
    let alive = true
    inflight = inflight || fetch('/api/auth/me')
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { cached = d?.api_key || null; return cached })
      .catch(() => null)
    inflight.then((k) => alive && k && setKey(k))
    return () => { alive = false }
  }, [])
  return key
}
