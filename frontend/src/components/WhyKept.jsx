// Why this build is kept beside the model's others: short marks, wrapping
// under the build name rather than widening the row. "No video" and the like,
// something the model can do that this build cannot, are marked in red.
export default function WhyKept({ why }) {
  if (!why?.length) return null
  return (
    <span className="mt-0.5 flex flex-wrap gap-1">
      {why.map((w) => (
        <span key={w} className={`rounded px-1 font-label text-[9px] leading-[14px] ${w.startsWith('No ') ? 'bg-error/10 text-error' : 'bg-primary/10 text-primary'}`}>
          {w}
        </span>
      ))}
    </span>
  )
}
