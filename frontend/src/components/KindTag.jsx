import { kindOf, mediaKind } from '../models'

// The small tag that says what a recipe is: an App, an LLM or a Media model.
// `specific` names the media kind instead (Image, Video, 3D, Music), where a
// row has room to say which.
const KIND = {
  app: { label: 'App', className: 'bg-surface-highest text-text-muted' },
  llm: { label: 'LLM', className: 'bg-primary/15 text-primary' },
  media: { label: 'Media', className: 'bg-tertiary/15 text-tertiary' },
}

const MEDIA_LABEL = { image: 'Image', video: 'Video', '3D model': '3D', song: 'Music' }

export default function KindTag({ recipe, specific = false }) {
  const kind = kindOf(recipe)
  const label = (specific && kind === 'media' && MEDIA_LABEL[mediaKind(recipe)]) || KIND[kind].label
  return (
    <span className={`shrink-0 rounded px-1 py-px font-label text-[9px] font-bold uppercase tracking-wide ${KIND[kind].className}`}>
      {label}
    </span>
  )
}
