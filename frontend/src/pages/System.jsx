import { useState, useEffect } from 'react'
import { useStore } from '../store'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'

function MetricCard({ label, value, unit, color = 'var(--primary)', pct }) {
  return (
    <div className="bg-surface rounded-2xl p-5 card-hover">
      <div className="text-text-dim text-xs font-medium mb-2">{label}</div>
      <div className="text-2xl font-bold font-[Manrope]" style={{ color }}>
        {value}
        <span className="text-sm font-normal text-text-dim ml-1">{unit}</span>
      </div>
      {pct !== undefined && (
        <div className="mt-3 w-full h-2 bg-outline-dim rounded-full overflow-hidden">
          <div
            className="h-full rounded-full transition-all duration-500"
            style={{ width: `${pct}%`, background: pct > 80 ? 'var(--error)' : color }}
          />
        </div>
      )}
    </div>
  )
}

export default function System() {
  const metrics = useStore((s) => s.metrics)
  const theme = useStore((s) => s.theme)
  const [history, setHistory] = useState([])

  useEffect(() => {
    if (!metrics) return
    setHistory((prev) => {
      const next = [
        ...prev,
        {
          time: new Date().toLocaleTimeString(),
          gpu: metrics.gpu_utilization,
          ram: metrics.ram_used_gb,
          temp: metrics.gpu_temperature,
        },
      ]
      return next.slice(-60)
    })
  }, [metrics])

  if (!metrics) {
    return (
      <div className="p-6 text-center text-text-dim py-20 animate-fadeIn">
        <div className="text-5xl mb-4">📊</div>
        <div className="text-lg font-semibold font-[Manrope]">Waiting for system metrics...</div>
      </div>
    )
  }

  const ramPct = metrics.ram_total_gb > 0 ? (metrics.ram_used_gb / metrics.ram_total_gb) * 100 : 0
  const diskPct = metrics.disk_total_gb > 0 ? (metrics.disk_used_gb / metrics.disk_total_gb) * 100 : 0
  const gridColor = theme === 'light' ? 'rgba(0,0,0,0.06)' : 'rgba(255,255,255,0.06)'
  const axisColor = theme === 'light' ? '#8a8a9a' : '#76747c'
  const tooltipBg = theme === 'light' ? '#ffffff' : '#1a1a24'
  const tooltipBorder = theme === 'light' ? 'rgba(0,0,0,0.1)' : 'rgba(255,255,255,0.1)'

  return (
    <div className="px-6 py-6 pb-24">
      <h2 className="text-2xl font-extrabold tracking-tight font-[Manrope] mb-1 m-0">System Monitor</h2>
      <p className="text-sm text-text-dim m-0 mb-6">Real-time hardware metrics</p>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <MetricCard label="GPU Utilization" value={metrics.gpu_utilization} unit="%" pct={metrics.gpu_utilization} />
        <MetricCard
          label="Memory"
          value={metrics.ram_used_gb}
          unit={`/ ${metrics.ram_total_gb} GB`}
          pct={ramPct}
        />
        <MetricCard
          label="Disk"
          value={metrics.disk_free_gb}
          unit="GB free"
          pct={diskPct}
        />
        <MetricCard
          label="GPU Temperature"
          value={metrics.gpu_temperature}
          unit="°C"
          color={metrics.gpu_temperature > 80 ? 'var(--error)' : 'var(--primary)'}
        />
      </div>

      {history.length > 2 && (
        <div className="bg-surface rounded-2xl p-5 card-hover">
          <div className="text-text-dim text-xs font-medium mb-4">GPU & Temperature (60s)</div>
          <ResponsiveContainer width="100%" height={250}>
            <LineChart data={history}>
              <CartesianGrid strokeDasharray="3 3" stroke={gridColor} />
              <XAxis dataKey="time" stroke={axisColor} tick={{ fontSize: 10 }} />
              <YAxis stroke={axisColor} tick={{ fontSize: 10 }} />
              <Tooltip
                contentStyle={{ background: tooltipBg, border: `1px solid ${tooltipBorder}`, borderRadius: 12 }}
                labelStyle={{ color: axisColor }}
              />
              <Line type="monotone" dataKey="gpu" stroke="var(--primary)" strokeWidth={2} dot={false} name="GPU %" />
              <Line type="monotone" dataKey="temp" stroke="#f59e0b" strokeWidth={2} dot={false} name="Temp °C" />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  )
}
