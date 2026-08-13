import { useEffect } from 'react'
import { coverInfo, originalFor } from '../covers'

// "About this image": the picture, and what it is. The description travels
// with the recipe, so a recipe added later describes its own artwork.
export default function CoverInfoModal({ recipe, onClose }) {
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const info = coverInfo(recipe)

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm animate-fadeIn p-6"
      onClick={onClose}
    >
      <div
        className="bg-surface-high rounded-2xl w-full max-w-3xl max-h-[88vh] overflow-y-auto shadow-2xl border border-outline-dim"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start gap-3 p-6 pb-4">
          <h3 className="text-lg font-bold text-text font-display m-0">About this image</h3>
          <button
            onClick={onClose}
            aria-label="Close"
            className="ml-auto shrink-0 w-8 h-8 rounded-lg bg-surface text-text-muted border border-outline-dim cursor-pointer hover:text-text transition-colors"
          >
            ✕
          </button>
        </div>

        <div className="px-6 pb-6">
          {/* The original source file, never a rendered crop. `object-contain`
              plus a viewport cap scales it down to fit but never cuts it. */}
          <img
            src={originalFor(recipe)}
            alt=""
            className="w-full max-h-[65vh] rounded-xl bg-surface object-contain ring-1 ring-outline-dim"
          />

          <p className="text-sm text-text-muted leading-relaxed mt-4 mb-0">
            {info?.caption || 'No description recorded for this image.'}
          </p>

          {info?.credit && (
            <p className="text-xs text-text-dim mt-2 mb-0">
              {info.credit}
              {info.source && (
                <>
                  {' · '}
                  <a href={info.source} target="_blank" rel="noreferrer" className="text-primary hover:underline">
                    source
                  </a>
                </>
              )}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}
