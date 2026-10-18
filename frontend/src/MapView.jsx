import { useEffect, useMemo, useRef, useState } from 'react'
import L from 'leaflet'
import { CircleMarker, MapContainer, Polyline, Popup, TileLayer, Tooltip, useMap } from 'react-leaflet'

export const LEVEL_COLORS = ['#16a34a', '#eab308', '#f97316', '#dc2626', '#7e22ce']
export const ROUTE_COLORS = ['#2563eb', '#0891b2', '#be185d']
const RGB = LEVEL_COLORS.map((h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)))
const METRICS = { overall: 'Overall risk', wind: 'Wind', rain: 'Rain', snow: 'Snow' }

export const fmtTime = (iso) =>
  new Date(iso).toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })

// Risk index 0..4 -> colour blended between the level colours (integer = band edge).
function color(v) {
  const i = Math.min(Math.floor(v), 3)
  const f = Math.min(v - i, 1)
  return RGB[i].map((c, k) => Math.round(c + (RGB[i + 1][k] - c) * f))
}

function FitBounds({ plan }) {
  const map = useMap()
  useEffect(() => {
    map.fitBounds(L.latLngBounds(plan.routes.flatMap((r) => r.geometry)), { padding: [30, 30] })
  }, [map, plan])
  return null
}

// Heatmap: one canvas pixel per corridor grid cell, stretched over the map as an image.
// The browser's bilinear upscaling turns the cells into a smooth field; cells off the
// corridor stay transparent. Redrawing ~150 pixels per slider tick is effectively free.
function CorridorHeat({ heatmap, metric, hour }) {
  const map = useMap()
  const grid = useMemo(() => {
    const { points, cell_deg: cell } = heatmap
    const ij = points.map(([lat, lon]) => [Math.round(lat / cell), Math.round(lon / cell)])
    const is = ij.map((p) => p[0])
    const js = ij.map((p) => p[1])
    const [imin, imax, jmin, jmax] = [Math.min(...is), Math.max(...is), Math.min(...js), Math.max(...js)]
    const canvas = document.createElement('canvas')
    canvas.width = jmax - jmin + 1
    canvas.height = imax - imin + 1
    const bounds = [[(imin - 0.5) * cell, (jmin - 0.5) * cell], [(imax + 0.5) * cell, (jmax + 0.5) * cell]]
    return { canvas, bounds, pixels: ij.map(([i, j]) => (imax - i) * canvas.width + (j - jmin)) }
  }, [heatmap])
  const overlay = useRef(null)

  useEffect(() => () => {
    overlay.current?.remove()
    overlay.current = null
  }, [map, grid])

  useEffect(() => {
    const { canvas, pixels } = grid
    const ctx = canvas.getContext('2d')
    const img = ctx.createImageData(canvas.width, canvas.height)
    heatmap.layers[metric].forEach((series, p) => {
      const [r, g, b] = color(series[hour])
      img.data.set([r, g, b, 255], pixels[p] * 4)
    })
    ctx.putImageData(img, 0, 0)
    const url = canvas.toDataURL()
    if (overlay.current) overlay.current.setUrl(url)
    else overlay.current = L.imageOverlay(url, grid.bounds, { opacity: 0.6, interactive: false }).addTo(map)
  }, [map, grid, heatmap, metric, hour])
  return null
}

function Checkpoint({ cp, route, color, faded }) {
  return (
    <CircleMarker
      center={[cp.lat, cp.lon]}
      radius={faded ? 4 : 6}
      pathOptions={{ color: '#111', weight: 1, fillColor: LEVEL_COLORS[cp.level], fillOpacity: faded ? 0.5 : 0.95 }}
    >
      <Popup>
        <b style={{ color }}>{route.name}</b> · mile {cp.mile}
        <br />ETA <b>{fmtTime(cp.eta)}</b>
        <br />Risk: <b style={{ color: LEVEL_COLORS[cp.level] }}>{cp.level_name}</b> ({cp.reasons.join('; ')})
        <br />Wind {cp.wind_mph.toFixed(0)} mph · Rain {cp.rain_in_hr.toFixed(2)} in/hr · Snow {cp.snow_in_hr.toFixed(1)} in/hr
      </Popup>
    </CircleMarker>
  )
}

export default function MapView({ plan, selected, onSelect }) {
  const [hour, setHour] = useState(0)
  const [metric, setMetric] = useState('overall')
  const [showHeat, setShowHeat] = useState(true)
  const [allCheckpoints, setAllCheckpoints] = useState(false)
  const [playing, setPlaying] = useState(false)

  useEffect(() => setHour(0), [plan])
  useEffect(() => {
    if (!playing) return
    const t = setInterval(() => setHour((h) => (h + 1) % 49), 350)
    return () => clearInterval(t)
  }, [playing])

  // Draw the selected route last so it sits on top.
  const order = plan ? plan.routes.map((_, i) => i).filter((i) => i !== selected).concat(selected) : []
  const heatTime = plan && new Date(new Date(plan.heatmap.start).getTime() + hour * 3600e3).toISOString()

  return (
    <div className="map-wrap">
      <MapContainer center={[39.5, -98.35]} zoom={4} preferCanvas className="map">
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        {plan && <FitBounds plan={plan} />}
        {plan && showHeat && <CorridorHeat heatmap={plan.heatmap} metric={metric} hour={hour} />}
        {order.map((i) => {
          const r = plan.routes[i]
          const m = r.summary.miles_by_level
          return (
            <Polyline
              key={i}
              positions={r.geometry}
              pathOptions={{ color: ROUTE_COLORS[i], weight: i === selected ? 7 : 4, opacity: i === selected ? 0.95 : 0.55 }}
              eventHandlers={{ click: () => onSelect(i) }}
            >
              <Tooltip sticky>
                <b>{r.name}</b>{i === plan.recommended && ' ★ recommended'}
                <br />{r.distance_mi} mi · {r.duration_h} h · arrive {fmtTime(r.arrival)}
                <br />Severe {m.Severe} mi · High {m.High} mi · avg risk {r.summary.avg_risk}
              </Tooltip>
            </Polyline>
          )
        })}
        {order
          .filter((i) => allCheckpoints || i === selected)
          .flatMap((i) =>
            plan.routes[i].checkpoints.map((cp) => (
              <Checkpoint key={`${i}-${cp.mile}`} cp={cp} route={plan.routes[i]} color={ROUTE_COLORS[i]} faded={i !== selected} />
            )),
          )}
        {plan &&
          [plan.origin, plan.destination].map((p, k) => (
            <CircleMarker key={k} center={[p.lat, p.lon]} radius={9} pathOptions={{ color: '#111', weight: 3, fillColor: '#fff', fillOpacity: 1 }}>
              <Tooltip>{k ? 'Destination' : 'Origin'}: {p.name}</Tooltip>
            </CircleMarker>
          ))}
      </MapContainer>

      {plan && (
        <div className="heat-panel">
          <div className="heat-row">
            <button type="button" onClick={() => setPlaying(!playing)} aria-label={playing ? 'Pause' : 'Play'}>
              {playing ? '❚❚' : '▶'}
            </button>
            <input
              type="range" min="0" max="48" step="1" value={hour}
              onChange={(e) => setHour(Number(e.target.value))}
              aria-label="Forecast hour"
            />
            <span className="heat-time">+{hour}h · {fmtTime(heatTime)}</span>
          </div>
          <div className="heat-row">
            <label><input type="checkbox" checked={showHeat} onChange={(e) => setShowHeat(e.target.checked)} /> Heatmap</label>
            <select value={metric} onChange={(e) => setMetric(e.target.value)} aria-label="Heatmap layer">
              {Object.entries(METRICS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <label><input type="checkbox" checked={allCheckpoints} onChange={(e) => setAllCheckpoints(e.target.checked)} /> Checkpoints on all routes</label>
          </div>
        </div>
      )}
    </div>
  )
}
