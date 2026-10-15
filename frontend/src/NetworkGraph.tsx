import { useState, useMemo, useEffect } from 'react'
import { MapContainer, GeoJSON, CircleMarker, useMapEvents } from 'react-leaflet'
import type { FeatureCollection, GeoJsonObject } from 'geojson'
import type { PathOptions } from 'leaflet'

// ── Highway classification ──────────────────────────────────────────────────

const HW_TYPES = [
  { key: 'motorway',    label: 'Motorway / Trunk',           color: '#ef4444', width: 4,   match: (h: string) => h.startsWith('motorway') || h.startsWith('trunk') },
  { key: 'primary',     label: 'Primary',                    color: '#3b82f6', width: 3,   match: (h: string) => h.startsWith('primary') },
  { key: 'secondary',   label: 'Secondary',                  color: '#a855f7', width: 2.5, match: (h: string) => h.startsWith('secondary') },
  { key: 'tertiary',    label: 'Tertiary',                   color: '#22c55e', width: 2,   match: (h: string) => h.startsWith('tertiary') },
  { key: 'residential', label: 'Residential / Unclassified', color: '#94a3b8', width: 1.5, match: (h: string) => h.startsWith('residential') || h.startsWith('unclassified') || h.startsWith('living_street') },
  { key: 'service',     label: 'Service / Other',            color: '#475569', width: 1,   match: () => true },
]

function hwGroup(hw: string | undefined): string {
  if (!hw) return 'service'
  const h = hw.toLowerCase()
  for (const t of HW_TYPES) if (t.key !== 'service' && t.match(h)) return t.key
  return 'service'
}
function hwMeta(hw: string | undefined) {
  return HW_TYPES.find(t => t.key === hwGroup(hw)) ?? HW_TYPES[HW_TYPES.length - 1]
}

// ── Types ───────────────────────────────────────────────────────────────────

type RoadProps = {
  source?: string; target?: string
  highway?: string; name?: string; length?: number; maxspeed?: string
  oneway_normalized?: string
  source_lon?: number; source_lat?: number
  target_lon?: number; target_lat?: number
}

type Summary = {
  graph: Record<string, number>
  candidate_count: number
  repairs: { accepted: number }
}

// ── Zoom tracker ─────────────────────────────────────────────────────────────

function ZoomTracker({ onZoom }: { onZoom: (z: number) => void }) {
  useMapEvents({ zoomend: e => onZoom((e.target as { getZoom(): number }).getZoom()) })
  return null
}

// ── Main component ──────────────────────────────────────────────────────────

type Props = {
  summary: Summary | null
  boundaries: { features: Array<{ properties?: Record<string, unknown>; type: string }> } | null
  mask: object | null
}

export function NetworkGraph({ summary, boundaries, mask }: Props) {
  const [roads, setRoads]               = useState<FeatureCollection | null>(null)
  const [loading, setLoading]           = useState(true)
  const [loadError, setLoadError]       = useState<string | null>(null)
  const [zoom, setZoom]                 = useState(10)
  const [selectedRoad, setSelectedRoad] = useState<RoadProps | null>(null)
  const [showNodes, setShowNodes]       = useState(true)
  const [showRoads, setShowRoads]       = useState(true)
  const [showBoundary, setShowBoundary] = useState(true)
  const [enabledTypes, setEnabledTypes] = useState<Set<string>>(new Set(HW_TYPES.map(t => t.key)))

  // Load the pre-exported static GeoJSON once — no API splitting needed
  useEffect(() => {
    fetch('/roads.geojson')
      .then(r => {
        if (!r.ok) throw new Error(`Failed to load roads.geojson (HTTP ${r.status})`)
        return r.json() as Promise<FeatureCollection>
      })
      .then(fc => { setRoads(fc); setLoading(false) })
      .catch((e: unknown) => {
        setLoadError(e instanceof Error ? e.message : 'Failed to load road data')
        setLoading(false)
      })
  }, [])

  const filteredRoads = useMemo(() => {
    if (!roads) return null
    return {
      type: 'FeatureCollection' as const,
      features: roads.features.filter(f =>
        enabledTypes.has(hwGroup((f.properties as RoadProps).highway))
      ),
    }
  }, [roads, enabledTypes])

  const junctionNodes = useMemo(() => {
    if (!roads || !showNodes || zoom < 14) return []
    const seen = new Map<string, [number, number]>()
    for (const f of roads.features) {
      const p = f.properties as RoadProps
      if (p.source && p.source_lat != null && p.source_lon != null)
        seen.set(p.source, [p.source_lat as number, p.source_lon as number])
      if (p.target && p.target_lat != null && p.target_lon != null)
        seen.set(p.target, [p.target_lat as number, p.target_lon as number])
    }
    return Array.from(seen.values())
  }, [roads, zoom, showNodes])

  const hwCounts = useMemo(() => {
    if (!roads) return {} as Record<string, number>
    const c: Record<string, number> = {}
    for (const f of roads.features) {
      const g = hwGroup((f.properties as RoadProps).highway)
      c[g] = (c[g] ?? 0) + 1
    }
    return c
  }, [roads])

  const province  = boundaries?.features.find(f => f.properties?.boundary_type === 'province')
  const districts = boundaries?.features.filter(f => f.properties?.boundary_type === 'district') ?? []
  const nodeRadius = zoom >= 16 ? 5 : zoom >= 15 ? 4 : 3

  return (
    <div className="flex overflow-hidden rounded-2xl border border-slate-700 shadow-xl"
      style={{ height: 'calc(100vh - 88px)' }}>

      {/* ── Sidebar ── */}
      <aside className="flex w-60 flex-shrink-0 flex-col overflow-hidden bg-slate-900 text-slate-100">
        <div className="border-b border-slate-700 p-4">
          <p className="text-[10px] font-bold uppercase tracking-widest text-blue-400">Sabaragamuwa Province</p>
          <p className="mt-0.5 text-[11px] text-slate-400">Road Network Graph — Component 2</p>
          {summary ? (
            <div className="mt-3 grid grid-cols-2 gap-1.5">
              {([
                ['Nodes',      summary.graph.nodes],
                ['Edges',      summary.graph.directed_edges],
                ['Candidates', summary.candidate_count],
                ['Repairs',    summary.repairs.accepted],
              ] as [string, number][]).map(([label, value]) => (
                <div key={label} className="rounded-lg bg-slate-800 px-2 py-2 text-center">
                  <p className="text-[9px] uppercase tracking-wider text-slate-500">{label}</p>
                  <p className="mt-0.5 text-sm font-bold text-white">{Number(value ?? 0).toLocaleString()}</p>
                </div>
              ))}
            </div>
          ) : (
            <p className="mt-3 text-xs text-slate-500">Loading stats…</p>
          )}
        </div>

        <div className="border-b border-slate-700 px-4 py-3">
          <p className="mb-2 text-[10px] font-bold uppercase tracking-wider text-slate-400">Layers</p>
          <div className="space-y-2">
            {([
              ['Road segments', showRoads, () => setShowRoads(v => !v)],
              [zoom < 14
                ? 'Junctions (zoom ≥ 14)'
                : `Junctions (${junctionNodes.length.toLocaleString()})`,
               showNodes, () => setShowNodes(v => !v)],
              ['Boundaries', showBoundary, () => setShowBoundary(v => !v)],
            ] as [string, boolean, () => void][]).map(([label, on, toggle]) => (
              <label key={label} className="flex cursor-pointer items-center gap-2">
                <input type="checkbox" checked={on} onChange={toggle} className="h-3.5 w-3.5 accent-blue-500" />
                <span className="text-xs text-slate-300">{label}</span>
              </label>
            ))}
          </div>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-3">
          <p className="mb-2 text-[10px] font-bold uppercase tracking-wider text-slate-400">Road Types</p>
          <div className="space-y-2.5">
            {HW_TYPES.map(({ key, label, color }) => (
              <label key={key} className="flex cursor-pointer items-center gap-2">
                <input
                  type="checkbox"
                  checked={enabledTypes.has(key)}
                  onChange={() => setEnabledTypes(prev => {
                    const next = new Set(prev)
                    next.has(key) ? next.delete(key) : next.add(key)
                    return next
                  })}
                  className="h-3.5 w-3.5 accent-blue-500"
                />
                <span className="inline-block h-1.5 w-7 flex-shrink-0 rounded-full" style={{ backgroundColor: color }} />
                <span className="flex-1 text-xs text-slate-300">{label}</span>
                {hwCounts[key] != null && (
                  <span className="font-mono text-[10px] text-slate-500">{hwCounts[key].toLocaleString()}</span>
                )}
              </label>
            ))}
          </div>
        </div>

        <div className="border-t border-slate-700 px-4 py-2 text-[10px] text-slate-600">
          {loading
            ? 'Loading road network…'
            : roads
              ? `${roads.features.length.toLocaleString()} segments loaded`
              : 'No data'}
          {' · '}Zoom {zoom}
        </div>
      </aside>

      {/* ── Map ── */}
      <div className="relative flex-1">
        {loading && (
          <div className="absolute inset-0 z-[1000] flex items-center justify-center bg-slate-900/80">
            <div className="text-center">
              <div className="mx-auto mb-3 h-8 w-8 animate-spin rounded-full border-2 border-slate-600 border-t-blue-500" />
              <p className="text-sm text-slate-300">Loading road network…</p>
            </div>
          </div>
        )}
        {loadError && (
          <div className="absolute bottom-3 left-3 right-3 z-[1000] rounded bg-red-900/80 px-3 py-2 text-xs text-red-200 shadow">
            {loadError}
          </div>
        )}

        <MapContainer center={[7.225, 80.625]} zoom={10} preferCanvas className="h-full w-full"
          style={{ background: '#0f172a' }}>
          <ZoomTracker onZoom={setZoom} />

          {mask && (
            <GeoJSON key="mask" data={mask as GeoJsonObject} interactive={false}
              style={{ color: 'transparent', weight: 0, fillColor: '#000', fillOpacity: 0.55 }} />
          )}

          {showBoundary && province && (
            <GeoJSON key="province" data={province as GeoJsonObject}
              style={{ color: '#f8fafc', weight: 2, fill: false } as PathOptions} />
          )}

          {showBoundary && districts.length > 0 && (
            <GeoJSON key="districts"
              data={{ type: 'FeatureCollection', features: districts } as GeoJsonObject}
              style={{ color: '#60a5fa', weight: 1.5, fill: false, dashArray: '6 3' } as PathOptions} />
          )}

          {showRoads && filteredRoads && (
            <GeoJSON
              key={`roads-${filteredRoads.features.length}-${Array.from(enabledTypes).sort().join()}`}
              data={filteredRoads as GeoJsonObject}
              style={feature => {
                const meta = hwMeta((feature?.properties as RoadProps | undefined)?.highway)
                return { color: meta.color, weight: meta.width, opacity: 0.9 }
              }}
              onEachFeature={(feature, layer) => {
                layer.on('click', e => {
                  (e as { originalEvent?: { stopPropagation?: () => void } }).originalEvent?.stopPropagation?.()
                  setSelectedRoad(feature.properties as RoadProps)
                })
              }}
            />
          )}

          {showNodes && zoom >= 14 && junctionNodes.map(([lat, lon], i) => (
            <CircleMarker key={i} center={[lat, lon]} radius={nodeRadius}
              pathOptions={{ color: '#0f172a', weight: 1, fillColor: '#fff', fillOpacity: 1 }} />
          ))}
        </MapContainer>
      </div>

      {/* ── Detail panel ── */}
      {selectedRoad && (
        <div className="flex w-64 flex-shrink-0 flex-col overflow-hidden border-l border-slate-700 bg-slate-900 text-slate-100">
          <div className="flex items-center justify-between border-b border-slate-700 px-4 py-3">
            <h3 className="text-sm font-semibold">Road Segment</h3>
            <button type="button" className="text-xl leading-none text-slate-500 hover:text-white"
              onClick={() => setSelectedRoad(null)}>×</button>
          </div>
          <div className="flex-1 overflow-y-auto p-4">
            {selectedRoad.highway && (
              <div className="mb-4 flex items-center gap-2">
                <span className="inline-block h-1.5 w-8 flex-shrink-0 rounded-full"
                  style={{ backgroundColor: hwMeta(selectedRoad.highway).color }} />
                <span className="font-semibold capitalize">
                  {selectedRoad.highway.replace(/_/g, ' ')}
                </span>
              </div>
            )}
            <dl className="space-y-2.5 text-sm">
              {([
                ['Name',      selectedRoad.name],
                ['Length',    selectedRoad.length != null ? `${Number(selectedRoad.length).toFixed(1)} m` : null],
                ['Max speed', selectedRoad.maxspeed],
                ['Direction', selectedRoad.oneway_normalized?.replace(/_/g, ' ')],
              ] as [string, string | null | undefined][]).filter(([, v]) => v != null).map(([label, value]) => (
                <div key={label} className="flex justify-between gap-3">
                  <dt className="text-slate-400">{label}</dt>
                  <dd className="text-right font-medium text-white">{value}</dd>
                </div>
              ))}
              <div className="border-t border-slate-700 pt-3 text-xs">
                <p className="mb-1 font-semibold text-slate-400">Source node</p>
                <p className="break-all font-mono text-slate-300">{selectedRoad.source ?? '—'}</p>
                <p className="my-1.5 text-center text-slate-600">↓ edge</p>
                <p className="mb-1 font-semibold text-slate-400">Target node</p>
                <p className="break-all font-mono text-slate-300">{selectedRoad.target ?? '—'}</p>
              </div>
              {selectedRoad.source_lat != null && (
                <div className="rounded bg-slate-800 p-2 text-[11px] leading-5 text-slate-400">
                  <p>src {Number(selectedRoad.source_lat).toFixed(6)}, {Number(selectedRoad.source_lon).toFixed(6)}</p>
                  <p>tgt {Number(selectedRoad.target_lat).toFixed(6)}, {Number(selectedRoad.target_lon).toFixed(6)}</p>
                </div>
              )}
            </dl>
          </div>
        </div>
      )}
    </div>
  )
}
