import { useState, useMemo, useCallback } from 'react'
import { MapContainer, TileLayer, GeoJSON, CircleMarker, useMapEvents } from 'react-leaflet'
import type { FeatureCollection, GeoJsonObject } from 'geojson'
import type { PathOptions } from 'leaflet'

const HW_TYPES = [
  { key: 'motorway', label: 'Motorway / Trunk', color: '#dc2626', match: (h: string) => h.includes('motorway') || h.includes('trunk') },
  { key: 'primary',  label: 'Primary',           color: '#1d4ed8', match: (h: string) => h.includes('primary') },
  { key: 'secondary',label: 'Secondary',          color: '#7c3aed', match: (h: string) => h.includes('secondary') },
  { key: 'tertiary', label: 'Tertiary',           color: '#059669', match: (h: string) => h.includes('tertiary') },
  { key: 'residential', label: 'Residential / Unclassified', color: '#6b7280', match: (h: string) => h.includes('residential') || h.includes('unclassified') || h.includes('living_street') },
  { key: 'service',  label: 'Service / Other',   color: '#9ca3af', match: () => true },
]

function hwGroup(hw: string | undefined): string {
  if (!hw) return 'service'
  const h = hw.toLowerCase()
  for (const t of HW_TYPES) if (t.key !== 'service' && t.match(h)) return t.key
  return 'service'
}

function hwColor(hw: string | undefined): string {
  return HW_TYPES.find(t => t.key === hwGroup(hw))?.color ?? '#9ca3af'
}

type RoadProps = {
  source?: string; target?: string; edge_key?: string
  highway?: string; name?: string; length?: number; maxspeed?: string
  oneway_normalized?: string; direction_source?: string
  source_lon?: number; source_lat?: number
  target_lon?: number; target_lat?: number
}

type Summary = { graph: Record<string, number>; candidate_count: number; repairs: { accepted: number } }

async function getJson<T>(url: string): Promise<T> {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`HTTP ${r.status}`)
  return r.json()
}

async function fetchRoads(bounds: number[], districts: string, depth = 0): Promise<FeatureCollection> {
  const bbox = bounds.join(',')
  try {
    return await getJson<FeatureCollection>(`/api/roads?districts=${districts}&bbox=${encodeURIComponent(bbox)}`)
  } catch (err) {
    if (!(err instanceof Error) || !err.message.includes('too many road segments') || depth >= 6) throw err
    const [w, s, e, n] = bounds
    const mLon = (w + e) / 2, mLat = (s + n) / 2
    const quads = [[w, s, mLon, mLat], [mLon, s, e, mLat], [w, mLat, mLon, n], [mLon, mLat, e, n]]
    const results = await Promise.all(quads.map(q => fetchRoads(q, districts, depth + 1)))
    return { type: 'FeatureCollection', features: results.flatMap(r => r.features) }
  }
}

function MapController({ onBoundsChange, onZoomChange }: { onBoundsChange: (b: string) => void; onZoomChange: (z: number) => void }) {
  const map = useMapEvents({
    moveend: () => {
      const b = map.getBounds()
      onBoundsChange([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].join(','))
      onZoomChange(map.getZoom())
    },
  })
  return null
}

type Props = {
  summary: Summary | null
  boundaries: { features: Array<{ properties?: Record<string, unknown>; geometry?: unknown; type: string }> } | null
  mask: object | null
}

export function NetworkGraph({ summary, boundaries, mask }: Props) {
  const [roads, setRoads] = useState<FeatureCollection | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [retryKey, setRetryKey] = useState(0)
  const [zoom, setZoom] = useState(10)
  const [selectedRoad, setSelectedRoad] = useState<RoadProps | null>(null)
  const [showNodes, setShowNodes] = useState(true)
  const [showRoads, setShowRoads] = useState(true)
  const [showBoundaries, setShowBoundaries] = useState(true)
  const [enabledTypes, setEnabledTypes] = useState<Set<string>>(new Set(HW_TYPES.map(t => t.key)))

  const loadRoads = useCallback((bbox: string) => {
    setLoading(true)
    setError(null)
    fetchRoads(bbox.split(',').map(Number), encodeURIComponent('Ratnapura,Kegalle'))
      .then(setRoads)
      .catch(err => setError(err instanceof Error ? err.message : 'Failed to load roads'))
      .finally(() => setLoading(false))
  }, [])

  const filteredRoads = useMemo(() => {
    if (!roads) return null
    return {
      type: 'FeatureCollection' as const,
      features: roads.features.filter(f => enabledTypes.has(hwGroup((f.properties as RoadProps).highway))),
    }
  }, [roads, enabledTypes])

  const nodePositions = useMemo(() => {
    if (!roads || !showNodes || zoom < 13) return []
    const m = new Map<string, [number, number]>()
    for (const f of roads.features) {
      const p = f.properties as RoadProps
      if (p?.source && p.source_lat != null && p.source_lon != null)
        m.set(p.source, [p.source_lat as number, p.source_lon as number])
      if (p?.target && p.target_lat != null && p.target_lon != null)
        m.set(p.target, [p.target_lat as number, p.target_lon as number])
    }
    return Array.from(m.entries()).map(([id, [lat, lon]]) => ({ id, lat, lon }))
  }, [roads, zoom, showNodes])

  const hwCounts = useMemo(() => {
    if (!roads) return {} as Record<string, number>
    const counts: Record<string, number> = {}
    for (const f of roads.features) {
      const g = hwGroup((f.properties as RoadProps).highway)
      counts[g] = (counts[g] ?? 0) + 1
    }
    return counts
  }, [roads])

  const province = boundaries?.features.find(f => f.properties?.boundary_type === 'province')
  const districts = boundaries?.features.filter(f => f.properties?.boundary_type === 'district') ?? []

  const nodeRadius = zoom >= 15 ? 4 : zoom >= 14 ? 3 : 2

  return (
    <div className="flex overflow-hidden rounded-2xl border border-slate-200 shadow-sm" style={{ height: '740px' }}>
      {/* ── Sidebar ── */}
      <aside className="flex w-56 flex-shrink-0 flex-col overflow-hidden bg-slate-800">
        {/* Header */}
        <div className="border-b border-slate-700 p-4">
          <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-400">Component 2</p>
          <h2 className="mt-0.5 text-sm font-bold leading-tight text-white">Sabaragamuwa Road Network</h2>
          {summary && (
            <div className="mt-3 grid grid-cols-2 gap-1.5">
              {[
                ['Nodes', summary.graph.nodes],
                ['Edges', summary.graph.directed_edges],
                ['Candidates', summary.candidate_count],
                ['Repairs', summary.repairs.accepted],
              ].map(([label, value]) => (
                <div key={String(label)} className="rounded bg-slate-700 px-2 py-1.5 text-center">
                  <p className="text-[9px] uppercase tracking-wide text-slate-400">{label}</p>
                  <p className="mt-0.5 text-xs font-bold text-white">{Number(value ?? 0).toLocaleString()}</p>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Layers */}
        <div className="border-b border-slate-700 p-4">
          <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-slate-400">Layers</p>
          <div className="space-y-2">
            {([
              ['Road segments', showRoads, () => setShowRoads(v => !v)],
              [`Junctions ${zoom < 13 ? '(zoom ≥ 13)' : `(${nodePositions.length})`}`, showNodes, () => setShowNodes(v => !v)],
              ['Boundaries', showBoundaries, () => setShowBoundaries(v => !v)],
            ] as [string, boolean, () => void][]).map(([label, checked, toggle]) => (
              <label className="flex cursor-pointer items-center gap-2" key={label}>
                <input checked={checked} className="h-3.5 w-3.5 accent-blue-400" onChange={toggle} type="checkbox" />
                <span className="text-xs text-slate-300">{label}</span>
              </label>
            ))}
          </div>
        </div>

        {/* Road types */}
        <div className="flex-1 overflow-y-auto p-4">
          <p className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-slate-400">Road Types</p>
          <div className="space-y-2">
            {HW_TYPES.map(({ key, label, color }) => (
              <label className="flex cursor-pointer items-center gap-2" key={key}>
                <input
                  checked={enabledTypes.has(key)}
                  className="h-3.5 w-3.5 accent-blue-400"
                  onChange={() => setEnabledTypes(prev => {
                    const next = new Set(prev)
                    next.has(key) ? next.delete(key) : next.add(key)
                    return next
                  })}
                  type="checkbox"
                />
                <span className="inline-block h-2 w-5 flex-shrink-0 rounded-sm" style={{ backgroundColor: color }} />
                <span className="flex-1 text-xs text-slate-300">{label}</span>
                {hwCounts[key] != null && (
                  <span className="font-mono text-[10px] text-slate-500">{hwCounts[key].toLocaleString()}</span>
                )}
              </label>
            ))}
          </div>
        </div>

        {/* Footer */}
        <div className="border-t border-slate-700 px-4 py-2 text-[10px] text-slate-500">
          Zoom {zoom} · EPSG:4326
        </div>
      </aside>

      {/* ── Map ── */}
      <div className="relative flex-1">
        {loading && (
          <div className="absolute left-3 top-3 z-[1000] rounded bg-white/90 px-3 py-1.5 text-xs font-medium shadow">
            Loading road network…
          </div>
        )}
        {error && (
          <div className="absolute bottom-3 left-3 right-3 z-[1000] rounded bg-red-50 px-3 py-2 text-xs text-red-800 shadow">
            {error}
            <button className="ml-2 underline" onClick={() => { setError(null); setRetryKey(k => k + 1) }} type="button">Retry</button>
          </div>
        )}
        <MapContainer center={[7.225, 80.225]} zoom={10} className="h-full w-full">
          <TileLayer attribution="&copy; OpenStreetMap contributors" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
          <MapController onBoundsChange={loadRoads} onZoomChange={setZoom} />

          {mask && (
            <GeoJSON key="mask" data={mask as GeoJsonObject} interactive={false}
              style={{ color: 'transparent', weight: 0, fillColor: '#94a3b8', fillOpacity: 0.18 }} />
          )}

          {showBoundaries && province && (
            <GeoJSON key="province" data={province as GeoJsonObject}
              style={{ color: '#1e293b', weight: 2.5, fill: false } as PathOptions} />
          )}
          {showBoundaries && districts.length > 0 && (
            <GeoJSON key="districts"
              data={{ type: 'FeatureCollection', features: districts } as GeoJsonObject}
              style={{ color: '#3b82f6', weight: 1.5, fill: false, dashArray: '6 3' } as PathOptions} />
          )}

          {showRoads && filteredRoads && (
            <GeoJSON
              key={`roads-${retryKey}-${filteredRoads.features.length}-${Array.from(enabledTypes).sort().join()}`}
              data={filteredRoads as GeoJsonObject}
              style={feature => {
                const hw = (feature?.properties as RoadProps | undefined)?.highway
                const g = hwGroup(hw)
                return {
                  color: hwColor(hw),
                  weight: g === 'motorway' ? 3.5 : g === 'primary' ? 2.5 : g === 'secondary' ? 2 : 1.5,
                  opacity: 0.92,
                }
              }}
              onEachFeature={(feature, layer) => {
                layer.on('click', e => {
                  (e as { originalEvent?: { stopPropagation?: () => void } }).originalEvent?.stopPropagation?.()
                  setSelectedRoad(feature.properties as RoadProps)
                })
              }}
            />
          )}

          {showNodes && zoom >= 13 && nodePositions.map(({ id, lat, lon }) => (
            <CircleMarker key={id} center={[lat, lon]} radius={nodeRadius}
              pathOptions={{ color: '#0f172a', weight: 1, fillColor: '#f8fafc', fillOpacity: 1 }} />
          ))}
        </MapContainer>
      </div>

      {/* ── Detail panel ── */}
      {selectedRoad && (
        <div className="flex w-60 flex-shrink-0 flex-col overflow-hidden border-l border-slate-200 bg-white">
          <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
            <h3 className="text-sm font-semibold text-slate-900">Road Segment</h3>
            <button className="text-lg leading-none text-slate-400 hover:text-slate-700" onClick={() => setSelectedRoad(null)} type="button">×</button>
          </div>
          <div className="flex-1 overflow-y-auto p-4">
            {selectedRoad.highway && (
              <div className="mb-4 flex items-center gap-2">
                <span className="inline-block h-3 w-3 flex-shrink-0 rounded-sm"
                  style={{ backgroundColor: hwColor(selectedRoad.highway) }} />
                <span className="font-semibold capitalize text-slate-900">
                  {selectedRoad.highway.replace(/_/g, ' ')}
                </span>
              </div>
            )}
            <dl className="space-y-2.5 text-sm">
              {[
                ['Name', selectedRoad.name],
                ['Length', selectedRoad.length != null ? `${Number(selectedRoad.length).toFixed(1)} m` : null],
                ['Max speed', selectedRoad.maxspeed],
                ['Direction', selectedRoad.oneway_normalized?.replace(/_/g, ' ')],
              ].filter(([, v]) => v != null).map(([label, value]) => (
                <div className="flex justify-between gap-2" key={String(label)}>
                  <dt className="text-slate-500">{label}</dt>
                  <dd className="font-medium text-right text-slate-900">{String(value)}</dd>
                </div>
              ))}
              <div className="border-t border-slate-100 pt-3">
                <p className="mb-1 text-xs font-medium text-slate-500">Source node</p>
                <p className="break-all font-mono text-xs text-slate-700">{selectedRoad.source}</p>
                <p className="my-1 text-center text-xs text-slate-400">↓</p>
                <p className="mb-1 text-xs font-medium text-slate-500">Target node</p>
                <p className="break-all font-mono text-xs text-slate-700">{selectedRoad.target}</p>
              </div>
              {selectedRoad.source_lat != null && (
                <div className="rounded bg-slate-50 p-2 text-xs text-slate-500">
                  <p>Source: {Number(selectedRoad.source_lat).toFixed(6)}, {Number(selectedRoad.source_lon).toFixed(6)}</p>
                  <p>Target: {Number(selectedRoad.target_lat).toFixed(6)}, {Number(selectedRoad.target_lon).toFixed(6)}</p>
                </div>
              )}
            </dl>
          </div>
        </div>
      )}
    </div>
  )
}
