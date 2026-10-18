import { useState } from 'react'
import MapView, { LEVEL_COLORS, ROUTE_COLORS, fmtTime } from './MapView.jsx'

const LEVELS = ['Low', 'Moderate', 'High', 'Severe', 'No Travel']

// "YYYY-MM-DDTHH:mm" for the next whole hour, in local time (what datetime-local expects).
function nextHourLocal() {
  const d = new Date()
  d.setMinutes(60, 0, 0)
  return new Date(d - d.getTimezoneOffset() * 60e3).toISOString().slice(0, 16)
}

const fmtDuration = (h) => `${Math.floor(h)}h ${String(Math.round((h % 1) * 60)).padStart(2, '0')}m`

function RiskBar({ miles, total }) {
  return (
    <div className="risk-bar" aria-hidden="true">
      {LEVELS.map((name, i) =>
        miles[name] > 0 ? (
          <span key={name} style={{ width: `${(miles[name] / total) * 100}%`, background: LEVEL_COLORS[i] }} title={`${name}: ${miles[name]} mi`} />
        ) : null,
      )}
    </div>
  )
}

function RouteCard({ route, index, recommended, selected, onSelect }) {
  const m = route.summary.miles_by_level
  return (
    <button type="button" className={`route-card${selected ? ' selected' : ''}`} onClick={onSelect} style={{ borderLeftColor: ROUTE_COLORS[index] }}>
      <div className="route-head">
        <b>{route.name}</b>
        {recommended && <span className="badge">★ Recommended</span>}
        <span className="max-level" style={{ background: LEVEL_COLORS[route.summary.max_level] }}>
          worst: {LEVELS[route.summary.max_level]}
        </span>
      </div>
      <div className="route-stats">
        {route.distance_mi} mi · {fmtDuration(route.duration_h)} · arrive {fmtTime(route.arrival)}
      </div>
      <RiskBar miles={m} total={route.distance_mi} />
      <div className="route-stats">
        Severe+ <b>{(m.Severe + m['No Travel']).toFixed(1)}</b> mi · High <b>{m.High}</b> mi · avg risk <b>{route.summary.avg_risk}</b> / 4
      </div>
    </button>
  )
}

export default function App() {
  const [form, setForm] = useState({
    origin: 'Chicago, IL',
    destination: 'Denver, CO',
    departure: nextHourLocal(),
    load_lb: 35000,
    interval_mi: 25,
  })
  const [plan, setPlan] = useState(null)
  const [selected, setSelected] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const set = (key) => (e) => setForm({ ...form, [key]: e.target.value })

  async function submit(e) {
    e.preventDefault()
    setLoading(true)
    setError(null)
    try {
      const res = await fetch('/api/plan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...form,
          departure: new Date(form.departure).toISOString(),
          load_lb: Number(form.load_lb),
          interval_mi: Number(form.interval_mi),
        }),
      })
      const data = await res.json().catch(() => ({ error: `Server error (HTTP ${res.status})` }))
      if (!res.ok) throw new Error(data.error)
      setPlan(data)
      setSelected(data.recommended)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <h1>Safe Truck Route Planner</h1>
        <form onSubmit={submit} className="form">
          <label>Origin<input required value={form.origin} onChange={set('origin')} placeholder="City, address…" /></label>
          <label>Destination<input required value={form.destination} onChange={set('destination')} placeholder="City, address…" /></label>
          <label>Departure<input required type="datetime-local" value={form.departure} onChange={set('departure')} /></label>
          <div className="form-row">
            <label>Load (lb)<input required type="number" min="0" max="80000" step="100" value={form.load_lb} onChange={set('load_lb')} /></label>
            <label>Weather every
              <select value={form.interval_mi} onChange={set('interval_mi')}>
                {[10, 25, 50].map((n) => <option key={n} value={n}>{n} miles</option>)}
              </select>
            </label>
          </div>
          <button type="submit" disabled={loading}>{loading ? 'Checking routes & forecast…' : 'Find safest route'}</button>
        </form>

        {error && <div className="alert error">{error}</div>}

        {plan && (
          <>
            {plan.warning && <div className="alert error">{plan.warning}</div>}
            <div className="alert info">
              <b>{plan.routes[plan.recommended].name} recommended.</b> {plan.reason}
            </div>
            {plan.routes.map((r, i) => (
              <RouteCard key={i} route={r} index={i} recommended={i === plan.recommended} selected={i === selected} onSelect={() => setSelected(i)} />
            ))}
          </>
        )}

        <div className="legend">
          {LEVELS.map((name, i) => (
            <span key={name}><i style={{ background: LEVEL_COLORS[i] }} />{name}</span>
          ))}
        </div>
        <p className="note">
          Ranking: fewest No Travel/Severe miles → fewest High miles → lowest average risk → shortest time.
          Click a checkpoint for its ETA and forecast. Routes: OSRM · Weather: Open-Meteo · Geocoding: Nominatim.
        </p>
      </aside>
      <MapView plan={plan} selected={selected} onSelect={setSelected} />
    </div>
  )
}
