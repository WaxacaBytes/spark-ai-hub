import { useEffect, useState } from 'react'
import { Card } from './Account'

export default function Settings() {
  const [hasKey, setHasKey] = useState(null)
  const [key, setKey] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    fetch('/api/system/civitai-key')
      .then((res) => {
        if (!res.ok) throw new Error('Could not check the Civitai key.')
        return res.json()
      })
      .then((data) => setHasKey(data.has_key))
      .catch(() => setError('Could not check the Civitai key.'))
  }, [])

  const save = async (event) => {
    event.preventDefault()
    const token = key.trim()
    if (!token || saving) return
    setSaving(true)
    setError('')
    try {
      const res = await fetch('/api/system/civitai-key', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token }),
      })
      if (!res.ok) throw new Error('Could not save the Civitai key.')
      setHasKey(true)
      setKey('')
    } catch {
      setError('Could not save the Civitai key.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="px-6 py-6 pb-12 max-w-4xl mx-auto animate-fadeIn flex flex-col gap-4">
      <div className="mb-1">
        <h1 className="text-2xl font-bold tracking-tight font-display m-0">Hub settings</h1>
        <p className="text-sm text-text-dim m-0 mt-1">Settings shared across this Spark AI Hub. Only admins can change them.</p>
      </div>

      <Card title="Civitai API key" subtitle="Used when anyone on this Hub links a LoRA from Civitai that requires sign-in. This key belongs to the Hub, not to a user's files.">
        <form onSubmit={save} className="flex flex-col gap-3 max-w-lg">
          <div className="text-sm text-text-muted">
            Status: <span className={hasKey ? 'text-primary' : 'text-text-dim'}>
              {hasKey === null ? (error ? 'Unavailable' : 'Checking…') : hasKey ? 'Saved' : 'Not set'}
            </span>
          </div>
          <a
            href="https://civitai.com/user/account"
            target="_blank"
            rel="noopener noreferrer"
            className="text-sm text-primary hover:underline self-start"
          >
            Get a key from Civitai account settings ↗
          </a>
          <p className="text-xs text-text-dim m-0">Sign in to Civitai and open the API Keys section.</p>
          <label htmlFor="civitai-key" className="text-sm font-medium text-text">Civitai API key</label>
          <div className="flex flex-wrap gap-2">
            <input
              id="civitai-key"
              type="password"
              autoComplete="off"
              value={key}
              onChange={(event) => setKey(event.target.value)}
              placeholder={hasKey ? 'Enter a replacement key' : 'Paste the key'}
              className="min-w-0 flex-1 rounded-lg border border-outline bg-surface px-3 py-2 text-sm text-text"
            />
            <button type="submit" disabled={!key.trim() || saving}
              className="px-4 py-2 rounded-lg bg-primary text-primary-on text-sm font-semibold border-none cursor-pointer disabled:opacity-50">
              {saving ? 'Saving…' : 'Save key'}
            </button>
          </div>
          <p className="text-xs text-text-dim m-0">The saved key is never displayed again. Enter a new key to replace it.</p>
          {error && <p role="alert" className="text-sm text-error m-0">{error}</p>}
        </form>
      </Card>
    </div>
  )
}
