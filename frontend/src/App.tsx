import { useCallback, useEffect, useRef, useState } from 'react'
import { MapContainer, TileLayer, GeoJSON, useMap, useMapEvents } from 'react-leaflet'
import type { Feature, FeatureCollection, GeoJsonObject } from 'geojson'
import { geoJSON } from 'leaflet'
import type { Layer, PathOptions } from 'leaflet'
import 'leaflet/dist/leaflet.css'

type Summary = {
  graph: Record<string, number>
  candidate_count: number
  candidate_status_counts: Record<string, number>
  repairs: { accepted: number; skipped: number; approval_count: number }
  study_area: string
}

type CandidateProperties = {
  candidate_id?: string
  status?: string
  distance_m?: number
  heading_compatible?: boolean
  semantic_conflicts?: string[]
  semantic_missing_attributes?: string[]
  endpoint_u?: string
  endpoint_v?: string
  feature_type?: string
}

type CandidateFeature = Feature & { properties?: CandidateProperties }
type DistrictName = 'Ratnapura' | 'Kegalle'
type BoundaryFeature = Feature & { properties?: { boundary_type?: string; district?: DistrictName; name?: string } }
type BoundaryCollection = FeatureCollection & { features: BoundaryFeature[] }

const districtNames: DistrictName[] = ['Ratnapura', 'Kegalle']

function provinceMask(boundaries: BoundaryCollection | null): Feature | null {
  const province = boundaries?.features.find((feature) => feature.properties?.boundary_type === 'province')
  const geometry = province?.geometry
  if (!geometry || (geometry.type !== 'Polygon' && geometry.type !== 'MultiPolygon')) return null
  const polygons = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.coordinates
  if (polygons.some((polygon) => polygon.length === 0 || polygon[0].length < 4)) return null
  const exteriorRings = polygons.map((polygon) => polygon[0])
  const interiorRings = polygons.flatMap((polygon) => polygon.slice(1))
  const world: number[][] = [[-180, -85], [180, -85], [180, 85], [-180, 85], [-180, -85]]
  return {
    type: 'Feature',
    properties: {},
    geometry: {
      type: 'MultiPolygon',
      coordinates: [[world, ...exteriorRings], ...interiorRings.map((ring) => [ring])],
    },
  }
}

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path)
  const body = await response.text()
  const contentType = response.headers.get('content-type') ?? ''
  let payload: unknown

  try {
    payload = JSON.parse(body)
  } catch {
    const preview = body.replace(/\s+/g, ' ').slice(0, 120)
    throw new Error(
      `API returned a non-JSON response for ${path} (${response.status}, ${contentType || 'unknown content type'}): ${preview || 'empty response'}`,
    )
  }

  if (!response.ok) {
    const error = payload as { error?: string }
    throw new Error(error.error ?? `API request failed for ${path} (${response.status})`)
  }

  if (!contentType.toLowerCase().includes('application/json')) {
    throw new Error(`API returned JSON with an unexpected content type for ${path}: ${contentType || 'missing content type'}`)
  }

  return payload as T
}

async function getRoadsForBounds(bounds: number[], districts: string, depth = 0): Promise<FeatureCollection> {
  const bbox = bounds.join(',')
  try {
    return await getJson<FeatureCollection>(`/api/roads?districts=${districts}&bbox=${encodeURIComponent(bbox)}`)
  } catch (reason) {
    if (!(reason instanceof Error) || !reason.message.includes('too many road segments') || depth >= 6) throw reason
    const [minLon, minLat, maxLon, maxLat] = bounds
    const midLon = (minLon + maxLon) / 2
    const midLat = (minLat + maxLat) / 2
    const quadrants = [
      [minLon, minLat, midLon, midLat],
      [midLon, minLat, maxLon, midLat],
      [minLon, midLat, midLon, maxLat],
      [midLon, midLat, maxLon, maxLat],
    ]
    const collections = await Promise.all(quadrants.map((quadrant) => getRoadsForBounds(quadrant, districts, depth + 1)))
    return { type: 'FeatureCollection', features: collections.flatMap((collection) => collection.features) }
  }
}

function ViewportRoads({ onBounds }: { onBounds: (bbox: string) => void }) {
  const map = useMapEvents({
    moveend: () => {
      const bounds = map.getBounds()
      onBounds([
        bounds.getWest(),
        bounds.getSouth(),
        bounds.getEast(),
        bounds.getNorth(),
      ].join(','))
    },
  })
  return null
}

function FitSelectedDistricts({ boundaries, selected }: { boundaries: BoundaryCollection | null; selected: DistrictName[] }) {
  const map = useMap()
  const initialFit = useRef(true)

  useEffect(() => {
    if (!boundaries) return
    const features = initialFit.current
      ? boundaries.features.filter((feature) => feature.properties?.boundary_type === 'province')
      : boundaries.features.filter((feature) => selected.includes(feature.properties?.district as DistrictName))
    initialFit.current = false
    if (features.length === 0) return
    const selectedFeatures: FeatureCollection = { type: 'FeatureCollection', features }
    const bounds = geoJSON(selectedFeatures).getBounds()
    if (bounds.isValid()) map.fitBounds(bounds, { padding: [20, 20] })
  }, [boundaries, map, selected])

  return null
}

function candidateStyle(feature?: Feature): PathOptions {
  const status = (feature as CandidateFeature | undefined)?.properties?.status
  if (status === 'eligible') return { color: '#16a34a', weight: 4 }
  if (status === 'needs_review') return { color: '#d97706', weight: 4, dashArray: '5 4' }
  return { color: '#dc2626', weight: 3, dashArray: '3 5' }
}

function App() {
  const [summary, setSummary] = useState<Summary | null>(null)
  const [candidates, setCandidates] = useState<FeatureCollection | null>(null)
  const [roads, setRoads] = useState<FeatureCollection | null>(null)
  const [roadError, setRoadError] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loadingRoads, setLoadingRoads] = useState(true)
  const [boundaries, setBoundaries] = useState<BoundaryCollection | null>(null)
  const [boundaryError, setBoundaryError] = useState<string | null>(null)
  const [selectedDistricts, setSelectedDistricts] = useState<DistrictName[]>(districtNames)

  useEffect(() => {
    getJson<Summary>('/api/summary').then(setSummary).catch((reason: unknown) => {
      const message = reason instanceof Error ? reason.message : 'Could not load research data'
      setError(message)
    })
    getJson<BoundaryCollection>('/api/boundaries').then(setBoundaries).catch((reason: unknown) => {
      const message = reason instanceof Error ? reason.message : 'Could not load boundary data'
      setBoundaryError(message)
    })
  }, [])

  useEffect(() => {
    if (selectedDistricts.length === 0) {
      return
    }
    const districts = encodeURIComponent(selectedDistricts.join(','))
    getJson<FeatureCollection>(`/api/candidates?districts=${districts}`)
      .then(setCandidates)
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : 'Could not load district candidates'))
  }, [selectedDistricts])

  const loadRoads = useCallback((bbox: string) => {
    if (selectedDistricts.length === 0) {
      setRoads(null)
      setLoadingRoads(false)
      return
    }
    setLoadingRoads(true)
    setRoadError(null)
    const districts = encodeURIComponent(selectedDistricts.join(','))
    getRoadsForBounds(bbox.split(',').map(Number), districts)
      .then(setRoads)
      .catch((reason: unknown) => {
        setRoadError(reason instanceof Error ? reason.message : 'Could not load roads')
        setRoads(null)
      })
      .finally(() => setLoadingRoads(false))
  }, [selectedDistricts])

  const onEachCandidate = (feature: Feature, layer: Layer) => {
    const properties = (feature as CandidateFeature).properties ?? {}
    const label = [
      `<strong>${properties.candidate_id ?? 'Unknown candidate'}</strong>`,
      `Status: ${properties.status ?? 'unavailable'}`,
      `Distance: ${properties.distance_m ?? 'unavailable'} m`,
      `Endpoints: ${properties.endpoint_u ?? '?'} → ${properties.endpoint_v ?? '?'}`,
    ].join('<br />')
    layer.bindPopup(label)
  }

  const roadData = roads as GeoJsonObject | null
  const candidateData = candidates as GeoJsonObject | null
  const selectedBoundaryData = boundaries
    ? {
        ...boundaries,
        features: boundaries.features.filter((feature) =>
          feature.properties?.boundary_type === 'province'
          || selectedDistricts.includes(feature.properties?.district as DistrictName),
        ),
      }
    : null
  const mask = provinceMask(boundaries)

  return (
    <main className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-7xl px-6 py-5">
          <p className="text-sm font-semibold uppercase tracking-[0.2em] text-blue-700">Component 2</p>
          <h1 className="mt-1 text-2xl font-bold text-slate-950">Sabaragamuwa Road Network</h1>
          <p className="mt-2 text-sm text-slate-600">Real OSM roads and topology evidence, delivered through bounded read-only API requests.</p>
        </div>
      </header>
      <section className="mx-auto max-w-7xl px-6 py-8">
        {error && <p className="mb-5 rounded-lg bg-red-50 p-4 text-sm text-red-800">{error}</p>}
        <section className="mb-6 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div>
              <h2 className="font-semibold text-slate-900">Select districts</h2>
              <p className="mt-1 text-sm text-slate-600">Roads and candidate overlays are filtered to the selected verified boundaries.</p>
            </div>
            <div className="flex flex-wrap gap-5">
              {districtNames.map((district) => (
                <label className="flex items-center gap-2 text-sm font-medium text-slate-800" key={district}>
                  <input
                    checked={selectedDistricts.includes(district)}
                    className="h-4 w-4 accent-blue-700"
                    onChange={() => {
                      const next = selectedDistricts.includes(district)
                        ? selectedDistricts.filter((name) => name !== district)
                        : [...selectedDistricts, district]
                      if (next.length === 0) {
                        setCandidates(null)
                        setRoads(null)
                        setLoadingRoads(false)
                        setRoadError(null)
                      }
                      setSelectedDistricts(next)
                    }}
                    type="checkbox"
                  />
                  {district}
                </label>
              ))}
            </div>
          </div>
          {boundaryError && <p className="mt-3 text-sm text-red-700">Verified district boundaries are unavailable: {boundaryError}</p>}
        </section>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          {[
            ['Nodes', summary?.graph.nodes],
            ['Directed edges', summary?.graph.directed_edges],
            ['Weak components', summary?.graph.weak_components],
            ['Candidates', summary?.candidate_count],
            ['Accepted repairs', summary?.repairs.accepted],
          ].map(([label, value]) => (
            <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm" key={label}>
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</p>
              <p className="mt-2 text-2xl font-bold text-slate-950">{value ?? '—'}</p>
            </div>
          ))}
        </div>
        <div className="mt-6 grid gap-6 lg:grid-cols-[1.6fr_1fr]">
          <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
            <div className="relative h-[620px]">
              <MapContainer center={[7.225, 80.225]} zoom={12} className="h-full w-full">
                <TileLayer attribution="&copy; OpenStreetMap contributors" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
                <FitSelectedDistricts boundaries={boundaries} selected={selectedDistricts} />
                <ViewportRoads onBounds={loadRoads} />
                {mask && <GeoJSON data={mask as GeoJsonObject} interactive={false} style={{ color: 'transparent', weight: 0, fillColor: '#f1f5f9', fillOpacity: 1 }} />}
                {selectedBoundaryData && <GeoJSON data={selectedBoundaryData} style={(feature) =>
                  feature?.properties?.boundary_type === 'province'
                    ? { color: '#0f172a', weight: 2, fill: false }
                    : { color: '#2563eb', weight: 3, fill: false }
                } />}
                {roadData && <GeoJSON data={roadData} style={{ color: '#2563eb', weight: 1.5, opacity: 0.7 }} />}
                {candidateData && <GeoJSON data={candidateData} style={candidateStyle} onEachFeature={onEachCandidate} />}
              </MapContainer>
              {selectedDistricts.length === 0 && <p className="absolute left-4 right-4 top-4 z-[1000] rounded bg-white px-3 py-3 text-sm font-medium text-slate-700 shadow">Select at least one district to display its roads and candidate overlays.</p>}
              {loadingRoads && selectedDistricts.length > 0 && <p className="absolute left-4 top-4 z-[1000] rounded bg-white px-3 py-2 text-xs font-medium shadow">Loading bounded district road view…</p>}
              {roadError && <p className="absolute bottom-4 left-4 right-4 z-[1000] rounded bg-red-50 px-3 py-2 text-xs text-red-800">{roadError}</p>}
            </div>
            <div className="flex flex-wrap gap-4 border-t border-slate-200 px-4 py-3 text-xs text-slate-600">
              <span><i className="mr-1 inline-block h-2.5 w-2.5 rounded-full bg-green-600" />Eligible</span>
              <span><i className="mr-1 inline-block h-2.5 w-2.5 rounded-full bg-amber-600" />Needs review</span>
              <span><i className="mr-1 inline-block h-2.5 w-2.5 rounded-full bg-red-600" />Rejected</span>
            </div>
          </div>
          <aside className="space-y-6">
            <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <h2 className="font-semibold text-slate-900">Candidate statuses</h2>
              <dl className="mt-4 space-y-3 text-sm">
                {Object.entries(summary?.candidate_status_counts ?? {}).map(([status, count]) => (
                  <div className="flex justify-between" key={status}><dt className="text-slate-600">{status}</dt><dd className="font-semibold">{count}</dd></div>
                ))}
              </dl>
              <p className="mt-5 text-xs leading-5 text-slate-500">Select a candidate line on the map to inspect its real ID, status, distance, and endpoint evidence.</p>
            </div>
            <div className="rounded-2xl border border-amber-200 bg-amber-50 p-5">
              <h2 className="font-semibold text-amber-950">Repair status</h2>
              <p className="mt-2 text-sm leading-6 text-amber-900">
                {summary?.repairs.accepted ?? 0} real-data repairs accepted. Candidates remain evidence, not confirmed defects, and this read-only UI cannot approve repairs.
              </p>
            </div>
          </aside>
        </div>
      </section>
    </main>
  )
}

export default App
