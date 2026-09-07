import { useRef, useState } from 'react'
import { useStore } from '../store'
import { copyText } from '../lib/clipboard'
import { hasSecret, maskIn } from '../lib/secret'
import { useApiKey } from '../hooks/useApiKey'

const KIND_LABEL = {
  // "origin" is the address this page was actually loaded from. The daemon
  // only lists it when it is not one of the names the box can see on itself
  // -- i.e. when a tunnel or reverse proxy is in front, which is exactly the
  // case where nothing computed on the server would have worked.
  origin: 'This address',
  mdns: 'mDNS name',
  tailscale: 'Tailscale',
  ip: 'LAN IP',
}

function CopyRow({ command, secret }) {
  const [state, setState] = useState('idle') // idle | copied | failed
  const [shown, setShown] = useState(false)
  const codeRef = useRef(null)
  // Same contract as Account's key card and the model page's benchmark
  // snippet: the key is masked on screen behind a Show, and Copy hands over
  // the real command. This modal is opened next to somebody most of the time
  // -- that is what it is for -- so the key not being readable over a shoulder
  // matters more here than anywhere else.
  const carries = hasSecret(command, secret)
  const display = maskIn(command, secret, shown)

  const copy = async () => {
    const ok = await copyText(command)
    if (ok) {
      setState('copied')
      setTimeout(() => setState('idle'), 1500)
      return
    }
    // Last resort: select the text so the user can hit Ctrl/Cmd+C. Reveal
    // first -- what is selected is what gets copied, and a masked selection
    // would hand over dots that fail against the Hub.
    setShown(true)
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
        {display}
      </code>
      {carries && (
        <button
          onClick={() => setShown(!shown)}
          className="shrink-0 px-2.5 rounded-xl bg-transparent border border-outline-dim text-text-dim hover:text-primary text-[11px] font-label cursor-pointer transition-colors"
          title={shown ? 'Hide your API key' : 'Reveal your API key'}
        >
          {shown ? 'Hide' : 'Show'}
        </button>
      )}
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
  const apiKey = useApiKey()

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
              <CopyRow command={info.commands.install} secret={apiKey} />
              <p className="text-xs text-text-dim mt-2 m-0 leading-relaxed">
                This line contains <strong className="text-text-muted">your personal API key</strong> —
                anyone who runs it can use this Spark as you. Send it only to your own devices.
              </p>
            </div>

            {/* Re-point an existing install */}
            <div>
              <div className="text-xs font-semibold text-text-muted font-label uppercase tracking-wide mb-2">
                Already have sah? Point it here
              </div>
              <div className="flex flex-col gap-2">
                <CopyRow command={info.commands.set_hub} />
                {info.commands.set_key && <CopyRow command={info.commands.set_key} secret={apiKey} />}
              </div>
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
                The installer saves all of these, in this order. If the server's IP
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
