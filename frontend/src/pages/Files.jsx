import { Suspense, lazy, useCallback, useEffect, useRef, useState } from 'react'
import { copyText } from '../lib/clipboard'
import { size } from '../lib/size'

const MeshView = lazy(() => import('../components/MeshView'))

/* My files: the images, videos, 3D models and songs this account made with the Hub's
 * media tools (/mcp), plus the files it uploaded for them to work on.
 *
 * Everything here is private to its owner (daemon/services/media_store.py);
 * everything stays until deleted. This page is where people see what they have, download
 * what they want to keep, delete the rest, and upload a local file to get a
 * link the tools accept. /upload lands here too. Only the signed-in account's
 * own files are ever listed, admins included. Each result carries the model
 * that made it, so a picture worth repeating can be traced back to one. */

const FILTERS = [
  { id: 'all', label: 'All', test: () => true },
  { id: 'image', label: 'Images', test: (f) => f.kind === 'image' },
  { id: 'video', label: 'Videos', test: (f) => f.kind === 'video' },
  { id: 'mesh', label: '3D', test: (f) => f.kind === 'mesh' },
  { id: 'audio', label: 'Music', test: (f) => f.kind === 'audio' },
  { id: 'upload', label: 'Uploads', test: (f) => f.upload },
]

const KIND_LABEL = { image: 'Image', video: 'Video', mesh: '3D model', audio: 'Song' }

export default function Files() {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [filter, setFilter] = useState('all')
  const [selected, setSelected] = useState(() => new Set())
  const [sending, setSending] = useState(0)     // uploads in flight
  const [failures, setFailures] = useState([])  // the last batch's refused files
  const [dragging, setDragging] = useState(false)
  const [viewing, setViewing] = useState(null)  // the file name open in the viewer
  const [details, setDetails] = useState(false)  // the viewer's details panel, kept while on the page
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

  // A 3D model's card shows a still of the model itself. The daemon cannot
  // draw one, so the first page to list a model without one draws it here,
  // one model at a time, and hands it back; every later visit just shows it.
  const drawing = useRef(new Set())
  useEffect(() => {
    const todo = (data?.files || []).filter((f) => f.kind === 'mesh' && !f.rendered && !drawing.current.has(f.name))
    if (!todo.length) return
    todo.forEach((f) => drawing.current.add(f.name))
    ;(async () => {
      const { snapshot } = await import('../lib/mesh3d')
      for (const f of todo) {
        try {
          const res = await fetch(`/api/media/${f.name}/thumb`, { method: 'PUT', body: await snapshot(f.path) })
          if (!res.ok) continue
          setData((d) => ({ ...d, files: d.files.map((x) => (x.name === f.name ? { ...x, rendered: true } : x)) }))
        } catch {
          // No WebGL, or the model would not load: the card keeps its picture.
        }
      }
    })()
  }, [data])

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
  const viewable = shown.filter((f) => f.kind !== 'audio')
  const viewIndex = viewable.findIndex((f) => f.name === viewing)

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
            Images, videos, 3D models and songs you made with the Hub, and files you uploaded for it. Only you can
            open them. Upload a file to get a link you can give the image and video tools. Everything here
            stays until you delete it.
          </p>
        </div>
        <input
          ref={picker}
          type="file"
          multiple
          accept="image/*,video/mp4,video/quicktime,audio/*"
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
          onOpen={setViewing}
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

      {viewIndex >= 0 && (
        <Viewer
          files={viewable}
          index={viewIndex}
          onIndex={(i) => setViewing(viewable[i].name)}
          onClose={() => setViewing(null)}
          details={details}
          onDetails={setDetails}
        />
      )}
    </div>
  )
}

function FileGrid({ files, selected, onToggle, onDelete, onOpen, lead }) {
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
          onOpen={onOpen}
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

function FileCard({ file, picked, selecting, onToggle, onDelete, onOpen }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    if (await copyText(window.location.origin + file.path)) {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    }
  }

  return (
    <div className={`group relative bg-surface rounded-2xl overflow-hidden flex flex-col card-hover ${picked ? 'outline-2 outline-primary' : ''}`}>
      <Preview file={file} selecting={selecting} onToggle={onToggle} onOpen={onOpen} />
      <input
        type="checkbox"
        checked={picked}
        onChange={() => onToggle(file.name)}
        title="Select"
        className={`absolute top-2.5 left-2.5 w-4 h-4 cursor-pointer accent-[var(--primary)] ${picked || selecting ? '' : onHover}`}
      />
      <div className={`absolute top-2 right-2 flex gap-1 ${selecting ? 'hidden' : onHover}`}>
        {file.kind !== 'audio' && (
          <a href={file.path} target="_blank" rel="noopener noreferrer" title="Open in a new tab" className={iconBtn}>
            <NewTabIcon />
          </a>
        )}
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
        {file.model && (
          <div className="text-xs text-text-muted truncate" title={file.model}>{file.model}</div>
        )}
        <div className="text-xs text-text-dim">{when(file.created)}</div>
        {shapeOf(file) && (
          <div className="text-sm font-semibold font-label text-text whitespace-nowrap truncate">{shapeOf(file)}</div>
        )}
        {file.seconds != null && <div className="text-xs text-text-dim whitespace-nowrap truncate">Took {took(file.seconds)}</div>}
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

function Preview({ file, selecting, onToggle, onOpen }) {
  if (file.kind === 'audio') {
    return (
      <div className="aspect-square bg-surface-high flex flex-col items-center justify-center gap-4 px-4">
        <div className="text-4xl">🎵</div>
        <audio controls preload="none" src={file.path} className="w-full" />
      </div>
    )
  }
  // A plain click opens the viewer; Ctrl/Cmd/middle click still opens a new tab.
  const pick = (e) => {
    if (e.ctrlKey || e.metaKey || e.shiftKey) return
    e.preventDefault()
    selecting ? onToggle(file.name) : onOpen(file.name)
  }
  return (
    <a href={file.path} target="_blank" rel="noopener noreferrer" onClick={pick}
       className="relative block aspect-square overflow-hidden bg-surface-high">
      {file.kind === 'video' ? (
        <video src={`${file.path}#t=0.5`} preload="metadata" muted className="w-full h-full object-cover block" />
      ) : (
        // A 3D model's is a still of the model once one is drawn (above), and
        // a fresh URL then, so the cached picture it was made from goes.
        <img src={`/api/media/${file.name}/thumb${file.rendered ? '?render' : ''}`} alt="" loading="lazy"
             className="w-full h-full object-cover block" />
      )}
      {(file.orbit || file.kind === 'mesh') && (
        <span className="absolute bottom-2 left-2 text-xs font-semibold font-label text-white bg-black/60 px-2 py-0.5 rounded-full">
          {file.orbit ? '360°' : '3D'}
        </span>
      )}
    </a>
  )
}

/* The picture or clip at its own size, shrunk only to fit the screen, with
 * arrows (and the ← → keys) through everything the filter shows. The (i)
 * button (or the I key) shows and hides its details. Esc, the X or a click
 * beside it closes. */
function Viewer({ files, index, onIndex, onClose, details, onDetails }) {
  const file = files[index]
  const [mesh, setMesh] = useState(null)   // { name, vertices, triangles } once a 3D model is loaded
  const step = useCallback((d) => onIndex((index + d + files.length) % files.length), [index, files.length, onIndex])

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') onClose()
      else if (e.key === 'ArrowLeft') step(-1)
      else if (e.key === 'ArrowRight') step(1)
      else if (e.key === 'i' || e.key === 'I') onDetails(!details)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [step, onClose, details, onDetails])

  useEffect(() => {
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = overflow }
  }, [])

  const stop = (e) => e.stopPropagation()
  const fit = 'block max-w-[calc(100vw-2rem)] max-h-[calc(100dvh-2rem)]'
  const onMesh = useCallback((counts) => setMesh({ name: file.name, ...counts }), [file.name])
  const known = mesh?.name === file.name ? mesh : null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/90 animate-fadeIn" onClick={onClose}>
      {file.kind === 'image' ? (
        <img key={file.name} src={file.path} alt="" onClick={stop} className={fit} />
      ) : file.kind === 'mesh' ? (
        <Suspense fallback={<span className="text-sm font-semibold text-white/80">Opening the 3D model…</span>}>
          <MeshView key={file.name} file={file} onLoaded={onMesh} />
        </Suspense>
      ) : file.orbit ? (
        <Orbit key={file.name} file={file} className={fit} />
      ) : (
        <video key={file.name} src={file.path} controls autoPlay playsInline onClick={stop}
               className={fit} />
      )}

      <span className="absolute top-4 left-4 text-sm font-label text-white/80">{index + 1} / {files.length}</span>
      <div className="absolute top-3 right-3 flex gap-2" onClick={stop}>
        <button
          onClick={() => onDetails(!details)}
          title={details ? 'Hide details' : 'Show details'}
          className={`${bigIconBtn} ${details ? '!bg-white !text-black' : ''}`}
        >
          <InfoIcon />
        </button>
        <a href={file.path} target="_blank" rel="noopener noreferrer" title="Open in a new tab" className={bigIconBtn}>
          <NewTabIcon />
        </a>
        <button onClick={onClose} title="Close" className={bigIconBtn}><CloseIcon /></button>
      </div>
      {files.length > 1 && (
        <>
          <button onClick={(e) => { stop(e); step(-1) }} title="Previous" className={`${bigIconBtn} absolute left-3 top-1/2 -translate-y-1/2`}>
            <ArrowIcon flip />
          </button>
          <button onClick={(e) => { stop(e); step(1) }} title="Next" className={`${bigIconBtn} absolute right-3 top-1/2 -translate-y-1/2`}>
            <ArrowIcon />
          </button>
        </>
      )}

      {details && (
        <dl
          onClick={stop}
          className="absolute bottom-4 left-4 m-0 max-w-[calc(100vw-2rem)] grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm text-white bg-black/75 backdrop-blur-sm rounded-xl px-4 py-3"
        >
          <Detail label="Type" value={`${KIND_LABEL[file.kind]}${file.orbit ? ' · 360° orbit' : ''}${file.upload ? ' · upload' : ''}`} />
          {file.model && <Detail label="Model" value={file.model} />}
          <Detail label="Made" value={when(file.created)} />
          {file.seconds != null && <Detail label="Took" value={took(file.seconds)} />}
          {shapeOf(file) && <Detail label={file.duration ? 'Length' : 'Size'} value={shapeOf(file)} />}
          {known?.triangles && (
            <Detail label="Mesh" value={`${fmtCount(known.triangles)} triangles · ${fmtCount(known.vertices)} vertices`} />
          )}
          <Detail label="File" value={size(file.bytes)} />
        </dl>
      )}
    </div>
  )
}

function Detail({ label, value }) {
  return (
    <>
      <dt className="text-white/60">{label}</dt>
      <dd className="m-0 min-w-0 break-words">{value}</dd>
    </>
  )
}

/* A 360° orbit (first frame = last): it turns by itself until it is first
 * grabbed, then stays wherever the hand leaves it. A drag turns it one full
 * turn across its width, the subject following the hand; each angle is a jump
 * to its frame. The Hub stores these clips with every frame a keyframe
 * (video_service.all_keyframes) so each jump is instant. One jump at a time:
 * the next waits for the last to land, then goes straight to where the drag is. */
function Orbit({ file, className }) {
  const video = useRef(null)
  const drag = useRef(null)                       // { x, t } where the drag began
  const seek = useRef({ busy: false, at: 0, want: 0 })
  const [turned, setTurned] = useState(false)

  const seekTo = (t) => {
    const s = seek.current
    s.want = t
    if (s.busy) return
    s.busy = true
    s.at = t
    video.current.currentTime = t
  }
  const onSeeked = () => {
    const s = seek.current
    s.busy = false
    if (s.want !== s.at) seekTo(s.want)
  }

  const down = (e) => {
    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    video.current.pause()
    drag.current = { x: e.clientX, t: video.current.currentTime }
    setTurned(true)
  }
  const move = (e) => {
    const d = video.current.duration
    if (!drag.current || !d) return
    // The camera circles one way, so the subject turns the other: playing
    // backward is what turns it the way the hand moves.
    const t = drag.current.t - ((e.clientX - drag.current.x) / e.currentTarget.clientWidth) * d
    seekTo(((t % d) + d) % d)
  }
  const up = () => { drag.current = null }

  return (
    <div className="relative" onClick={(e) => e.stopPropagation()}>
      <video
        ref={video}
        src={file.path}
        autoPlay
        loop
        muted
        playsInline
        onSeeked={onSeeked}
        onPointerDown={down}
        onPointerMove={move}
        onPointerUp={up}
        onPointerCancel={up}
        className={`${className} cursor-grab active:cursor-grabbing touch-none select-none`}
      />
      {!turned && (
        <span className="pointer-events-none absolute bottom-4 left-1/2 -translate-x-1/2 text-sm font-semibold text-white bg-black/60 px-3 py-1.5 rounded-full">
          Drag to turn it
        </span>
      )}
    </div>
  )
}

function DownloadIcon() {
  return (
    <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 4v11" /><path d="m7 10 5 5 5-5" /><path d="M5 20h14" />
    </svg>
  )
}

function InfoIcon() {
  return (
    <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="9" /><path d="M12 11v5" /><path d="M12 8h.01" />
    </svg>
  )
}

function NewTabIcon() {
  return (
    <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 4h6v6" /><path d="M20 4 11 13" /><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />
    </svg>
  )
}

function CloseIcon() {
  return (
    <svg className="w-5 h-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
      <path d="M6 6l12 12" /><path d="M18 6 6 18" />
    </svg>
  )
}

function ArrowIcon({ flip }) {
  return (
    <svg className={`w-6 h-6 ${flip ? 'rotate-180' : ''}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="m9 5 7 7-7 7" />
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

function fmtCount(n) {
  return n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e3 ? `${Math.round(n / 1e3)}K` : `${n}`
}

// What the file is, to read the time it took against: "1024 × 1024" for a
// picture, "5.2 s · 1344 × 768" for a clip, "2 min 30 s" for a song.
function shapeOf(file) {
  const dims = file.width ? `${file.width} × ${file.height}` : null
  const length = file.duration ? clipLength(file.duration) : null
  return [length, dims].filter(Boolean).join(' · ') || null
}

function clipLength(seconds) {
  return seconds < 10 ? `${seconds.toFixed(1)} s` : took(seconds)
}

// How long making a file took: "9 s", "1 min 25 s", "1 h 5 min".
function took(seconds) {
  const s = Math.round(seconds)
  if (s < 60) return `${s} s`
  if (s < 3600) return `${Math.floor(s / 60)} min${s % 60 ? ` ${s % 60} s` : ''}`
  return `${Math.floor(s / 3600)} h${Math.round((s % 3600) / 60) ? ` ${Math.round((s % 3600) / 60)} min` : ''}`
}

function when(seconds) {
  return new Date(seconds * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })
}

const btnReject = 'px-3 py-1.5 rounded-lg bg-error-surface text-error border border-error/30 text-xs font-semibold cursor-pointer hover:bg-error/15 disabled:opacity-50 transition-colors whitespace-nowrap'
const iconBtn = 'w-8 h-8 rounded-lg flex items-center justify-center bg-black/55 text-white backdrop-blur-sm border-none cursor-pointer no-underline hover:bg-black/75 transition-colors'
const bigIconBtn = 'w-11 h-11 rounded-full flex items-center justify-center bg-black/55 text-white backdrop-blur-sm border-none cursor-pointer no-underline hover:bg-black/80 transition-colors'
const btnQuiet = 'px-3 py-1.5 rounded-lg bg-surface-high text-text border border-outline-dim text-xs font-semibold cursor-pointer hover:bg-surface-highest transition-colors whitespace-nowrap'
