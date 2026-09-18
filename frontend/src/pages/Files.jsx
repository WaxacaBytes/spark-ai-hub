import { useCallback, useEffect, useRef, useState } from 'react'
import { copyText } from '../lib/clipboard'

/* My files: the images, videos and songs this account made with the Hub's
 * media tools (/mcp), plus the files it uploaded for them to work on.
 *
 * Everything here is private to its owner (daemon/services/media_store.py) and
 * expires on its own; this page is where people see what they have, download
 * what they want to keep, delete the rest, and upload a local file to get a
 * link the tools accept. /upload lands here too. Only the signed-in account's
 * own files are ever listed, admins included. */

const FILTERS = [
  { id: 'all', label: 'All', test: () => true },
  { id: 'image', label: 'Images', test: (f) => f.kind === 'image' },
  { id: 'video', label: 'Videos', test: (f) => f.kind === 'video' },
  { id: 'audio', label: 'Music', test: (f) => f.kind === 'audio' },
  { id: 'upload', label: 'Uploads', test: (f) => f.upload },
]

const KIND_LABEL = { image: 'Image', video: 'Video', audio: 'Song' }

export default function Files() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [filter, setFilter] = useState('all')
  const [selected, setSelected] = useState(() => new Set())
  const [sending, setSending] = useState(0)     // uploads in flight
  const [failures, setFailures] = useState([])  // the last batch's refused files
  const [dragging, setDragging] = useState(false)
  const picker = useRef(null)

  const load = useCallback(async () => {
    const res = await fetch('/api/media')
    if (!res.ok) {
      setError('Could not load your files.')
      return
    }
    setData(await res.json())
    setError(null)
  }, [])

  useEffect(() => { load() }, [load])

  // A finished upload shows up as the first card (newest first), where its
  // link is; only a refused file needs a message of its own.
  const upload = useCallback(async (list) => {
    if (!list.length) return
    setFailures([])
    setSending((n) => n + list.length)
    for (const file of list) {
      try {
        const res = await fetch('/api/uploads', { method: 'POST', body: file })
        const body = await res.json().catch(() => ({}))
        if (!res.ok) throw new Error(body.detail || `Upload failed (HTTP ${res.status}).`)
      } catch (e) {
        setFailures((f) => [...f, `${file.name || 'Pasted image'}: ${e.message}`])
      }
      setSending((n) => n - 1)
      load()
    }
  }, [load])

  // Paste anywhere on the page, e.g. a screenshot straight from the clipboard.
  useEffect(() => {
    const onPaste = (e) => {
      const list = [...(e.clipboardData?.files || [])]
      if (list.length) upload(list)
    }
    document.addEventListener('paste', onPaste)
    return () => document.removeEventListener('paste', onPaste)
  }, [upload])

  const remove = async (names) => {
    const what = names.length === 1 ? 'this file' : `${names.length} files`
    if (!window.confirm(`Delete ${what}?\n\nLinks to it stop working. This cannot be undone.`)) return
    for (const name of names) {
      await fetch(`/api/media/${name}`, { method: 'DELETE' })
    }
    setSelected(new Set())
    load()
  }

  const toggle = (name) => setSelected((s) => {
    const next = new Set(s)
    next.has(name) ? next.delete(name) : next.add(name)
    return next
  })

  if (error) return <div className="px-6 py-20 text-center text-text-dim text-sm">{error}</div>
  if (!data) return <div className="px-6 py-20 text-center text-text-dim text-sm">Loading files…</div>

  const test = FILTERS.find((f) => f.id === filter).test
  const shown = data.files.filter(test)
  const chosen = data.files.filter((f) => selected.has(f.name))
  const allShown = shown.length > 0 && shown.every((f) => selected.has(f.name))

  return (
    <div
      className="px-6 py-6 pb-12 animate-fadeIn flex flex-col gap-4"
      onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
      onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) setDragging(false) }}
      onDrop={(e) => { e.preventDefault(); setDragging(false); upload([...e.dataTransfer.files]) }}
    >
      <div className="flex items-end justify-between gap-3 flex-wrap mb-1">
        <div>
          <h1 className="text-2xl font-bold tracking-tight font-display m-0">My files</h1>
          <p className="text-sm text-text-dim m-0 mt-1">
            Images, videos and songs you made with the Hub, and files you uploaded for it. Only you can
            open them. Upload a file to get a link you can give the image and video tools. Results are
            deleted after {data.results_days} days and uploads after {data.uploads_days}, so download
            anything you want to keep.
          </p>
        </div>
        <input
          ref={picker}
          type="file"
          multiple
          accept="image/*,video/mp4,video/quicktime"
          className="hidden"
          onChange={(e) => { upload([...e.target.files]); e.target.value = '' }}
        />
      </div>

      {failures.map((msg) => (
        <div key={msg} className="text-xs text-error bg-error-surface rounded-xl px-3 py-2.5">{msg}</div>
      ))}

      <div className="flex gap-2 overflow-x-auto pb-1">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            onClick={() => setFilter(f.id)}
            className={`shrink-0 cursor-pointer rounded-full border px-4 py-2 text-sm font-medium transition-all duration-200 ${
              filter === f.id
                ? 'border-primary bg-primary text-primary-on shadow-md shadow-primary/15'
                : 'border-outline bg-transparent text-text-muted hover:border-text-dim hover:text-text'
            }`}
          >
            {f.label} <span className="opacity-70">{data.files.filter(f.test).length}</span>
          </button>
        ))}
        {shown.length > 1 && (
          <button
            onClick={() => setSelected(allShown
              ? new Set([...selected].filter((name) => !shown.some((f) => f.name === name)))
              : new Set([...selected, ...shown.map((f) => f.name)]))}
            className={`${btnQuiet} ml-auto self-center`}
          >
            {allShown ? 'Deselect all' : `Select all ${shown.length}`}
          </button>
        )}
      </div>

      {/* Songs cannot be uploaded, so the Music filter gets no upload tile. */}
      {shown.length === 0 && filter === 'audio' ? (
        <div className="text-center py-14 text-text-dim">
          <div className="text-4xl mb-3">🗂️</div>
          <div className="text-base font-semibold font-display">Nothing here yet</div>
          <div className="text-sm mt-1">Songs you make with the Hub&apos;s music tool show up here.</div>
        </div>
      ) : (
        <FileGrid
          files={shown}
          selected={selected}
          onToggle={toggle}
          onDelete={remove}
          lead={filter !== 'audio' && (
            <UploadTile dragging={dragging} sending={sending} onPick={() => picker.current?.click()} />
          )}
        />
      )}
      {shown.length === 0 && filter !== 'audio' && (
        <p className="text-sm text-text-dim m-0">
          {filter === 'upload'
            ? 'No uploads yet.'
            : 'Nothing here yet. What you make with the Hub’s image, video and music tools shows up here too.'}
        </p>
      )}

      {chosen.length > 0 && (
        <div className="sticky bottom-4 self-center flex items-center gap-3 bg-surface-high border border-outline-dim rounded-2xl px-4 py-2.5 shadow-xl">
          <span className="text-sm font-label">
            {chosen.length} selected · {size(chosen.reduce((sum, f) => sum + f.bytes, 0))}
          </span>
          <button onClick={() => setSelected(new Set())} className={btnQuiet}>Clear</button>
          <button onClick={() => remove(chosen.map((f) => f.name))} className={btnReject}>Delete selected</button>
        </div>
      )}
    </div>
  )
}

function FileGrid({ files, selected, onToggle, onDelete, lead }) {
  const selecting = selected.size > 0
  return (
    <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(190px, 1fr))' }}>
      {lead}
      {files.map((f) => (
        <FileCard
          key={f.name}
          file={f}
          picked={selected.has(f.name)}
          selecting={selecting}
          onToggle={onToggle}
          onDelete={onDelete}
        />
      ))}
    </div>
  )
}

/* Hover shows a card's checkbox and actions; a touch screen, which cannot
 * hover, always shows them. Once anything is selected every checkbox shows and
 * a click on a thumbnail selects instead of opening, so picking many is quick.
 * The footer always toggles the checkbox. */
const onHover = 'opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 [@media(hover:none)]:opacity-100 transition-opacity'

function FileCard({ file, picked, selecting, onToggle, onDelete }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    if (await copyText(window.location.origin + file.path)) {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    }
  }

  return (
    <div className={`group relative bg-surface rounded-2xl overflow-hidden flex flex-col card-hover ${picked ? 'outline-2 outline-primary' : ''}`}>
      <Preview file={file} selecting={selecting} onToggle={onToggle} />
      <input
        type="checkbox"
        checked={picked}
        onChange={() => onToggle(file.name)}
        title="Select"
        className={`absolute top-2.5 left-2.5 w-4 h-4 cursor-pointer accent-[var(--primary)] ${picked || selecting ? '' : onHover}`}
      />
      <div className={`absolute top-2 right-2 flex gap-1 ${selecting ? 'hidden' : onHover}`}>
        <a href={file.path} download={file.name} title="Download" className={iconBtn}>
          <DownloadIcon />
        </a>
        <button onClick={copy} title={copied ? 'Link copied' : 'Copy link'} className={iconBtn}>
          {copied ? <CheckIcon /> : <LinkIcon />}
        </button>
        <button onClick={() => onDelete([file.name])} title="Delete" className={`${iconBtn} hover:!bg-error hover:!text-white`}>
          <TrashIcon />
        </button>
      </div>
      <div
        onClick={() => onToggle(file.name)}
        title={picked ? 'Unselect' : 'Select'}
        className="px-3.5 pt-3 pb-3.5 flex flex-col gap-0.5 flex-1 cursor-pointer select-none"
      >
        <div className="flex items-center gap-2 text-sm">
          <span className="font-semibold font-display">{KIND_LABEL[file.kind]}</span>
          {file.upload && (
            <span className="text-[10px] font-label font-medium text-secondary bg-secondary/10 px-2 py-0.5 rounded-full">upload</span>
          )}
          <span className="text-xs text-text-dim font-label ml-auto">{size(file.bytes)}</span>
        </div>
        <div className="text-xs text-text-dim">{when(file.created)}</div>
        <div className="text-[11px] text-text-dim font-label">{expiry(file.expires_at)}</div>
      </div>
    </div>
  )
}

/* The first tile of the grid: where a new upload lands, newest first. A file
 * can be dropped anywhere on the page or pasted; the tile lights up while one
 * is dragged over. */
function UploadTile({ dragging, sending, onPick }) {
  return (
    <button
      onClick={onPick}
      className={`min-h-[200px] rounded-2xl border-2 border-dashed flex flex-col items-center justify-center gap-3 px-4 text-center cursor-pointer transition-colors ${
        dragging
          ? 'border-primary bg-primary/5 text-text'
          : 'border-outline-dim bg-transparent text-text-dim hover:border-outline hover:text-text-muted'
      }`}
    >
      <span className={`w-12 h-12 rounded-full flex items-center justify-center transition-colors ${
        dragging ? 'bg-primary text-primary-on' : 'bg-surface-high text-text'
      }`}>
        <svg className="w-6 h-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <line x1="12" y1="5" x2="12" y2="19" />
          <line x1="5" y1="12" x2="19" y2="12" />
        </svg>
      </span>
      <span className="text-sm font-semibold font-display text-text">
        {sending ? (sending > 1 ? `Uploading ${sending} files…` : 'Uploading…') : 'Drop or paste an image or MP4'}
      </span>
      <span className="text-xs">{sending ? 'It shows up here when it is done' : 'or click to choose files'}</span>
    </button>
  )
}

function Preview({ file, selecting, onToggle }) {
  if (file.kind === 'audio') {
    return (
      <div className="aspect-square bg-surface-high flex flex-col items-center justify-center gap-4 px-4">
        <div className="text-4xl">🎵</div>
        <audio controls preload="none" src={file.path} className="w-full" />
      </div>
    )
  }
  const pick = (e) => {
    if (!selecting) return
    e.preventDefault()
    onToggle(file.name)
  }
  return (
    <a href={file.path} target="_blank" rel="noopener noreferrer" onClick={pick}
       className="block aspect-square overflow-hidden bg-surface-high">
      {file.kind === 'image' ? (
        <img src={`/api/media/${file.name}/thumb`} alt="" loading="lazy" className="w-full h-full object-cover block" />
      ) : (
        <video src={`${file.path}#t=0.5`} preload="metadata" muted className="w-full h-full object-cover block" />
      )}
    </a>
  )
}

function DownloadIcon() {
  return (
    <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 4v11" /><path d="m7 10 5 5 5-5" /><path d="M5 20h14" />
    </svg>
  )
}

function LinkIcon() {
  return (
    <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
      <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
    </svg>
  )
}

function CheckIcon() {
  return (
    <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M20 6 9 17l-5-5" />
    </svg>
  )
}

function TrashIcon() {
  return (
    <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 6h18" /><path d="M8 6V4h8v2" /><path d="M19 6l-1 14H6L5 6" />
    </svg>
  )
}

/* ── bits ────────────────────────────────────────────────────────────────── */

function size(bytes) {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(1)} MB`
  return `${Math.max(1, Math.round(bytes / 1e3))} KB`
}

function when(seconds) {
  return new Date(seconds * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })
}

function expiry(seconds) {
  const days = Math.ceil((seconds * 1000 - Date.now()) / 864e5)
  return days <= 1 ? 'Deleted within a day' : `Deleted in ${days} days`
}

const btnReject = 'px-3 py-1.5 rounded-lg bg-error-surface text-error border border-error/30 text-xs font-semibold cursor-pointer hover:bg-error/15 disabled:opacity-50 transition-colors whitespace-nowrap'
const iconBtn = 'w-8 h-8 rounded-lg flex items-center justify-center bg-black/55 text-white backdrop-blur-sm border-none cursor-pointer no-underline hover:bg-black/75 transition-colors'
const btnQuiet = 'px-3 py-1.5 rounded-lg bg-surface-high text-text border border-outline-dim text-xs font-semibold cursor-pointer hover:bg-surface-highest transition-colors whitespace-nowrap'
