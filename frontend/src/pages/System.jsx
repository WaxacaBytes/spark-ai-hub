import { useState, useEffect } from 'react'
import { useStore } from '../store'
import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'

function MetricCard({ label, value, unit, color = '#76b900', pct }) {
  return (
    <div className="bg-surface border border-border rounded-xl p-5">
      <div className="text-text-dim text-xs mb-2">{label}</div>
      <div className="text-2xl font-bold" style={{ color }}>
        {value}
        <span className="text-sm font-normal text-text-dim ml-1">{unit}</span>
      </div>
      {pct !== undefined && (
        <div className="mt-3 w-full h-2 bg-white/[0.06] rounded-full overflow-hidden">
          <div
            className="h-full rounded-full transition-all duration-500"
            style={{ width: `${pct}%`, background: pct > 80 ? '#ef4444' : color }}
          />
        </div>
      )}
    </div>
  )
}

export default function System() {
  const metrics = useStore((s) => s.metrics)
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
      <div className="p-6 text-center text-text-dim py-20">
        <div className="text-4xl mb-3">📊</div>
        <div>Waiting for system metrics...</div>
      </div>
    )
  }

  const ramPct = metrics.ram_total_gb > 0 ? (metrics.ram_used_gb / metrics.ram_total_gb) * 100 : 0
  const diskPct = metrics.disk_total_gb > 0 ? (metrics.disk_used_gb / metrics.disk_total_gb) * 100 : 0

  return (
    <div className="p-6">
      <h2 className="text-lg font-bold mb-4">System Monitor</h2>

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
          color={metrics.gpu_temperature > 80 ? '#ef4444' : '#76b900'}
        />
      </div>

      {history.length > 2 && (
        <div className="bg-surface border border-border rounded-xl p-5">
          <div className="text-text-dim text-xs mb-4">GPU & Temperature (60s)</div>
          <ResponsiveContainer width="100%" height={250}>
            <LineChart data={history}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
              <XAxis dataKey="time" stroke="#6b6b7b" tick={{ fontSize: 10 }} />
              <YAxis stroke="#6b6b7b" tick={{ fontSize: 10 }} />
              <Tooltip
                contentStyle={{ background: '#1a1a24', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8 }}
                labelStyle={{ color: '#9b9bab' }}
              />
              <Line type="monotone" dataKey="gpu" stroke="#76b900" strokeWidth={2} dot={false} name="GPU %" />
              <Line type="monotone" dataKey="temp" stroke="#f59e0b" strokeWidth={2} dot={false} name="Temp °C" />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  )
}
