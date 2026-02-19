import { useStore } from '../store'

export default function SystemBar() {
  const metrics = useStore((s) => s.metrics)
  const recipes = useStore((s) => s.recipes)

  const ramUsed = metrics?.ram_used_gb ?? 0
  const ramTotal = metrics?.ram_total_gb ?? 128
  const ramPct = ramTotal > 0 ? (ramUsed / ramTotal) * 100 : 0

  return (
    <div className="flex items-center gap-4 px-5 py-2.5 border-b border-border text-xs text-text-dim bg-surface">
      <div className="flex items-center gap-1.5">
        <span className="text-spark text-sm">●</span>
        <span className="text-text-muted">{metrics?.gpu_name || 'GB10 Blackwell'}</span>
      </div>
      <div className="flex items-center gap-1.5">
        <span>RAM</span>
        <div className="w-24 h-1.5 bg-white/[0.08] rounded-full overflow-hidden">
          <div
            className="h-full rounded-full transition-all duration-500"
            style={{
              width: `${ramPct}%`,
              background: ramPct > 80 ? '#ef4444' : '#76b900',
            }}
          />
        </div>
        <span>{ramUsed}GB / {ramTotal}GB</span>
      </div>
      <div className="flex items-center gap-1.5">
        <span>GPU</span>
        <span className="text-spark">{metrics?.gpu_utilization ?? 0}%</span>
      </div>
      <div className="flex items-center gap-1.5">
        <span>Disk</span>
        <span>{metrics?.disk_free_gb ?? 0}GB free</span>
      </div>
      {metrics?.gpu_temperature > 0 && (
        <div className="flex items-center gap-1.5">
          <span>Temp</span>
          <span className={metrics.gpu_temperature > 80 ? 'text-red-400' : 'text-spark'}>
            {metrics.gpu_temperature}°C
          </span>
        </div>
      )}
      <div className="ml-auto flex items-center gap-1.5">
        <span className="text-spark">▲</span>
        <span>{recipes.length} recipes</span>
      </div>
    </div>
  )
}
