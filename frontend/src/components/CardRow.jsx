import { useCallback, useEffect, useRef, useState } from 'react'

// A horizontally scrolling shelf with arrow controls that hide when there is
// nothing further to scroll to.
export default function CardRow({ title, subtitle, actions, children }) {
  const scroller = useRef(null)
  const [atStart, setAtStart] = useState(true)
  const [atEnd, setAtEnd] = useState(true)

  const sync = useCallback(() => {
    const el = scroller.current
    if (!el) return
    setAtStart(el.scrollLeft <= 2)
    setAtEnd(el.scrollLeft + el.clientWidth >= el.scrollWidth - 2)
  }, [])

  useEffect(() => {
    const el = scroller.current
    if (!el) return
    sync()
    const ro = new ResizeObserver(sync)
    ro.observe(el)
    return () => ro.disconnect()
  }, [sync, children])

  const scrollBy = (dir) => {
    const el = scroller.current
    if (!el) return
    el.scrollBy({ left: dir * Math.max(el.clientWidth * 0.8, 200), behavior: 'smooth' })
  }

  const canScroll = !(atStart && atEnd)

  return (
    <section className="group/row">
      <div className="mb-3 flex flex-wrap items-end gap-x-3 gap-y-1 px-6">
        <div className="min-w-0">
          <h2 className="m-0 font-display text-base font-bold tracking-tight text-text">{title}</h2>
          {subtitle && <p className="m-0 mt-0.5 text-xs text-text-dim">{subtitle}</p>}
        </div>
        {actions && <div className="ml-auto flex flex-wrap items-center gap-2">{actions}</div>}
        {canScroll && (
          <div className={`flex items-center gap-1 ${actions ? '' : 'ml-auto'}`}>
            <ArrowButton dir="left" disabled={atStart} onClick={() => scrollBy(-1)} />
            <ArrowButton dir="right" disabled={atEnd} onClick={() => scrollBy(1)} />
          </div>
        )}
      </div>

      <div className="relative">
        <div
          ref={scroller}
          onScroll={sync}
          className="row-scroller flex gap-3 overflow-x-auto scroll-smooth px-6 pb-2"
        >
          {children}
        </div>
        {/* Edge fades hint that the shelf continues. */}
        {!atStart && <div className="pointer-events-none absolute inset-y-0 left-0 w-10 bg-gradient-to-r from-bg to-transparent" />}
        {!atEnd && <div className="pointer-events-none absolute inset-y-0 right-0 w-10 bg-gradient-to-l from-bg to-transparent" />}
      </div>
    </section>
  )
}

function ArrowButton({ dir, disabled, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={dir === 'left' ? 'Scroll left' : 'Scroll right'}
      className="flex h-7 w-7 items-center justify-center rounded-full border border-outline-dim bg-surface-high text-text-muted transition-all hover:border-text-dim hover:text-text disabled:cursor-default disabled:opacity-25 disabled:hover:border-outline-dim disabled:hover:text-text-muted"
    >
      <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        {dir === 'left' ? <polyline points="15 18 9 12 15 6" /> : <polyline points="9 18 15 12 9 6" />}
      </svg>
    </button>
  )
}
