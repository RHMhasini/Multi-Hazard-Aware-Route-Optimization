import { useEffect, useState } from 'react'
import type { Feature, FeatureCollection } from 'geojson'
import 'leaflet/dist/leaflet.css'
import { NetworkGraph } from './NetworkGraph'

type Summary = {
  graph: Record<string, number>
  candidate_count: number
  repairs: { accepted: number; skipped: number; approval_count: number }
  study_area: string
  candidate_status_counts: Record<string, number>
}

type BoundaryFeature = Feature & { properties?: { boundary_type?: string; district?: string; name?: string } }
type BoundaryCollection = FeatureCollection & { features: BoundaryFeature[] }

function provinceMask(boundaries: BoundaryCollection | null): Feature | null {
  const province = boundaries?.features.find((f) => f.properties?.boundary_type === 'province')
  const geometry = province?.geometry
  if (!geometry || (geometry.type !== 'Polygon' && geometry.type !== 'MultiPolygon')) return null
  const polygons = geometry.type === 'Polygon' ? [geometry.coordinates] : geometry.coordinates
  if (polygons.some((p) => p.length === 0 || p[0].length < 4)) return null
  const exteriorRings = polygons.map((p) => p[0])
  const interiorRings = polygons.flatMap((p) => p.slice(1))
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
  const offline = 'API unreachable — start it with: .venv\\Scripts\\python.exe scripts\\c2_api.py'
  let response: Response
  try { response = await fetch(path) } catch { throw new Error(offline) }
  const body = await response.text()
  let payload: unknown
  try { payload = JSON.parse(body) } catch {
    throw new Error(`Non-JSON response for ${path} (${response.status}): ${body.slice(0, 80)}`)
  }
  if (!response.ok) {
    const err = payload as { error?: string; message?: string }
    throw new Error(`HTTP ${response.status} for ${path}: ${err.error ?? err.message ?? 'error'}`)
  }
  return payload as T
}

function App() {
  const [summary, setSummary] = useState<Summary | null>(null)
  const [boundaries, setBoundaries] = useState<BoundaryCollection | null>(null)
  const [apiError, setApiError] = useState<string | null>(null)

  useEffect(() => {
    getJson<Summary>('/api/summary')
      .then(setSummary)
      .catch((e: unknown) => setApiError(e instanceof Error ? e.message : 'API error'))
    getJson<BoundaryCollection>('/api/boundaries')
      .then(setBoundaries)
      .catch(() => setBoundaries(null))
  }, [])

  const mask = provinceMask(boundaries)

  return (
    <main className="flex min-h-screen flex-col bg-slate-100">
      <header className="border-b border-slate-200 bg-white px-6 py-4 shadow-sm">
        <div className="mx-auto flex max-w-[1600px] items-baseline gap-4">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-blue-700">Component 2</p>
            <h1 className="text-xl font-bold text-slate-950">Sabaragamuwa Province — Road Network Graph</h1>
          </div>
          <p className="hidden text-sm text-slate-500 sm:block">
            OSM-derived directed graph · {summary ? `${Number(summary.graph.nodes ?? 0).toLocaleString()} nodes · ${Number(summary.graph.directed_edges ?? 0).toLocaleString()} edges` : 'loading…'}
          </p>
        </div>
      </header>

      {apiError && (
        <div className="mx-auto mt-4 w-full max-w-[1600px] px-6">
          <div className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-800">{apiError}</div>
        </div>
      )}

      <section className="flex-1 px-4 py-4 sm:px-6">
        <div className="mx-auto max-w-[1600px]">
          <NetworkGraph summary={summary} boundaries={boundaries} mask={mask} />
        </div>
      </section>
    </main>
  )
}

export default App
