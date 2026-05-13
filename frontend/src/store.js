import { create } from 'zustand'

const getInitialTheme = () => {
  const saved = localStorage.getItem('spark-ai-hub-theme')
  if (saved === 'light' || saved === 'dark') return saved
  return 'dark'
}

export const useStore = create((set, get) => ({
  recipes: [],
  metrics: null,
  buildLogs: {},
  installing: {},  // slug -> true
  updating: {},    // slug -> true
  removing: {},    // slug -> true
  purging: {},     // slug -> true
  _inFlight: {},  // slug -> { starting, running, ready, installed } overrides during transitions
  _ws: null,
  selectedRecipe: null,
  containerLogs: {},
  // slug -> true when the last install attempt failed; used to keep build
  // logs visible (instead of flashing back to "Container not running") until
  // the user retries.
  lastInstallFailed: {},
  // When non-null, a global modal prompts for an HF token before the
  // pending action (`install` or `launch`) on the given slug proceeds.
  hfTokenRequest: null,
  _logWs: null,
  theme: getInitialTheme(),

  toggleTheme: () => {
    const next = get().theme === 'dark' ? 'light' : 'dark'
    localStorage.setItem('spark-ai-hub-theme', next)
    document.documentElement.setAttribute('data-theme', next)
    set({ theme: next })
  },

  setRecipes: (recipes) => set({ recipes }),
  setMetrics: (metrics) => set({ metrics }),

  selectRecipe: (slug) => {
    set({ selectedRecipe: slug })
  },

  clearRecipe: () => {
    get().disconnectLogs()
    set({ selectedRecipe: null })
  },

  connectLogs: (slug) => {
    get().disconnectLogs()
    const wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${wsProto}//${location.host}/ws/logs/${slug}`)
    set({
      _logWs: ws,
      containerLogs: { ...get().containerLogs, [slug]: [] },
    })

    ws.onmessage = (e) => {
      if (e.data === '[spark-ai-hub:ready]') {
        // Backend marked it ready; refetch so recipe.ready updates everywhere
        get().fetchRecipes()
        return
      }
      set((s) => {
        const prev = s.containerLogs[slug] || []
        const line = e.data
        // Detect progress lines (e.g. "50% Completed | 2/4", "Downloading: 45%")
        const isProgress = /\d+%/.test(line) && (/\|/.test(line) || /it\/s/.test(line) || /Completed/.test(line) || /Downloading/.test(line))
        if (isProgress) {
          // Find last progress line with same prefix (text before the percentage)
          const prefix = line.match(/^(.*?)\d+%/)?.[1] || ''
          const idx = prev.findLastIndex((l) => l.startsWith(prefix) && /\d+%/.test(l))
          if (idx >= 0) {
            const updated = [...prev]
            updated[idx] = line
            return { containerLogs: { ...s.containerLogs, [slug]: updated } }
          }
        }
        return { containerLogs: { ...s.containerLogs, [slug]: [...prev, line] } }
      })
    }

    ws.onerror = () => {
      console.warn('Container log WebSocket error')
    }

    ws.onclose = () => {
      set({ _logWs: null })
    }
  },

  resolveHfToken: async (token) => {
    const req = get().hfTokenRequest
    if (!req) return { ok: false, error: 'No pending request' }
    try {
      const res = await fetch('/api/system/hf-token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: token.trim() }),
      })
      if (!res.ok) return { ok: false, error: 'Failed to save token' }
    } catch {
      return { ok: false, error: 'Failed to save token' }
    }
    set({ hfTokenRequest: null })
    if (req.action === 'install') {
      get().installRecipe(req.slug)
    } else if (req.action === 'launch') {
      get().launchRecipe(req.slug)
    }
    return { ok: true }
  },

  cancelHfToken: () => set({ hfTokenRequest: null }),

  disconnectLogs: () => {
    const ws = get()._logWs
    if (ws && ws.readyState <= 1) {
      ws.close()
    }
    set({ _logWs: null })
  },

  addBuildLine: (slug, line) => set((s) => {
    const prev = s.buildLogs[slug] || []
    // Match Docker layer progress: " <hash> Downloading/Extracting/Waiting/Pull complete"
    const layerMatch = line.match(/^\s*([0-9a-f]{12})\s+(Downloading|Extracting|Verifying|Waiting|Pull complete|Already exists|Download complete|Pulling fs layer)/)
    if (layerMatch) {
      const layerId = layerMatch[1]
      // Find and replace the last line with the same layer ID
      const idx = prev.findLastIndex((l) => l.includes(layerId))
      if (idx >= 0) {
        const updated = [...prev]
        updated[idx] = line
        return { buildLogs: { ...s.buildLogs, [slug]: updated } }
      }
    }
    return { buildLogs: { ...s.buildLogs, [slug]: [...prev, line] } }
  }),

  fetchRecipes: async () => {
    try {
      const res = await fetch(`/api/recipes?t=${Date.now()}`)
      if (!res.ok) return
      const fresh = await res.json()
      const inFlight = get()._inFlight
      // Overlay in-flight state so polling can't flash wrong states
      const merged = fresh.map(r => inFlight[r.slug] ? { ...r, ...inFlight[r.slug] } : r)
      set({ recipes: merged })
    } catch (e) {
      console.error('Failed to fetch recipes:', e)
    }
  },

  installRecipe: async (slug) => {
    const recipe = get().recipes.find((r) => r.slug === slug)
    if (recipe?.requires_hf_token) {
      try {
        const res = await fetch('/api/system/hf-token')
        const { has_token } = await res.json()
        if (!has_token) {
          set({ hfTokenRequest: { slug, action: 'install' } })
          return
        }
      } catch (e) {
        console.warn('HF token check failed, proceeding anyway:', e)
      }
    }

    set((s) => {
      const failed = { ...s.lastInstallFailed }
      delete failed[slug]
      return {
        installing: { ...s.installing, [slug]: true },
        buildLogs: { ...s.buildLogs, [slug]: [] },
        lastInstallFailed: failed,
      }
    })

    try {
      await fetch(`/api/recipes/${slug}/install`, { method: 'POST' })
    } catch (e) {
      console.error('Install POST failed:', e)
      set((s) => { const { [slug]: _, ...rest } = s.installing; return { installing: rest } })
      return
    }

    // Connect WebSocket for live log streaming
    const wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${wsProto}//${location.host}/ws/build/${slug}`)
    set({ _ws: ws })

    ws.onmessage = (e) => {
      if (e.data === '[done]') {
        const lines = get().buildLogs[slug] || []
        const failed = lines.some((l) => /Install failed with exit code/i.test(l))
        set((s) => {
          const { [slug]: _, ...restInstalling } = s.installing
          return {
            installing: restInstalling,
            lastInstallFailed: failed
              ? { ...s.lastInstallFailed, [slug]: true }
              : s.lastInstallFailed,
          }
        })
        get().fetchRecipes()
        return
      }
      get().addBuildLine(slug, e.data)
    }

    ws.onerror = () => {
      console.warn('Build WebSocket error, falling back to polling')
      get()._pollBuildStatus(slug)
    }

    ws.onclose = () => {
      if (get().installing[slug]) {
        get()._pollBuildStatus(slug)
      }
    }
  },

  updateRecipe: async (slug) => {
    set((s) => ({ updating: { ...s.updating, [slug]: true }, buildLogs: { ...s.buildLogs, [slug]: [] } }))

    try {
      await fetch(`/api/recipes/${slug}/update`, { method: 'POST' })
    } catch (e) {
      console.error('Update POST failed:', e)
      set((s) => { const { [slug]: _, ...rest } = s.updating; return { updating: rest } })
      return
    }

    const wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${wsProto}//${location.host}/ws/build/${slug}`)

    ws.onmessage = (e) => {
      if (e.data === '[done]') {
        set((s) => { const { [slug]: _, ...rest } = s.updating; return { updating: rest } })
        get().fetchRecipes()
        setTimeout(() => get().connectLogs(slug), 1000)
        return
      }
      get().addBuildLine(slug, e.data)
    }

    ws.onerror = () => {
      console.warn('Update WebSocket error, falling back to polling')
      get()._pollBuildStatus(slug, 'updating')
    }

    ws.onclose = () => {
      if (get().updating[slug]) {
        get()._pollBuildStatus(slug, 'updating')
      }
    }
  },

  _pollBuildStatus: async (slug, stateKey = 'installing') => {
    const poll = async () => {
      try {
        const res = await fetch(`/api/recipes/${slug}/build-status`)
        if (!res.ok) return
        const data = await res.json()
        set((s) => ({
          buildLogs: { ...s.buildLogs, [slug]: data.lines },
        }))
        if (data.status === 'done') {
          set((s) => { const { [slug]: _, ...rest } = s[stateKey]; return { [stateKey]: rest } })
          get().fetchRecipes()
        } else {
          setTimeout(poll, 1000)
        }
      } catch {
        set({ [stateKey]: null })
      }
    }
    poll()
  },

  launchRecipe: async (slug) => {
    const override = { starting: true, running: false, ready: false }
    set({
      _inFlight: { ...get()._inFlight, [slug]: override },
      recipes: get().recipes.map(r => r.slug === slug ? { ...r, ...override } : r),
    })
    try {
      await fetch(`/api/recipes/${slug}/launch`, { method: 'POST' })
    } catch (e) {
      console.error('Launch failed:', e)
    } finally {
      set({ _inFlight: { ...get()._inFlight, [slug]: undefined } })
      await get().fetchRecipes()
    }
  },

  stopRecipe: async (slug) => {
    const override = { running: false, ready: false, starting: false }
    set({
      _inFlight: { ...get()._inFlight, [slug]: override },
      recipes: get().recipes.map(r => r.slug === slug ? { ...r, ...override } : r),
    })
    try {
      await fetch(`/api/recipes/${slug}/stop`, { method: 'POST' })
    } catch (e) {
      console.error('Stop failed:', e)
    } finally {
      set({ _inFlight: { ...get()._inFlight, [slug]: undefined } })
      await get().fetchRecipes()
    }
  },

  removeRecipe: async (slug) => {
    const override = { installed: false, running: false, ready: false, starting: false }
    set((s) => ({
      removing: { ...s.removing, [slug]: true },
      _inFlight: { ...s._inFlight, [slug]: override },
      recipes: s.recipes.map(r => r.slug === slug ? { ...r, ...override } : r),
    }))
    try {
      const res = await fetch(`/api/recipes/${slug}`, { method: 'DELETE' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
    } catch (e) {
      console.error('Remove failed:', e)
    } finally {
      set((s) => {
        const { [slug]: _, ...rest } = s.removing
        return { removing: rest, _inFlight: { ...s._inFlight, [slug]: undefined } }
      })
      await get().fetchRecipes()
    }
  },

  purgeRecipe: async (slug) => {
    set((s) => ({ purging: { ...s.purging, [slug]: true } }))
    try {
      const res = await fetch(`/api/recipes/${slug}/purge`, { method: 'POST' })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
    } catch (e) {
      console.error('Purge failed:', e)
    } finally {
      set((s) => { const { [slug]: _, ...rest } = s.purging; return { purging: rest } })
      await get().fetchRecipes()
    }
  },
}))
