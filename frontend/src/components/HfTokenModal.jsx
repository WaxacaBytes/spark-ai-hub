import { useEffect, useState } from 'react'
import { useStore } from '../store'

export default function HfTokenModal() {
  const request = useStore((s) => s.hfTokenRequest)
  const resolveHfToken = useStore((s) => s.resolveHfToken)
  const cancelHfToken = useStore((s) => s.cancelHfToken)
  const recipes = useStore((s) => s.recipes)
  const [token, setToken] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!request) {
      setToken('')
      setError('')
      setSaving(false)
    }
  }, [request])

  if (!request) return null

  const recipe = recipes.find((r) => r.slug === request.slug)
  const verb = request.action === 'install' ? 'install' : 'launch'

  const submit = async () => {
    if (!token.trim()) return
    setSaving(true)
    setError('')
    const result = await resolveHfToken(token)
    if (!result.ok) {
      setError(result.error || 'Failed to save token')
      setSaving(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm animate-fadeIn">
      <div className="bg-surface-high rounded-2xl p-6 w-full max-w-md shadow-2xl border border-outline-dim">
        <h3 className="text-lg font-bold text-text font-display m-0">HuggingFace Token Required</h3>
        <p className="text-sm text-text-dim mt-2 mb-4 leading-relaxed">
          {recipe?.name || request.slug} uses gated assets on HuggingFace. Paste your access token below to {verb} it.
          You can create one at{' '}
          <a href="https://huggingface.co/settings/tokens" target="_blank" rel="noreferrer" className="text-primary hover:underline">
            huggingface.co/settings/tokens
          </a>.
        </p>
        <input
          type="password"
          placeholder="hf_..."
          value={token}
          onChange={(e) => setToken(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submit()}
          className="w-full px-4 py-2.5 rounded-xl bg-surface-low text-text border border-outline-dim text-sm font-mono focus:outline-none focus:border-primary"
          autoFocus
        />
        {error && <p className="text-xs text-error mt-2 m-0">{error}</p>}
        <div className="flex justify-end gap-3 mt-4">
          <button
            onClick={cancelHfToken}
            className="px-4 py-2 bg-transparent text-text-muted border border-outline-dim rounded-xl text-sm font-semibold cursor-pointer hover:text-text transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={!token.trim() || saving}
            className="px-5 py-2 bg-primary text-white border-none rounded-xl text-sm font-bold cursor-pointer disabled:opacity-40 disabled:cursor-default"
          >
            {saving ? 'Saving...' : `Save & ${verb.charAt(0).toUpperCase() + verb.slice(1)}`}
          </button>
        </div>
      </div>
    </div>
  )
}
