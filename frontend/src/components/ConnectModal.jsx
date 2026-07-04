import { useRef, useState } from 'react'
import { useStore } from '../store'

const KIND_LABEL = {
  mdns: 'mDNS name',
  tailscale: 'Tailscale',
  ip: 'LAN IP',
}

// The Hub is served over plain HTTP on the LAN, which is NOT a secure context,
// so navigator.clipboard is unavailable. Fall back to a temp-textarea +
// execCommand('copy'), which works over HTTP.
async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    /* fall through to the legacy path */
  }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.setAttribute('readonly', '')
    ta.style.position = 'fixed'
    ta.style.top = '-9999px'
    document.body.appendChild(ta)
    ta.select()
    ta.setSelectionRange(0, text.length)
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  }
}

function CopyRow({ command }) {
  const [state, setState] = useState('idle') // idle | copied | failed
  const codeRef = useRef(null)

  const copy = async () => {
    const ok = await copyText(command)
    if (ok) {
      setState('copied')
      setTimeout(() => setState('idle'), 1500)
      return
    }
    // Last resort: select the text so the user can hit Ctrl/Cmd+C.
    const el = codeRef.current
    if (el) {
      const range = document.createRange()
      range.selectNodeContents(el)
      const sel = window.getSelection()
      sel.removeAllRanges()
      sel.addRange(range)
    }
    setState('failed')
    setTimeout(() => setState('idle'), 3000)
  }

  const label = state === 'copied' ? 'Copied' : state === 'failed' ? 'Press ⌘/Ctrl+C' : 'Copy'

  return (
    <div className="flex items-stretch gap-2">
      <code
        ref={codeRef}
        className="flex-1 px-3 py-2.5 rounded-xl bg-surface-low text-text border border-outline-dim text-xs font-mono overflow-x-auto whitespace-nowrap"
      >
        {command}
      </code>
      <button
        onClick={copy}
        className="shrink-0 px-3 rounded-xl bg-primary text-primary-on border-none text-xs font-bold cursor-pointer hover:opacity-90 transition-opacity whitespace-nowrap"
        title="Copy to clipboard"
      >
        {label}
      </button>
    </div>
  )
}

export default function ConnectModal() {
  const open = useStore((s) => s.connectOpen)
  const info = useStore((s) => s.connectInfo)
  const close = useStore((s) => s.closeConnect)

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm animate-fadeIn"
      onClick={close}
    >
      <div
        className="bg-surface-high rounded-2xl p-6 w-full max-w-lg shadow-2xl border border-outline-dim max-h-[90vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between">
          <div>
            <h3 className="text-lg font-bold text-text font-display m-0">Connect an app to your model</h3>
            <p className="text-sm text-text-dim mt-1 mb-0 leading-relaxed">
              Point your coding agents at the model this Hub is serving. Install the{' '}
              <code className="font-mono text-text">sah</code> CLI on a device, then launch any
              supported agent — it wires to whichever Ready-to-Serve model is running.
            </p>
          </div>
          <button
            onClick={close}
            className="shrink-0 -mt-1 -mr-1 w-8 h-8 rounded-lg bg-transparent text-text-dim border-none cursor-pointer hover:text-text hover:bg-surface transition-colors text-xl leading-none"
            title="Close"
          >
            ×
          </button>
        </div>

        {!info ? (
          <p className="text-sm text-text-dim mt-6">Loading connection details…</p>
        ) : (
          <div className="flex flex-col gap-5 mt-5">
            {/* Install one-liner */}
            <div>
              <div className="text-xs font-semibold text-text-muted font-label uppercase tracking-wide mb-2">
                Install on a new device
              </div>
              <CopyRow command={info.commands.install} />
            </div>

            {/* Re-point an existing install */}
            <div>
              <div className="text-xs font-semibold text-text-muted font-label uppercase tracking-wide mb-2">
                Already have sah? Point it here
              </div>
              <CopyRow command={info.commands.set_hub} />
            </div>

            {/* Reachable addresses */}
            <div>
              <div className="text-xs font-semibold text-text-muted font-label uppercase tracking-wide mb-2">
                Reachable addresses
              </div>
              <div className="flex flex-col gap-2">
                {info.candidates.map((c) => (
                  <div
                    key={c.url}
                    className="flex items-start gap-3 px-3 py-2.5 rounded-xl bg-surface border border-outline-dim"
                  >
                    <span
                      className={`shrink-0 mt-0.5 text-[10px] font-bold font-label px-1.5 py-0.5 rounded ${
                        c.recommended
                          ? 'bg-primary text-primary-on'
                          : 'bg-surface-high text-text-dim'
                      }`}
                    >
                      {KIND_LABEL[c.kind] || c.kind}
                    </span>
                    <div className="min-w-0">
                      <div className="text-sm font-mono text-text break-all">
                        {c.url}
                        {c.recommended && (
                          <span className="ml-2 text-[10px] text-primary font-label font-bold">
                            RECOMMENDED
                          </span>
                        )}
                      </div>
                      <div className="text-xs text-text-dim mt-0.5">{c.note}</div>
                    </div>
                  </div>
                ))}
              </div>
              <p className="text-xs text-text-dim mt-2 m-0 leading-relaxed">
                The installer saves all of these (stable name first). If the server's IP
                changes, <code className="font-mono text-text">sah</code> automatically
                falls back to the name that still works — no reconfiguration needed.
              </p>
            </div>

            {/* Supported agents */}
            {info.agents?.length > 0 && (
              <div>
                <div className="text-xs font-semibold text-text-muted font-label uppercase tracking-wide mb-2">
                  Supported agents
                </div>
                <div className="flex flex-col gap-1.5">
                  {info.agents.map((a) => (
                    <div
                      key={a.name}
                      className="flex items-center gap-3 px-3 py-2 rounded-xl bg-surface border border-outline-dim"
                    >
                      <span className="shrink-0 text-sm text-text font-medium w-40 truncate">
                        {a.name}
                      </span>
                      <span className="shrink-0 text-[10px] font-bold font-label px-1.5 py-0.5 rounded bg-surface-high text-text-dim">
                        {a.kind}
                      </span>
                      <code className="ml-auto text-xs font-mono text-text-muted truncate">
                        {a.command}
                      </code>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
