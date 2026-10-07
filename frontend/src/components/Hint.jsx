import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

// An explanation that appears on hover, focus or tap, right where the thing
// it explains is. A native `title` tooltip waits a second, shows nothing on
// some browsers and never on a phone, so this draws its own bubble. It is
// portalled to <body> with fixed positioning: the tables it sits in are laid
// out in CSS columns, which clip anything that pokes out of them.
//
// `tap`: a tap toggles the bubble. Off for controls whose tap already does
// something (a sort button sorts), where hover and focus still show it.

const WIDTH = 280
const MARGIN = 8

export default function Hint({ text, children, className = '', tap = true }) {
  const ref = useRef(null)
  const [pos, setPos] = useState(null)

  useEffect(() => {
    if (!pos) return undefined
    const hide = () => setPos(null)
    // A tap anywhere else closes it -- a phone has no mouse to move away.
    const away = (e) => { if (!ref.current?.contains(e.target)) hide() }
    // The page scrolls inside <main>, so listen in the capture phase.
    window.addEventListener('scroll', hide, true)
    window.addEventListener('resize', hide)
    document.addEventListener('pointerdown', away)
    return () => {
      window.removeEventListener('scroll', hide, true)
      window.removeEventListener('resize', hide)
      document.removeEventListener('pointerdown', away)
    }
  }, [pos])

  if (!text) return <span className={className}>{children}</span>

  const show = () => {
    const r = ref.current.getBoundingClientRect()
    const left = Math.min(Math.max(r.left + r.width / 2 - WIDTH / 2, MARGIN), window.innerWidth - WIDTH - MARGIN)
    // Below the anchor, unless that would run off the bottom of the window.
    const below = r.bottom + 180 < window.innerHeight
    setPos(below ? { left, top: r.bottom + 6 } : { left, bottom: window.innerHeight - r.top + 6 })
  }
  const hide = () => setPos(null)

  return (
    <span
      ref={ref}
      className={className}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
      // A tap opens it rather than toggling: on a touch screen the tap's own
      // emulated mouseenter has already opened it, and a toggle would close it.
      onClick={tap ? (e) => { e.stopPropagation(); show() } : undefined}
      tabIndex={tap ? 0 : undefined}
    >
      {children}
      {pos && createPortal(
        <span
          role="tooltip"
          style={{ position: 'fixed', width: WIDTH, ...pos }}
          className="pointer-events-none z-[100] block rounded-xl border border-outline bg-surface-highest px-3 py-2.5 text-left font-sans text-[11px] font-normal normal-case leading-5 tracking-normal text-text-muted shadow-2xl"
        >
          {text}
        </span>,
        document.body,
      )}
    </span>
  )
}
