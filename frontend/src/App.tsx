import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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

type PipelineStage = {
  stage_id: string
  status: string
  explanation: string
}

type GraphStageMetrics = {
  node_count: number | null
  directed_edge_count: number | null
  degree_1_node_count: number | null
  weakly_connected_component_count: number | null
}

type GraphComparison = {
  graphs: Record<string, { metrics: GraphStageMetrics | null; status: string }>
}

type MetricsResponse = {
  selected_districts: DistrictName[]
  spatial_policy: {
    node_inclusion: string
    edge_inclusion: string
    geometric_boundary_crossing_directed_edge_count: number | null
    geometric_boundary_crossing_limitation: string
    endpoint_split_directed_edge_count: string
  }
  province_wide: {
    node_count: number | null
    directed_edge_count: number | null
    weakly_connected_component_count: number | null
  }
  selected_area: {
    node_count: number | null
    induced_directed_edge_count: number | null
    weakly_connected_component_count: number | null
    endpoint_split_directed_edge_count: number | null
  }
}

type CandidateProperties = {
  candidate_id?: string
  status?: string
  distance_m?: number
  heading_compatible?: boolean
  heading_u_degrees?: number | null
  heading_v_degrees?: number | null
  heading_deviation_degrees?: number | null
  reasons?: string[]
  semantic_conflicts?: string[]
  semantic_missing_attributes?: string[]
  endpoint_semantics?: Record<string, Record<string, { ambiguous: boolean; missing: boolean; values: string[] }>>
  endpoint_u?: string | number
  endpoint_v?: string | number
  feature_type?: string
}

type CandidateFeature = Feature & { properties?: CandidateProperties }
type RoadAttributeEvidence = { ambiguous: boolean; missing: boolean; values: string[] }
type ReviewEvidence = { evidence_type: string; description: string; reference: string }
type CandidateReview = {
  candidate_id: string
  automated_status: string
  review_state: 'unreviewed' | 'reviewed'
  classification: 'likely_gap' | 'legitimate_endpoint' | 'uncertain' | null
  reviewer_decision: string | null
  supporting_evidence: ReviewEvidence[]
  limitations: string[]
  reviewed_at: string | null
  reviewer_id: string | null
}
type ReviewDataset = {
  schema_identifier: string
  candidate_source: { path: string; sha256: string }
  reviews: CandidateReview[]
}
type DistrictName = 'Ratnapura' | 'Kegalle'
type BoundaryFeature = Feature & { properties?: { boundary_type?: string; district?: DistrictName; name?: string } }
type BoundaryCollection = FeatureCollection & { features: BoundaryFeature[] }

const districtNames: DistrictName[] = ['Ratnapura', 'Kegalle']
const candidateStatuses = ['eligible', 'needs_review', 'rejected'] as const

function formatRoadAttribute(evidence?: RoadAttributeEvidence): string {
  if (!evidence) return 'Unavailable'
  if (evidence.missing) return 'Not recorded'
  if (evidence.values.length === 0) return 'Unavailable'
  return `${evidence.values.join(', ')}${evidence.ambiguous ? ' (ambiguous)' : ''}`
}

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
  const unavailableMessage =
    'Connection failure: cannot reach the Component 2 API at 127.0.0.1:8000. Start or restart it from the repository root with .venv\\Scripts\\python.exe scripts\\c2_api.py, then retry.'
  let response: Response
  try {
    response = await fetch(path)
  } catch {
    throw new Error(unavailableMessage)
  }
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
    const error = payload as { error?: string; message?: string; code?: string }
    const detail = error.error ?? error.message ?? 'Request was rejected'
    throw new Error(`HTTP ${response.status} API error${error.code ? ` (${error.code})` : ''} for ${path}: ${detail}`)
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

function ViewportRoads({ onBounds, retryKey }: { onBounds: (bbox: string) => void; retryKey: number }) {
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
  useEffect(() => {
    if (retryKey === 0) return
    const bounds = map.getBounds()
    onBounds([bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()].join(','))
  }, [map, onBounds, retryKey])
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

function candidateStyle(feature?: Feature, selectedCandidateId: string | null = null): PathOptions {
  const properties = (feature as CandidateFeature | undefined)?.properties
  const selected = properties?.candidate_id === selectedCandidateId
  const status = properties?.status
  if (selected) return { color: '#7c3aed', weight: 9, opacity: 1 }
  if (status === 'eligible') return { color: '#16a34a', weight: 6, opacity: 0.9 }
  if (status === 'needs_review') return { color: '#d97706', weight: 6, opacity: 0.9, dashArray: '5 4' }
  return { color: '#dc2626', weight: 6, opacity: 0.9, dashArray: '3 5' }
}

const HIGHWAY_COLORS: Record<string, string> = {
  motorway: '#dc2626', motorway_link: '#ef4444',
  trunk: '#b91c1c', trunk_link: '#dc2626',
  primary: '#1d4ed8', primary_link: '#2563eb',
  secondary: '#7c3aed', secondary_link: '#8b5cf6',
  tertiary: '#059669', tertiary_link: '#10b981',
  residential: '#6b7280', living_street: '#9ca3af',
  service: '#9ca3af', unclassified: '#d1d5db',
}

function highwayColor(highway: string | undefined): string {
  if (!highway) return '#94a3b8'
  return HIGHWAY_COLORS[highway.toLowerCase().trim()] ?? '#94a3b8'
}

type View = 'analysis' | 'roadmap'

function App() {
  const [summary, setSummary] = useState<Summary | null>(null)
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null)
  const [metricsError, setMetricsError] = useState<string | null>(null)
  const [loadingMetrics, setLoadingMetrics] = useState(true)
  const [metricsRetry, setMetricsRetry] = useState(0)
  const [reviewDataset, setReviewDataset] = useState<ReviewDataset | null>(null)
  const [reviewError, setReviewError] = useState<string | null>(null)
  const [selectedCandidateId, setSelectedCandidateId] = useState<string | null>(null)
  const [candidates, setCandidates] = useState<FeatureCollection | null>(null)
  const [roads, setRoads] = useState<FeatureCollection | null>(null)
  const [roadError, setRoadError] = useState<string | null>(null)
  const [roadRetry, setRoadRetry] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [dashboardRetry, setDashboardRetry] = useState(0)
  const [loadingRoads, setLoadingRoads] = useState(true)
  const [boundaries, setBoundaries] = useState<BoundaryCollection | null>(null)
  const [boundaryError, setBoundaryError] = useState<string | null>(null)
  const [selectedDistricts, setSelectedDistricts] = useState<DistrictName[]>(districtNames)
  const [activeView, setActiveView] = useState<View>('roadmap')
  const [pipelineStages, setPipelineStages] = useState<PipelineStage[] | null>(null)
  const [graphComparison, setGraphComparison] = useState<GraphComparison | null>(null)
  const highwayBreakdown = useMemo(() => {
    if (!roads) return null
    const counts: Record<string, number> = {}
    for (const feature of roads.features) {
      const hw = (feature.properties?.highway as string | undefined) ?? 'unknown'
      counts[hw] = (counts[hw] ?? 0) + 1
    }
    return Object.entries(counts).sort(([, a], [, b]) => b - a)
  }, [roads])

  const loadReviewDataset = useCallback(() => {
    getJson<ReviewDataset>('/api/reviews').then(setReviewDataset).catch((reason: unknown) => {
      setReviewDataset(null)
      setReviewError(reason instanceof Error ? reason.message : 'Could not load candidate review records')
    })
  }, [])

  useEffect(() => {
    getJson<Summary>('/api/summary').then(setSummary).catch((reason: unknown) => {
      setSummary(null)
      const message = reason instanceof Error ? reason.message : 'Could not load research data'
      setError(message)
    })
    getJson<BoundaryCollection>('/api/boundaries').then((value) => {
      setBoundaries(value)
      setBoundaryError(null)
    }).catch((reason: unknown) => {
      setBoundaries(null)
      const message = reason instanceof Error ? reason.message : 'Could not load boundary data'
      setBoundaryError(message)
    })
    getJson<{ stages: PipelineStage[] }>('/api/pipeline-status')
      .then((r) => setPipelineStages(r.stages))
      .catch(() => setPipelineStages(null))
    getJson<GraphComparison>('/api/graph-comparison')
      .then(setGraphComparison)
      .catch(() => setGraphComparison(null))
    loadReviewDataset()
  }, [loadReviewDataset, dashboardRetry])

  useEffect(() => {
    let active = true
    const districts = encodeURIComponent(selectedDistricts.join(','))
    getJson<MetricsResponse>(`/api/metrics?districts=${districts}`)
      .then((response) => {
        if (active) setMetrics(response)
      })
      .catch((reason: unknown) => {
        if (!active) return
        setMetrics(null)
        setMetricsError(reason instanceof Error ? reason.message : 'Could not load graph metrics')
      })
      .finally(() => {
        if (active) setLoadingMetrics(false)
      })
    return () => { active = false }
  }, [selectedDistricts, metricsRetry])

  useEffect(() => {
    if (selectedDistricts.length === 0) {
      return
    }
    const districts = encodeURIComponent(selectedDistricts.join(','))
    getJson<FeatureCollection>(`/api/candidates?districts=${districts}`)
      .then(setCandidates)
      .catch((reason: unknown) => {
        setCandidates(null)
        setError(reason instanceof Error ? reason.message : 'Could not load district candidates')
      })
  }, [selectedDistricts, dashboardRetry])

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
    if (properties.candidate_id) {
      // Endpoint point features are rendered after their short connector line and
      // can receive the pointer event instead of the line. Both must select the ID.
      layer.on('click', () => setSelectedCandidateId(properties.candidate_id ?? null))
    }
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
  const selectedReview = reviewDataset?.reviews.find((review) => review.candidate_id === selectedCandidateId)
  const selectedCandidate = candidates?.features.find((feature) => {
    const properties = (feature as CandidateFeature).properties
    return properties?.candidate_id === selectedCandidateId && properties.feature_type === 'candidate_connection'
  }) as CandidateFeature | undefined
  const selectedCandidateProperties = selectedCandidate?.properties
  const candidateFeatures = candidates?.features.filter((feature) => {
    const properties = (feature as CandidateFeature).properties
    return properties?.feature_type === 'candidate_connection' && Boolean(properties.candidate_id)
  }) as CandidateFeature[] | undefined
  const candidateAttributes = selectedCandidateProperties?.endpoint_semantics
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
      <div className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-7xl px-6">
          <nav className="-mb-px flex gap-8" aria-label="View tabs">
            <button className={`border-b-2 pb-3 pt-3 text-sm font-medium transition-colors ${activeView === 'analysis' ? 'border-blue-600 text-blue-700' : 'border-transparent text-slate-500 hover:text-slate-700'}`} onClick={() => setActiveView('analysis')} type="button">Topology Analysis</button>
            <button className={`border-b-2 pb-3 pt-3 text-sm font-medium transition-colors ${activeView === 'roadmap' ? 'border-blue-600 text-blue-700' : 'border-transparent text-slate-500 hover:text-slate-700'}`} onClick={() => setActiveView('roadmap')} type="button">Road Map Output</button>
          </nav>
        </div>
      </div>
      <section className="mx-auto max-w-7xl px-6 py-8">
        {error && <div className="mb-5 rounded-lg bg-red-50 p-4 text-sm text-red-800"><p>{error}</p><button className="mt-2 rounded border border-red-300 px-3 py-1 font-medium hover:bg-red-100" onClick={() => { setError(null); setReviewError(null); setDashboardRetry((attempt) => attempt + 1) }} type="button">Retry dashboard data</button></div>}
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
                      setLoadingMetrics(true)
                      setMetricsError(null)
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
        <section className="mb-6 rounded-2xl border border-slate-200 bg-slate-50 p-5">
          <div>
            <h2 className="font-semibold text-slate-900">Province-wide graph totals</h2>
            <p className="mt-1 text-sm text-slate-600">Scope: all of Sabaragamuwa Province. These totals do not change with district selection.</p>
          </div>
          <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
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
        </section>
        {activeView === 'analysis' && <><section className="mb-6 rounded-2xl border border-blue-200 bg-white p-5 shadow-sm" aria-live="polite">
          <div>
            <h2 className="font-semibold text-slate-900">Selected-area graph metrics</h2>
            <p className="mt-1 text-sm text-slate-600">Scope: {selectedDistricts.length ? selectedDistricts.join(' + ') : 'empty district selection'}; calculated from graph nodes covered by the selected verified district polygon union.</p>
          </div>
          {loadingMetrics && <p className="mt-4 text-sm text-slate-600">Loading graph metrics…</p>}
          {metricsError && <div className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-800"><p>Selected-area metrics unavailable: {metricsError}</p><button className="mt-2 rounded border border-red-300 px-3 py-1 font-medium hover:bg-red-100" onClick={() => { setLoadingMetrics(true); setMetricsError(null); setMetricsRetry((attempt) => attempt + 1) }} type="button">Retry metrics</button></div>}
          {!loadingMetrics && !metricsError && metrics && (
            <>
              <dl className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                {[
                  ['Selected nodes', metrics.selected_area.node_count],
                  ['Induced directed edges', metrics.selected_area.induced_directed_edge_count],
                  ['Selected weak components', metrics.selected_area.weakly_connected_component_count],
                  ['Endpoint-split directed edges', metrics.selected_area.endpoint_split_directed_edge_count],
                ].map(([label, value]) => (
                  <div className="rounded-xl border border-slate-200 p-4" key={label}>
                    <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</dt>
                    <dd className="mt-2 text-2xl font-bold text-slate-950">{value ?? 'Unavailable'}</dd>
                  </div>
                ))}
              </dl>
              <p className="mt-4 text-xs leading-5 text-slate-600">{metrics.spatial_policy.edge_inclusion} {metrics.spatial_policy.geometric_boundary_crossing_limitation}</p>
              <p className="mt-1 text-xs text-slate-500">Geometric boundary-crossing edges: {metrics.spatial_policy.geometric_boundary_crossing_directed_edge_count ?? 'Unavailable'}. A 200 response does not imply every metric is available.</p>
            </>
          )}
        </section>
        <div className="mt-6 grid gap-6 lg:grid-cols-[1.6fr_1fr]">
          <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
            <div className="relative h-[620px]">
              <MapContainer center={[7.225, 80.225]} zoom={12} className="h-full w-full">
                <TileLayer attribution="&copy; OpenStreetMap contributors" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
                <FitSelectedDistricts boundaries={boundaries} selected={selectedDistricts} />
                <ViewportRoads onBounds={loadRoads} retryKey={roadRetry} />
                {mask && <GeoJSON data={mask as GeoJsonObject} interactive={false} style={{ color: 'transparent', weight: 0, fillColor: '#f1f5f9', fillOpacity: 1 }} />}
                {selectedBoundaryData && <GeoJSON data={selectedBoundaryData} style={(feature) =>
                  feature?.properties?.boundary_type === 'province'
                    ? { color: '#0f172a', weight: 2, fill: false }
                    : { color: '#2563eb', weight: 3, fill: false }
                } />}
                {roadData && <GeoJSON data={roadData} style={{ color: '#2563eb', weight: 1.5, opacity: 0.7 }} />}
                {candidateData && <GeoJSON data={candidateData} style={(feature) => candidateStyle(feature, selectedCandidateId)} onEachFeature={onEachCandidate} />}
              </MapContainer>
              {selectedDistricts.length === 0 && <p className="absolute left-4 right-4 top-4 z-[1000] rounded bg-white px-3 py-3 text-sm font-medium text-slate-700 shadow">Select at least one district to display its roads and candidate overlays.</p>}
              {loadingRoads && selectedDistricts.length > 0 && <p className="absolute left-4 top-4 z-[1000] rounded bg-white px-3 py-2 text-xs font-medium shadow">Loading bounded district road view…</p>}
              {roadError && <div className="absolute bottom-4 left-4 right-4 z-[1000] rounded bg-red-50 px-3 py-2 text-xs text-red-800"><p>{roadError}</p><button className="mt-1 rounded border border-red-300 px-2 py-1 font-medium hover:bg-red-100" onClick={() => { setLoadingRoads(true); setRoadError(null); setRoadRetry((attempt) => attempt + 1) }} type="button">Retry road data</button></div>}
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
                {candidateStatuses.map((status) => (
                  <div className="flex justify-between" key={status}><dt className="text-slate-600">{status}</dt><dd className="font-semibold">{summary ? summary.candidate_status_counts[status] ?? 0 : 'Unavailable'}</dd></div>
                ))}
              </dl>
              <p className="mt-5 text-xs leading-5 text-slate-500">Select a candidate connector or endpoint on the map, or use these keyboard-accessible candidate controls.</p>
              {candidateFeatures && candidateFeatures.length > 0 && <ul className="mt-3 max-h-52 space-y-1 overflow-y-auto">{candidateFeatures.map((feature) => {
                const properties = feature.properties ?? {}
                const candidateId = properties.candidate_id!
                return <li key={candidateId}><button aria-pressed={selectedCandidateId === candidateId} className={`w-full rounded px-2 py-1 text-left text-xs font-mono ${selectedCandidateId === candidateId ? 'bg-blue-100 text-blue-950' : 'hover:bg-slate-100'}`} onClick={() => setSelectedCandidateId(candidateId)} type="button">{candidateId} <span className="font-sans">· {properties.status ?? 'status unavailable'}</span></button></li>
              })}</ul>}
              {candidates && candidateFeatures?.length === 0 && <p className="mt-3 text-sm text-slate-600">No candidate connections are available for the selected districts.</p>}
            </div>
            <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <h2 className="font-semibold text-slate-900">Candidate inspection</h2>
              {!selectedCandidateId && <p className="mt-2 text-sm text-slate-600">Select a real candidate from the map or list.</p>}
              {selectedCandidateId && <>
                <p className="mt-2 break-all font-mono text-xs text-slate-600">{selectedCandidateId}</p>
                {!selectedCandidate && <p className="mt-2 text-sm text-amber-800">Candidate geometry or evidence is not currently available for this selection.</p>}
                {selectedCandidateProperties && <dl className="mt-3 space-y-2 text-sm">
                  <div className="flex justify-between gap-3"><dt className="text-slate-600">Automated status</dt><dd className="font-semibold">{selectedCandidateProperties.status ?? 'unavailable'}</dd></div>
                  <div className="flex justify-between gap-3"><dt className="text-slate-600">Endpoint distance</dt><dd>{selectedCandidateProperties.distance_m ?? 'Unavailable'}{selectedCandidateProperties.distance_m !== undefined ? ' m' : ''}</dd></div>
                  <div className="flex justify-between gap-3"><dt className="text-slate-600">Heading compatible</dt><dd>{selectedCandidateProperties.heading_compatible === undefined ? 'Unavailable' : selectedCandidateProperties.heading_compatible ? 'yes' : 'no'}</dd></div>
                  <div className="flex justify-between gap-3"><dt className="text-slate-600">Heading deviation</dt><dd>{selectedCandidateProperties.heading_deviation_degrees ?? 'Unavailable'}{selectedCandidateProperties.heading_deviation_degrees != null ? '°' : ''}</dd></div>
                  <div><dt className="text-slate-600">Endpoints</dt><dd className="break-all">{selectedCandidateProperties.endpoint_u ?? 'Unavailable'} → {selectedCandidateProperties.endpoint_v ?? 'Unavailable'}</dd></div>
                  {selectedCandidateProperties.reasons && <div><dt className="text-slate-600">Automated screening reasons</dt><dd>{selectedCandidateProperties.reasons.join(', ') || 'None recorded'}</dd></div>}
                  {selectedCandidateProperties.semantic_conflicts && <div><dt className="text-slate-600">Road attribute conflicts</dt><dd>{selectedCandidateProperties.semantic_conflicts.join(', ') || 'None recorded'}</dd></div>}
                  {selectedCandidateProperties.semantic_missing_attributes && <div><dt className="text-slate-600">Missing road attributes</dt><dd>{selectedCandidateProperties.semantic_missing_attributes.join(', ') || 'None recorded'}</dd></div>}
                  <div><dt className="text-slate-600">Endpoint road attributes</dt>{['u', 'v'].map((endpoint) => {
                    const attributes = candidateAttributes?.[endpoint]
                    const entries = Object.entries(attributes ?? {})
                    return <dd className="mt-1" key={endpoint}><span className="font-medium">{endpoint}:</span> {entries.length > 0 ? entries.map(([attribute, evidence]) => `${attribute}: ${formatRoadAttribute(evidence)}`).join('; ') : 'Unavailable'}</dd>
                  })}</div>
                </dl>}
                <p className="mt-3 break-all text-[11px] text-slate-500">Candidate source: {reviewDataset?.candidate_source.path ?? (reviewError ? `unavailable (${reviewError})` : 'loading')} {reviewDataset?.candidate_source.sha256 ? `· SHA-256 ${reviewDataset.candidate_source.sha256}` : ''}</p>
              </>}
            </div>
            <div className="rounded-2xl border border-amber-200 bg-amber-50 p-5">
              <h2 className="font-semibold text-amber-950">Repair status</h2>
              <p className="mt-2 text-sm leading-6 text-amber-900">
              {summary ? `${summary.repairs.accepted} real-data repairs accepted.` : 'Repair summary unavailable.'} Candidates remain evidence, not confirmed defects, and this read-only UI cannot approve repairs.
              </p>
            </div>
            <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
              <h2 className="font-semibold text-slate-900">Human review</h2>
              {reviewError && <div className="mt-2 text-sm text-red-700"><p>Review records unavailable: {reviewError}</p><button className="mt-2 rounded border border-red-300 px-3 py-1 font-medium text-red-800 hover:bg-red-100" onClick={() => { setReviewError(null); loadReviewDataset() }} type="button">Retry review data</button></div>}
              {!reviewError && !selectedReview && <p className="mt-2 text-sm text-slate-600">{selectedCandidateId ? 'No human-review record is available for this candidate.' : 'Select a candidate connection on the map to inspect its review record.'}</p>}
              {selectedReview && (
                <div className="mt-3 space-y-3 text-sm">
                  <dl className="space-y-2">
                    <div className="flex justify-between gap-3"><dt className="text-slate-600">Human review</dt><dd className="font-semibold">{selectedReview.review_state === 'unreviewed' ? 'unreviewed' : selectedReview.classification}</dd></div>
                  </dl>
                  {selectedReview.reviewer_decision && <p><span className="font-medium">Decision: </span>{selectedReview.reviewer_decision}</p>}
                  {selectedReview.supporting_evidence.length > 0 && <div><p className="font-medium">Supporting evidence</p><ul className="mt-1 list-disc space-y-1 pl-5">{selectedReview.supporting_evidence.map((item, index) => <li key={`${item.reference}-${index}`}>{item.evidence_type}: {item.description} <span className="break-all text-slate-500">({item.reference})</span></li>)}</ul></div>}
                  {selectedReview.limitations.length > 0 && <div><p className="font-medium">Limitations</p><ul className="mt-1 list-disc space-y-1 pl-5">{selectedReview.limitations.map((item) => <li key={item}>{item}</li>)}</ul></div>}
                  <p className="text-xs text-slate-500">Reviewer: {selectedReview.reviewer_id ?? 'not recorded'} · Reviewed: {selectedReview.reviewed_at ?? 'not recorded'}</p>
                  <p className="rounded bg-blue-50 p-2 text-xs text-blue-900">Review annotations are evidence records only. They do not approve repairs or modify the road graph.</p>
                </div>
              )}
            </div>
          </aside>
        </div></>}
        {activeView === 'roadmap' && (
          <div className="mt-6 grid gap-6 lg:grid-cols-[2fr_1fr]">
            <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
              <div className="relative h-[680px]">
                <MapContainer center={[7.225, 80.225]} zoom={10} className="h-full w-full">
                  <TileLayer attribution="&copy; OpenStreetMap contributors" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
                  <FitSelectedDistricts boundaries={boundaries} selected={selectedDistricts} />
                  <ViewportRoads onBounds={loadRoads} retryKey={roadRetry} />
                  {mask && <GeoJSON data={mask as GeoJsonObject} interactive={false} style={{ color: 'transparent', weight: 0, fillColor: '#f1f5f9', fillOpacity: 0.35 }} />}
                  {selectedBoundaryData && <GeoJSON data={selectedBoundaryData} style={(feature) =>
                    feature?.properties?.boundary_type === 'province'
                      ? { color: '#0f172a', weight: 2.5, fill: false }
                      : { color: '#2563eb', weight: 1.5, fill: false, dashArray: '5 3' }
                  } />}
                  {roadData && <GeoJSON data={roadData} style={(feature) => {
                    const hw = feature?.properties?.highway as string | undefined
                    return {
                      color: highwayColor(hw),
                      weight: !hw ? 1 : hw.startsWith('motorway') || hw.startsWith('trunk') ? 3.5 : hw.startsWith('primary') ? 2.5 : hw.startsWith('secondary') ? 2 : 1.5,
                      opacity: 0.9,
                    }
                  }} />}
                </MapContainer>
                {loadingRoads && selectedDistricts.length > 0 && <p className="absolute left-4 top-4 z-[1000] rounded bg-white px-3 py-2 text-xs font-medium shadow">Loading road network…</p>}
                {roadError && <div className="absolute bottom-4 left-4 right-4 z-[1000] rounded bg-red-50 px-3 py-2 text-xs text-red-800"><p>{roadError}</p><button className="mt-1 rounded border border-red-300 px-2 py-1 font-medium hover:bg-red-100" onClick={() => { setLoadingRoads(true); setRoadError(null); setRoadRetry((n) => n + 1) }} type="button">Retry</button></div>}
              </div>
              <div className="border-t border-slate-200 px-5 py-4">
                <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500">Road classification</p>
                <div className="flex flex-wrap gap-x-5 gap-y-2">
                  {[
                    { label: 'Motorway / Trunk', color: '#dc2626' },
                    { label: 'Primary', color: '#1d4ed8' },
                    { label: 'Secondary', color: '#7c3aed' },
                    { label: 'Tertiary', color: '#059669' },
                    { label: 'Residential', color: '#6b7280' },
                    { label: 'Service / Other', color: '#9ca3af' },
                  ].map(({ label, color }) => (
                    <span className="flex items-center gap-1.5 text-xs text-slate-700" key={label}>
                      <span className="inline-block h-1.5 w-6 rounded-full" style={{ backgroundColor: color }} />
                      {label}
                    </span>
                  ))}
                </div>
              </div>
            </div>
            <aside className="space-y-6">
              <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                <h2 className="font-semibold text-slate-900">Road types in viewport</h2>
                <p className="mt-1 text-sm text-slate-500">Segments visible in the current map view.</p>
                {!highwayBreakdown && <p className="mt-3 text-sm text-slate-500">Pan or zoom the map to load road data.</p>}
                {highwayBreakdown && highwayBreakdown.length === 0 && <p className="mt-3 text-sm text-slate-500">No roads in current view.</p>}
                {highwayBreakdown && highwayBreakdown.length > 0 && (
                  <ul className="mt-4 space-y-2">
                    {highwayBreakdown.slice(0, 12).map(([type, count]) => (
                      <li className="flex items-center gap-2" key={type}>
                        <span className="inline-block h-3 w-3 flex-shrink-0 rounded-sm" style={{ backgroundColor: highwayColor(type) }} />
                        <span className="flex-1 text-sm text-slate-700">{type}</span>
                        <span className="font-mono text-xs font-semibold text-slate-900">{count.toLocaleString()}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                <h2 className="font-semibold text-slate-900">Pipeline Stages</h2>
                <p className="mt-1 text-sm text-slate-500">Component 2 processing steps.</p>
                {!pipelineStages && <p className="mt-3 text-sm text-slate-400">Loading…</p>}
                {pipelineStages && (
                  <ul className="mt-3 space-y-2">
                    {pipelineStages.map((s) => {
                      const label: Record<string, string> = {
                        extraction: 'OSM Data Extraction',
                        cleaning_validation: 'Road Cleaning & Validation',
                        direction_normalization: 'Direction Normalization',
                        topology_candidates: 'Topology Candidate Detection',
                        topology_repair: 'Topology Repair',
                        evaluation: 'Evaluation Reports',
                        export: 'Graph Export',
                      }
                      const dot = s.status === 'completed' ? 'bg-green-500' : s.status === 'partial' ? 'bg-amber-400' : s.status === 'failed' ? 'bg-red-500' : 'bg-slate-300'
                      return (
                        <li className="flex items-start gap-2" key={s.stage_id}>
                          <span className={`mt-1.5 inline-block h-2.5 w-2.5 flex-shrink-0 rounded-full ${dot}`} />
                          <span className="text-sm text-slate-800">{label[s.stage_id] ?? s.stage_id}</span>
                        </li>
                      )
                    })}
                  </ul>
                )}
                <div className="mt-3 flex flex-wrap gap-3 text-xs text-slate-500">
                  <span className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-full bg-green-500" />Completed</span>
                  <span className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-full bg-amber-400" />Partial</span>
                  <span className="flex items-center gap-1"><span className="inline-block h-2 w-2 rounded-full bg-slate-300" />Unavailable</span>
                </div>
              </div>
              <div className="rounded-2xl border border-slate-200 bg-slate-50 p-5">
                <h2 className="font-semibold text-slate-900">Network Refinement</h2>
                <p className="mt-1 text-sm text-slate-500">Raw OSM vs topology-refined output.</p>
                {graphComparison && (() => {
                  const raw = graphComparison.graphs.raw?.metrics
                  const final = graphComparison.graphs.topology_repaired?.metrics ?? graphComparison.graphs.direction_normalized?.metrics
                  if (!raw || !final) return <p className="mt-3 text-sm text-slate-400">Metrics unavailable.</p>
                  return (
                    <div className="mt-3 space-y-3">
                      {[
                        { label: 'Nodes', raw: raw.node_count, out: final.node_count },
                        { label: 'Directed edges', raw: raw.directed_edge_count, out: final.directed_edge_count },
                        { label: 'Degree-1 nodes', raw: raw.degree_1_node_count, out: final.degree_1_node_count },
                        { label: 'Weak components', raw: raw.weakly_connected_component_count, out: final.weakly_connected_component_count },
                      ].map(({ label, raw: r, out: o }) => (
                        <div key={label}>
                          <p className="text-xs font-medium text-slate-500">{label}</p>
                          <div className="mt-1 flex justify-between text-sm">
                            <span className="text-slate-500">Raw: <span className="font-mono font-semibold text-slate-700">{r?.toLocaleString() ?? '—'}</span></span>
                            <span className="text-slate-500">Final: <span className="font-mono font-semibold text-green-700">{o?.toLocaleString() ?? '—'}</span></span>
                          </div>
                        </div>
                      ))}
                      <div className="mt-1 border-t border-slate-200 pt-3">
                        <div className="flex justify-between text-sm">
                          <span className="text-slate-600">Topology candidates</span>
                          <span className="font-semibold text-slate-900">{summary?.candidate_count?.toLocaleString() ?? '—'}</span>
                        </div>
                        <div className="flex justify-between text-sm">
                          <span className="text-slate-600">Accepted repairs</span>
                          <span className="font-semibold text-slate-900">{summary?.repairs.accepted?.toLocaleString() ?? '—'}</span>
                        </div>
                      </div>
                    </div>
                  )
                })()}
                {!graphComparison && <p className="mt-3 text-sm text-slate-400">Loading…</p>}
              </div>
              {metrics && !loadingMetrics && (
                <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                  <h2 className="font-semibold text-slate-900">Selected-area totals</h2>
                  <p className="mt-1 text-sm text-slate-500">{selectedDistricts.length ? selectedDistricts.join(' + ') : 'No districts selected'}</p>
                  <dl className="mt-3 space-y-2 text-sm">
                    <div className="flex justify-between"><dt className="text-slate-600">Nodes</dt><dd className="font-semibold">{metrics.selected_area.node_count?.toLocaleString() ?? '—'}</dd></div>
                    <div className="flex justify-between"><dt className="text-slate-600">Road segments</dt><dd className="font-semibold">{metrics.selected_area.induced_directed_edge_count?.toLocaleString() ?? '—'}</dd></div>
                    <div className="flex justify-between"><dt className="text-slate-600">Weak components</dt><dd className="font-semibold">{metrics.selected_area.weakly_connected_component_count?.toLocaleString() ?? '—'}</dd></div>
                  </dl>
                </div>
              )}
            </aside>
          </div>
        )}
      </section>
    </main>
  )
}

export default App
