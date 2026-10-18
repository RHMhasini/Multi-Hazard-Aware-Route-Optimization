/**
 * Frontend logic for Component 3 Research UI
 * Manages Leaflet map layers, live route calculation, charts, and table updates.
 */

// Global State
let map = null;
let edgeLayer = null;
let hazardLayer = null;
let routeLayers = {};
let markerLayers = [];
let paretoChartInstance = null;
let scenarioChartInstance = null;
let runtimeChartInstance = null;

let currentGeojson = null;
let currentBaselines = [];
let currentPareto = [];

// Color palette mapping
const ROUTE_STYLES = {
  "recommended_compromise": { color: "#10b981", weight: 6, opacity: 0.95, dashArray: null },
  "shortest_time": { color: "#3b82f6", weight: 4.5, opacity: 0.85, dashArray: null },
  "risk_minimal": { color: "#06b6d4", weight: 4.5, opacity: 0.85, dashArray: "6, 4" },
  "shortest_distance": { color: "#f43f5e", weight: 4, opacity: 0.75, dashArray: "4, 4" },
  "weighted_sum_alpha_75": { color: "#8b5cf6", weight: 3.5, opacity: 0.7, dashArray: null },
};

// Preset scenarios
const OD_PRESETS = {
  "ratnapura_default": {
    origin: [80.3620, 6.7020],
    dest: [80.4550, 6.6450],
    label: "Ratnapura General Hospital -> Kahawatta Camp"
  },
  "north_south": {
    origin: [80.3600, 6.7200],
    dest: [80.3600, 6.6400],
    label: "North Valley -> South Mountain Relief"
  },
  "diagonal_crossing": {
    origin: [80.3600, 6.6400],
    dest: [80.4500, 6.7200],
    label: "East-West Hazard Hotspot Traverse"
  }
};

// Initialize Application
document.addEventListener("DOMContentLoaded", async () => {
  setupTabs();
  setupControls();
  initMap();
  await loadEnvironment();
  await fetchDemoRoutes();
  await loadExperimentAnalytics();
});

// Tab Switcher
function setupTabs() {
  const tabBtns = document.querySelectorAll(".tab-btn");
  const tabPanes = document.querySelectorAll(".tab-pane");

  tabBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      tabBtns.forEach(b => b.classList.remove("active"));
      tabPanes.forEach(p => p.classList.remove("active"));

      btn.classList.add("active");
      const targetId = btn.getAttribute("data-tab");
      const targetPane = document.getElementById(targetId);
      if (targetPane) targetPane.classList.add("active");

      // Invalidate map size when tab becomes visible
      if (targetId === "tab-map" && map) {
        setTimeout(() => map.invalidateSize(), 200);
      }
    });
  });
}

// Sliders and Input Controls
function setupControls() {
  const sliderMaxHazard = document.getElementById("sliderMaxHazard");
  const labelMaxHazard = document.getElementById("labelMaxHazard");
  sliderMaxHazard.addEventListener("input", (e) => {
    labelMaxHazard.textContent = parseFloat(e.target.value).toFixed(2);
  });

  const sliderDetourRatio = document.getElementById("sliderDetourRatio");
  const labelDetourRatio = document.getElementById("labelDetourRatio");
  sliderDetourRatio.addEventListener("input", (e) => {
    labelDetourRatio.textContent = parseFloat(e.target.value).toFixed(2) + "x";
  });

  const sliderExclusion = document.getElementById("sliderExclusion");
  const labelExclusion = document.getElementById("labelExclusion");
  sliderExclusion.addEventListener("input", (e) => {
    labelExclusion.textContent = parseFloat(e.target.value).toFixed(2);
  });

  document.getElementById("btnRunOptimization").addEventListener("click", () => {
    triggerLiveOptimization();
  });

  // Layer Toggles
  document.getElementById("toggleRec").addEventListener("change", (e) => toggleRouteLayer("recommended_compromise", e.target.checked));
  document.getElementById("toggleTime").addEventListener("change", (e) => toggleRouteLayer("shortest_time", e.target.checked));
  document.getElementById("toggleRisk").addEventListener("change", (e) => toggleRouteLayer("risk_minimal", e.target.checked));
  document.getElementById("toggleDist").addEventListener("change", (e) => toggleRouteLayer("shortest_distance", e.target.checked));
  
  document.getElementById("toggleHazardRaster").addEventListener("change", (e) => {
    if (hazardLayer) {
      if (e.target.checked) map.addLayer(hazardLayer);
      else map.removeLayer(hazardLayer);
    }
  });
}

// Leaflet Map Initialization
function initMap() {
  // Center roughly at Ratnapura, Sri Lanka (6.68N, 80.40E)
  map = L.map("leafletMap", {
    zoomControl: true,
    attributionControl: false
  }).setView([6.678, 80.405], 13);

  // CartoDB Dark Matter tile basemap for slick high-contrast dark theme
  L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
    subdomains: "abcd",
    maxZoom: 19
  }).addTo(map);
}

// Fetch Road Network and Hazard Raster Cells
async function loadEnvironment() {
  try {
    const res = await fetch("/api/environment");
    const data = await res.json();

    // 1. Render Hazard Surface Heatmap Polygons
    if (data.hazard_grid_geojson) {
      hazardLayer = L.geoJSON(data.hazard_grid_geojson, {
        style: (feature) => {
          const h = feature.properties.hazard || 0;
          let fill = "#10b981"; // green low
          if (h > 0.70) fill = "#ef4444"; // red critical
          else if (h > 0.45) fill = "#f59e0b"; // amber high
          else if (h > 0.25) fill = "#06b6d4"; // cyan moderate
          return {
            fillColor: fill,
            fillOpacity: 0.28,
            color: fill,
            weight: 0.5,
            opacity: 0.4
          };
        },
        onEachFeature: (feature, layer) => {
          layer.bindTooltip(`Hazard Level: ${feature.properties.hazard}`, { sticky: true });
        }
      }).addTo(map);
    }

    // 2. Render Road Network Directed Edges
    if (data.edges_geojson) {
      edgeLayer = L.geoJSON(data.edges_geojson, {
        style: (feature) => {
          const h = feature.properties.hazard_score || 0;
          let col = "#4b5563";
          if (h >= 0.75) col = "#f87171"; // high risk road
          return {
            color: col,
            weight: 2,
            opacity: 0.6
          };
        },
        onEachFeature: (feature, layer) => {
          const p = feature.properties;
          layer.bindPopup(`
            <div style="font-size:12px; font-family:sans-serif; color:#111;">
              <strong>Road Segment</strong><br>
              From: ${p.source} &rarr; ${p.target}<br>
              Hazard Score: <b>${p.hazard_score}</b><br>
              Length: ${p.length_m} m<br>
              Travel Time: ${p.travel_time_s} s
            </div>
          `);
        }
      }).addTo(map);
    }

  } catch (err) {
    console.error("Failed to load environment:", err);
  }
}

// Fetch Pre-Computed Demo GeoJSON
async function fetchDemoRoutes() {
  try {
    const res = await fetch("/api/demo-routes");
    const geojson = await res.json();
    renderGeoJsonFeatures(geojson);

    // Initial default values from demo.py run
    document.getElementById("auditDecisionsContainer").innerHTML = `
      <strong>Decision Audit:</strong> Selected <code>sweep_alpha_75</code>.<br>
      Achieves <b>68.5% risk reduction</b> over fastest route with only <b>5.4% time detour</b>.<br>
      Ceiling Hazard &le; 0.70 satisfied (Max edge hazard: 0.288).
    `;

    // Populate initial baselines table from demo
    renderBaselinesTable([
      { route_name: "shortest_distance", travel_time_sec: 1077.32, travel_time_min: 17.96, distance_km: 11.88, cumulative_exposure: 6724.17, max_edge_hazard: 0.928, time_detour_pct: 17.7, edge_count: 10 },
      { route_name: "shortest_time", travel_time_sec: 915.45, travel_time_min: 15.26, distance_km: 12.01, cumulative_exposure: 4315.38, max_edge_hazard: 0.667, time_detour_pct: 0.0, edge_count: 10 },
      { route_name: "risk_minimal", travel_time_sec: 1041.27, travel_time_min: 17.35, distance_km: 12.03, cumulative_exposure: 558.82, max_edge_hazard: 0.071, time_detour_pct: 13.7, edge_count: 10 },
      { route_name: "weighted_sum_alpha_75", travel_time_sec: 964.90, travel_time_min: 16.08, distance_km: 12.15, cumulative_exposure: 1360.31, max_edge_hazard: 0.288, time_detour_pct: 5.4, edge_count: 10, is_recommended: true }
    ]);

    // Initial Pareto Points
    const initialPareto = [
      { algorithm: "sweep_alpha_95", travel_time_min: 15.26, hazard_exposure: 4315.38, distance_km: 12.01, max_hazard: 0.667, is_recommended: false },
      { algorithm: "label_setting", travel_time_min: 15.38, hazard_exposure: 3746.21, distance_km: 12.02, max_hazard: 0.667, is_recommended: false },
      { algorithm: "label_setting", travel_time_min: 15.96, hazard_exposure: 1929.48, distance_km: 12.14, max_hazard: 0.403, is_recommended: false },
      { algorithm: "sweep_alpha_75", travel_time_min: 16.08, hazard_exposure: 1360.31, distance_km: 12.15, max_hazard: 0.288, is_recommended: true },
      { algorithm: "sweep_alpha_60", travel_time_min: 16.86, hazard_exposure: 742.16, distance_km: 12.11, max_hazard: 0.095, is_recommended: false },
      { algorithm: "sweep_alpha_00", travel_time_min: 17.35, hazard_exposure: 558.82, distance_km: 12.03, max_hazard: 0.071, is_recommended: false }
    ];
    renderParetoChartAndTable(initialPareto);

    // Initial Resilience
    renderResilienceTable([
      { threshold: 0.90, initial_edges: 111, active_edges: 103, severed_edges: 8, severed_pct: 7.21, strongly_connected_components: 2, weakly_connected_components: 2 },
      { threshold: 0.80, initial_edges: 111, active_edges: 96, severed_edges: 15, severed_pct: 13.51, strongly_connected_components: 4, weakly_connected_components: 3 },
      { threshold: 0.75, initial_edges: 111, active_edges: 92, severed_edges: 19, severed_pct: 17.12, strongly_connected_components: 5, weakly_connected_components: 5 },
      { threshold: 0.60, initial_edges: 111, active_edges: 84, severed_edges: 27, severed_pct: 24.32, strongly_connected_components: 8, weakly_connected_components: 8 }
    ]);

  } catch (err) {
    console.error("Error fetching demo routes:", err);
  }
}

// Render GeoJSON Route Features on Leaflet
function renderGeoJsonFeatures(geojson) {
  currentGeojson = geojson;

  // Clear existing route layers
  Object.values(routeLayers).forEach(layer => map.removeLayer(layer));
  routeLayers = {};
  markerLayers.forEach(m => map.removeLayer(m));
  markerLayers = [];

  let bounds = L.latLngBounds([]);

  geojson.features.forEach(feature => {
    const geom = feature.geometry;
    const props = feature.properties || {};

    // 1. Point Markers (Origin / Destination)
    if (geom.type === "Point") {
      const coord = [geom.coordinates[1], geom.coordinates[0]];
      const isOrigin = props.marker_type === "origin";
      
      const icon = L.divIcon({
        className: 'custom-map-pin',
        html: `<div style="
          width: 22px; height: 22px; 
          background: ${isOrigin ? '#3b82f6' : '#ef4444'}; 
          border: 2px solid #fff; 
          border-radius: 50%; 
          box-shadow: 0 0 10px rgba(0,0,0,0.6);
          display: flex; align-items: center; justify-content: center;
          color: #fff; font-size: 11px; font-weight: bold;
        ">${isOrigin ? 'O' : 'D'}</div>`,
        iconSize: [22, 22],
        iconAnchor: [11, 11]
      });

      const marker = L.marker(coord, { icon }).addTo(map);
      marker.bindPopup(`<b>${props.label || 'Point'}</b><br>Coords: [${coord[0].toFixed(4)}, ${coord[1].toFixed(4)}]`);
      markerLayers.push(marker);
      bounds.extend(coord);
    }

    // 2. LineString Routes
    if (geom.type === "LineString") {
      const latlngs = geom.coordinates.map(c => [c[1], c[0]]);
      latlngs.forEach(ll => bounds.extend(ll));

      const routeId = props.route_id || props.algorithm || "route";
      const isRec = props.is_recommended;
      
      let style = ROUTE_STYLES[routeId] || {
        color: isRec ? "#10b981" : "#a855f7",
        weight: isRec ? 6 : 4,
        opacity: 0.85
      };

      const line = L.polyline(latlngs, {
        color: style.color,
        weight: style.weight,
        opacity: style.opacity,
        dashArray: style.dashArray
      }).addTo(map);

      line.bindPopup(`
        <div style="font-size:12px; font-family:sans-serif; color:#111;">
          <h4 style="margin:0 0 4px 0; color:${style.color};">${props.algorithm || routeId} ${isRec ? '(RECOMMENDED)' : ''}</h4>
          Travel Time: <b>${(props.travel_time_sec / 60).toFixed(2)} min</b><br>
          Distance: <b>${(props.distance_m / 1000).toFixed(2)} km</b><br>
          Hazard Exposure: <b>${Math.round(props.hazard_exposure || 0)}</b><br>
          Max Edge Hazard: <b>${(props.max_hazard || 0).toFixed(3)}</b>
        </div>
      `);

      routeLayers[routeId] = line;
    }
  });

  if (bounds.isValid()) {
    map.fitBounds(bounds, { padding: [40, 40] });
  }
}

// Toggle individual route layers
function toggleRouteLayer(key, isVisible) {
  // Find matching key in routeLayers
  for (const [id, layer] of Object.entries(routeLayers)) {
    if (id.includes(key) || key.includes(id)) {
      if (isVisible) map.addLayer(layer);
      else map.removeLayer(layer);
    }
  }
}

// Live Route Optimization Trigger
async function triggerLiveOptimization() {
  const btn = document.getElementById("btnRunOptimization");
  const origText = btn.innerHTML;
  btn.innerHTML = `<span>Calculating Bi-Criteria Solution...</span>`;
  btn.disabled = true;

  try {
    const preset = document.getElementById("presetRouteSelect").value;
    const odData = OD_PRESETS[preset] || OD_PRESETS["ratnapura_default"];

    const payload = {
      origin_lon: odData.origin[0],
      origin_lat: odData.origin[1],
      dest_lon: odData.dest[0],
      dest_lat: odData.dest[1],
      max_hazard_threshold: parseFloat(document.getElementById("sliderMaxHazard").value),
      max_detour_ratio: parseFloat(document.getElementById("sliderDetourRatio").value),
      hazard_exclusion_threshold: parseFloat(document.getElementById("sliderExclusion").value),
    };

    const res = await fetch("/api/optimize-route", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    if (!res.ok) {
      throw new Error(await res.text());
    }

    const data = await res.json();

    // 1. Update Map
    renderGeoJsonFeatures(data.geojson);

    // 2. Update KPI metrics
    const rec = data.recommendation;
    document.getElementById("kpiRecName").textContent = rec.algorithm;
    document.getElementById("kpiRecAlgorithm").textContent = `${rec.travel_time_min} min | ${rec.distance_km} km`;
    
    // Compute exposure reduction vs quickest route
    const quickest = data.baselines_table.find(r => r.route_name === "shortest_time");
    if (quickest && quickest.cumulative_exposure > 0) {
      const red = ((quickest.cumulative_exposure - rec.cumulative_exposure) / quickest.cumulative_exposure) * 100;
      document.getElementById("kpiRiskRed").textContent = Math.max(0, red).toFixed(1);
      
      const detour = ((rec.travel_time_sec - quickest.travel_time_sec) / quickest.travel_time_sec) * 100;
      document.getElementById("kpiDetourPct").textContent = (detour >= 0 ? "+" : "") + detour.toFixed(1);
    }

    document.getElementById("kpiResilience").textContent = data.post_exclusion_reachable ? "Reachable" : "Isolated";

    // 3. Update Audit Notes
    document.getElementById("auditDecisionsContainer").innerHTML = `
      <strong>Decision Audit:</strong><br>
      ${rec.audit_notes.replace(/\n/g, '<br>')}
    `;

    // 4. Update Tables and Charts
    renderBaselinesTable(data.baselines_table);
    renderParetoChartAndTable(data.pareto_points);
    renderResilienceTable(data.resilience_table);

  } catch (err) {
    alert("Optimization failed: " + err.message);
    console.error(err);
  } finally {
    btn.innerHTML = origText;
    btn.disabled = false;
  }
}

// Render Comparison Baselines Table
function renderBaselinesTable(baselines) {
  const tbody = document.getElementById("baselinesTableBody");
  tbody.innerHTML = "";

  baselines.forEach(row => {
    const tr = document.createElement("tr");
    if (row.is_recommended || row.route_name.includes("weighted_sum_alpha_75")) {
      tr.classList.add("highlight-row");
    }

    const detourVal = row.time_detour_pct !== undefined ? `${row.time_detour_pct.toFixed(1)}%` : "-";
    const nameFormatted = row.route_name.replace(/_/g, " ");

    tr.innerHTML = `
      <td>
        <span style="font-weight:600; text-transform:capitalize;">${nameFormatted}</span>
        ${tr.classList.contains("highlight-row") ? '<span class="badge-rec">KNEE-POINT</span>' : ''}
      </td>
      <td><b>${(row.travel_time_sec / 60).toFixed(2)} min</b> (${Math.round(row.travel_time_sec)}s)</td>
      <td>${(row.distance_km || (row.distance_m ? row.distance_m / 1000 : 0)).toFixed(2)} km</td>
      <td>${Math.round(row.cumulative_exposure || row.cumulative_exposure_haz_m || 0)}</td>
      <td>${(row.max_edge_hazard || 0).toFixed(3)}</td>
      <td>${detourVal}</td>
      <td>${row.edge_count || 10}</td>
    `;
    tbody.appendChild(tr);
  });
}

// Render Pareto Scatter Chart and Data Table
function renderParetoChartAndTable(paretoPoints) {
  // Update Table
  const tbody = document.getElementById("paretoTableBody");
  tbody.innerHTML = "";
  document.getElementById("paretoCountBadge").textContent = `${paretoPoints.length} Non-Dominated Solutions`;

  paretoPoints.forEach(p => {
    const tr = document.createElement("tr");
    if (p.is_recommended) tr.classList.add("highlight-row");

    tr.innerHTML = `
      <td>
        ${p.algorithm}
        ${p.is_recommended ? '<span class="badge-rec">OPTIMAL</span>' : ''}
      </td>
      <td><b>${p.travel_time_min.toFixed(2)}</b></td>
      <td>${Math.round(p.hazard_exposure)}</td>
      <td>${p.max_hazard.toFixed(3)}</td>
      <td>${p.distance_km.toFixed(2)} km</td>
    `;
    tbody.appendChild(tr);
  });

  // Update Pareto Scatter Plot
  const ctx = document.getElementById("paretoChart").getContext("2d");
  if (paretoChartInstance) {
    paretoChartInstance.destroy();
  }

  const standardPoints = paretoPoints
    .filter(p => !p.is_recommended)
    .map(p => ({ x: p.travel_time_min, y: p.hazard_exposure, label: p.algorithm }));

  const recPoint = paretoPoints
    .filter(p => p.is_recommended)
    .map(p => ({ x: p.travel_time_min, y: p.hazard_exposure, label: "Recommended Knee-Point" }));

  paretoChartInstance = new Chart(ctx, {
    type: 'scatter',
    data: {
      datasets: [
        {
          label: 'Pareto-Optimal Frontiers',
          data: standardPoints,
          backgroundColor: '#3b82f6',
          borderColor: '#60a5fa',
          pointRadius: 6,
          pointHoverRadius: 9
        },
        {
          label: 'Recommended Compromise (Knee-Point)',
          data: recPoint,
          backgroundColor: '#10b981',
          borderColor: '#34d399',
          pointRadius: 10,
          pointHoverRadius: 13,
          pointStyle: 'star'
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: '#9ca3af' } },
        tooltip: {
          callbacks: {
            label: (ctx) => `Time: ${ctx.parsed.x} min | Risk: ${ctx.parsed.y} H*m (${ctx.raw.label})`
          }
        }
      },
      scales: {
        x: {
          title: { display: true, text: 'Travel Time (minutes)', color: '#9ca3af' },
          grid: { color: 'rgba(255, 255, 255, 0.06)' },
          ticks: { color: '#9ca3af' }
        },
        y: {
          title: { display: true, text: 'Cumulative Hazard Exposure (hazard x m)', color: '#9ca3af' },
          grid: { color: 'rgba(255, 255, 255, 0.06)' },
          ticks: { color: '#9ca3af' }
        }
      }
    }
  });
}

// Render Resilience Assessment Table
function renderResilienceTable(resilience) {
  const tbody = document.getElementById("resilienceTableBody");
  tbody.innerHTML = "";

  resilience.forEach(row => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><b>&ge; ${row.threshold.toFixed(2)}</b></td>
      <td>${row.initial_edges}</td>
      <td>${row.active_edges}</td>
      <td style="color:#f87171;">-${row.severed_edges}</td>
      <td><b>${row.severed_pct.toFixed(1)}%</b></td>
      <td>${row.strongly_connected_components}</td>
      <td>${row.weakly_connected_components}</td>
    `;
    tbody.appendChild(tr);
  });
}

// Load Monte Carlo Research Analytics
async function loadExperimentAnalytics() {
  try {
    const res = await fetch("/api/experiment-summary");
    const summary = await res.json();
    if (!summary.by_scenario || summary.by_scenario.length === 0) return;

    // 1. Scenario Comparison Bar Chart
    const ctxScen = document.getElementById("scenarioChart").getContext("2d");
    const labels = summary.by_scenario.map(s => s.scenario.replace(/_/g, " ").toUpperCase());
    const reachData = summary.by_scenario.map(s => s.reachable_pct);
    const exposureRedData = summary.by_scenario.map(s => s.avg_exposure_reduction_pct);

    scenarioChartInstance = new Chart(ctxScen, {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [
          {
            label: 'Post-Closure Reachability (%)',
            data: reachData,
            backgroundColor: '#06b6d4',
            borderRadius: 4
          },
          {
            label: 'Avg Hazard Exposure Reduction (%)',
            data: exposureRedData,
            backgroundColor: '#10b981',
            borderRadius: 4
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { labels: { color: '#9ca3af' } } },
        scales: {
          x: { grid: { color: 'rgba(255, 255, 255, 0.06)' }, ticks: { color: '#9ca3af' } },
          y: { grid: { color: 'rgba(255, 255, 255, 0.06)' }, ticks: { color: '#9ca3af' }, max: 100 }
        }
      }
    });

    // 2. Runtime Benchmarks Bar Chart
    const ctxRuntime = document.getElementById("runtimeChart").getContext("2d");
    runtimeChartInstance = new Chart(ctxRuntime, {
      type: 'bar',
      data: {
        labels: ['Sweep Weighted-Sum (21 steps)', 'Bi-Criteria Label-Setting'],
        datasets: [{
          label: 'Mean Algorithmic Runtime (ms)',
          data: [summary.avg_runtime_sweep_ms, summary.avg_runtime_ls_ms],
          backgroundColor: ['#3b82f6', '#8b5cf6'],
          borderRadius: 6
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { labels: { color: '#9ca3af' } } },
        scales: {
          x: { grid: { color: 'rgba(255, 255, 255, 0.06)' }, ticks: { color: '#9ca3af' } },
          y: { 
            title: { display: true, text: 'Execution Time (milliseconds)', color: '#9ca3af' },
            grid: { color: 'rgba(255, 255, 255, 0.06)' }, 
            ticks: { color: '#9ca3af' } 
          }
        }
      }
    });

  } catch (err) {
    console.error("Failed to load experiment analytics:", err);
  }
}
