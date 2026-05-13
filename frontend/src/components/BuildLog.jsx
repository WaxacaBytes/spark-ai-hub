import { useEffect, useRef } from 'react'
import { useStore } from '../store'

export default function BuildLog() {
  const installing = useStore((s) => s.installing)
  const buildLogs = useStore((s) => s.buildLogs)
  const scrollRef = useRef(null)

  // installing is a map slug->true; pick the first active slug to show logs for
  const activeSlug = Object.keys(installing)[0] || null
  const lines = activeSlug ? (buildLogs[activeSlug] || []) : []

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [lines.length])

  if (!activeSlug) return null

  return (
    <div className="fixed bottom-0 left-0 right-0 h-56 bg-[#0c0c10] border-t border-spark/30 font-mono text-[11px] p-4 overflow-auto z-50" ref={scrollRef}>
      <div className="flex justify-between mb-2">
        <span className="text-spark font-bold text-xs">BUILD LOG — {activeSlug}</span>
        <span className="text-text-dim">⟳ Building...</span>
      </div>
      {lines.map((l, i) => (
        <div
          key={i}
          className={`leading-7 ${
            l.includes('successfully') || l.includes('✅') ? 'text-spark' :
            l.includes('error') || l.includes('failed') ? 'text-red-400' :
            'text-text-muted'
          }`}
        >
          {l}
        </div>
      ))}
      <div className="text-spark animate-pulse">▋</div>
    </div>
  )
}
