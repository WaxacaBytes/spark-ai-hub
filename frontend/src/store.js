import { create } from 'zustand'

export const useStore = create((set, get) => ({
  recipes: [],
  metrics: null,
  buildLogs: {},
  installing: null,
  removing: null,
  _ws: null,
  selectedRecipe: null,
  containerLogs: {},
  _logWs: null,

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
      if (e.data === '[sparkforge:ready]') {
        // Backend marked it ready; refetch so recipe.ready updates everywhere
        get().fetchRecipes()
        return
      }
      set((s) => ({
        containerLogs: {
          ...s.containerLogs,
          [slug]: [...(s.containerLogs[slug] || []), e.data],
        },
      }))
    }

    ws.onerror = () => {
      console.warn('Container log WebSocket error')
    }

    ws.onclose = () => {
      set({ _logWs: null })
    }
  },

  disconnectLogs: () => {
    const ws = get()._logWs
    if (ws && ws.readyState <= 1) {
      ws.close()
    }
    set({ _logWs: null })
  },

  addBuildLine: (slug, line) => set((s) => ({
    buildLogs: {
      ...s.buildLogs,
      [slug]: [...(s.buildLogs[slug] || []), line],
    },
  })),

  fetchRecipes: async () => {
    try {
      const res = await fetch('/api/recipes')
      if (res.ok) set({ recipes: await res.json() })
    } catch (e) {
      console.error('Failed to fetch recipes:', e)
    }
  },

  installRecipe: async (slug) => {
    set({ installing: slug, buildLogs: { ...get().buildLogs, [slug]: [] } })

    try {
      await fetch(`/api/recipes/${slug}/install`, { method: 'POST' })
    } catch (e) {
      console.error('Install POST failed:', e)
      set({ installing: null })
      return
    }

    // Connect WebSocket for live log streaming
    const wsProto = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const ws = new WebSocket(`${wsProto}//${location.host}/ws/build/${slug}`)
    set({ _ws: ws })

    ws.onmessage = (e) => {
      if (e.data === '[done]') {
        set({ installing: null, _ws: null })
        get().fetchRecipes()
        return
      }
      get().addBuildLine(slug, e.data)
    }

    ws.onerror = () => {
      console.warn('Build WebSocket error, falling back to polling')
      get()._pollBuildStatus(slug)
    }

    ws.onclose = (e) => {
      if (get().installing === slug) {
        get()._pollBuildStatus(slug)
      }
    }
  },

  _pollBuildStatus: async (slug) => {
    const poll = async () => {
      try {
        const res = await fetch(`/api/recipes/${slug}/build-status`)
        if (!res.ok) return
        const data = await res.json()
        set((s) => ({
          buildLogs: { ...s.buildLogs, [slug]: data.lines },
        }))
        if (data.status === 'done') {
          set({ installing: null })
          get().fetchRecipes()
        } else {
          setTimeout(poll, 1000)
        }
      } catch {
        set({ installing: null })
      }
    }
    poll()
  },

  launchRecipe: async (slug) => {
    try {
      await fetch(`/api/recipes/${slug}/launch`, { method: 'POST' })
      get().fetchRecipes()
    } catch (e) {
      console.error('Launch failed:', e)
    }
  },

  stopRecipe: async (slug) => {
    try {
      await fetch(`/api/recipes/${slug}/stop`, { method: 'POST' })
      get().fetchRecipes()
    } catch (e) {
      console.error('Stop failed:', e)
    }
  },

  removeRecipe: async (slug) => {
    set({ removing: slug })
    try {
      const res = await fetch(`/api/recipes/${slug}`, { method: 'DELETE' })
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`)
      }
      get().fetchRecipes()
    } catch (e) {
      console.error('Remove failed:', e)
    } finally {
      set({ removing: null })
    }
  },
}))
