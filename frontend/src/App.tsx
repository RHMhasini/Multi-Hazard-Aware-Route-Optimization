import { useCallback, useEffect, useState } from 'react'
import { MapContainer, TileLayer, GeoJSON, useMapEvents } from 'react-leaflet'
import type { Feature, FeatureCollection, GeoJsonObject } from 'geojson'
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

const API_BASE = import.meta.env.VITE_API_BASE ?? ''
const initialBounds = '80.20,7.20,80.25,7.25'

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`)
  if (!response.ok) {
    const error = (await response.json().catch(() => ({}))) as { error?: string }
    throw new Error(error.error ?? `Request failed (${response.status})`)
  }
  return response.json() as Promise<T>
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

  useEffect(() => {
    Promise.all([
      getJson<Summary>('/api/summary'),
      getJson<FeatureCollection>('/api/candidates'),
    ]).then(([nextSummary, nextCandidates]) => {
      setSummary(nextSummary)
      setCandidates(nextCandidates)
    }).catch((reason: unknown) => {
      setError(reason instanceof Error ? reason.message : 'Could not load research data')
    })
  }, [])

  const loadRoads = useCallback((bbox: string) => {
    setLoadingRoads(true)
    setRoadError(null)
    getJson<FeatureCollection>(`/api/roads?bbox=${encodeURIComponent(bbox)}`)
      .then(setRoads)
      .catch((reason: unknown) => {
        setRoadError(reason instanceof Error ? reason.message : 'Could not load roads')
        setRoads(null)
      })
      .finally(() => setLoadingRoads(false))
  }, [])

  useEffect(() => {
    loadRoads(initialBounds)
  }, [loadRoads])

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
                <ViewportRoads onBounds={loadRoads} />
                {roadData && <GeoJSON data={roadData} style={{ color: '#2563eb', weight: 1.5, opacity: 0.7 }} />}
                {candidateData && <GeoJSON data={candidateData} style={candidateStyle} onEachFeature={onEachCandidate} />}
              </MapContainer>
              {loadingRoads && <p className="absolute left-4 top-4 z-[1000] rounded bg-white px-3 py-2 text-xs font-medium shadow">Loading bounded road view…</p>}
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
